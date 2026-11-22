"""Tests for ExecutionPipeline, truthful milestone emissions, and security guarantees."""

import glob
import json
import os
import subprocess
import sys
import tempfile
import unittest

from engine.models import (
    ExecutionRequest,
    LanguageId,
    MAX_SOURCE_BYTES,
    Milestone,
    ResourceLimits,
    RunnerResult,
    SeccompPolicyProfile,
)
from engine.pipeline import ExecutionPipeline
from engine.registry import RuntimeRegistry
from engine.runner import MockSandboxRunner, NativeSandboxRunner


class ArtifactCreatingMockRunner(MockSandboxRunner):
    """Simulates realistic compiler behavior by producing output artifact on compile success."""

    def run(self, workspace, target_bin, target_argv, limits, stdin_content="", **kwargs):
        res = super().run(workspace, target_bin, target_argv, limits, stdin_content, **kwargs)
        # If compilation succeeded, create the expected output binary
        if res.status == "OK" and res.exit_code == 0:
            if target_bin.endswith("gcc") or target_bin.endswith("clang++") or target_bin.endswith("g++"):
                out_path = os.path.join(workspace, "a.out")
                with open(out_path, "w") as f:
                    f.write("#!/bin/sh\n")
                os.chmod(out_path, 0o755)
            elif target_bin.endswith("javac"):
                out_path = os.path.join(workspace, "Solution.class")
                with open(out_path, "wb") as f:
                    f.write(b"\xca\xfe\xba\xbe")
            elif any("tsc" in str(arg) for arg in target_argv) or target_bin.endswith("tsc"):
                out_path = os.path.join(workspace, "Solution.js")
                with open(out_path, "w") as f:
                    f.write('"use strict";\n')
            elif any("csc" in str(arg) for arg in target_argv) or target_bin.endswith("dotnet"):
                out_path = os.path.join(workspace, "Solution.dll")
                with open(out_path, "wb") as f:
                    f.write(b"MZ\x90\x00")
        return res


class TestExecutionPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = RuntimeRegistry()
        self.runner = ArtifactCreatingMockRunner()
        self.pipeline = ExecutionPipeline(self.registry, self.runner)

    def test_c_success_milestones(self) -> None:
        # C compile success, then execution success
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 150, 100, 32768, "", "")
        )
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 20, 10, 8192, "hello from c\n", "")
        )

        request = ExecutionRequest(
            request_id="req-c-1",
            language_id=LanguageId.C,
            source_code="#include <stdio.h>\nint main() { printf(\"hello from c\\n\"); return 0; }\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.stdout, "hello from c\n")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.COMPILING,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertEqual(len(self.runner.invocations), 2)
        # Stage 1: compiler bin
        self.assertTrue(self.runner.invocations[0]["target_bin"].endswith("gcc"))
        self.assertEqual(self.runner.invocations[0]["seccomp_profile"], SeccompPolicyProfile.COMPILER)
        # Stage 2: artifact bin
        self.assertEqual(self.runner.invocations[1]["target_bin"], "/workspace/a.out")
        self.assertEqual(self.runner.invocations[1]["seccomp_profile"], SeccompPolicyProfile.STRICT_NATIVE)

    def test_c_compile_success_without_artifact_fails_closed(self) -> None:
        # Compiler exits 0 with status OK, but produces no artifact
        bare_runner = MockSandboxRunner()
        bare_runner.queue_result(
            RunnerResult("OK", 0, 0, 80, 50, 16384, "", "")
        )
        pipeline = ExecutionPipeline(self.registry, bare_runner)

        request = ExecutionRequest(
            request_id="req-c-no-art",
            language_id=LanguageId.C,
            source_code="int main() { return 0; }\n",
        )
        res = pipeline.execute(request)

        # Must fail closed as COMPILE_ERROR
        self.assertEqual(res.final_status, Milestone.COMPILE_ERROR)
        self.assertIn("produced no artifact", res.compile_output or "")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.COMPILING,
            Milestone.COMPILE_ERROR,
        ]
        self.assertEqual(milestone_names, expected)

        # Execution stage must NEVER have started
        self.assertNotIn(Milestone.RUNNING, milestone_names)
        self.assertEqual(len(bare_runner.invocations), 1)

    def test_c_compile_error_truthful_and_fail_closed(self) -> None:
        # Compiler fails
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 80, 70, 24576, "", "error: expected ';' before '}' token\n")
        )

        request = ExecutionRequest(
            request_id="req-c-fail",
            language_id=LanguageId.C,
            source_code="int main() { return 0 }\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPILE_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("expected ';'", res.compile_output or "")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.COMPILING,
            Milestone.COMPILE_ERROR,
        ]
        self.assertEqual(milestone_names, expected)

        # Critical: RUNNING must NEVER be emitted upon compilation failure
        self.assertNotIn(Milestone.RUNNING, milestone_names)
        self.assertEqual(len(self.runner.invocations), 1)

    def test_stale_artifact_defense(self) -> None:
        # Pre-plant a fake artifact before compile
        class StaleArtifactPlantingRunner(MockSandboxRunner):
            def run(self, workspace, target_bin, target_argv, limits, stdin_content="", **kwargs):
                # Simulate a malicious or leftover a.out artifact in workspace
                fake_bin = os.path.join(workspace, "a.out")
                with open(fake_bin, "w") as f:
                    f.write("echo 'malicious stale execution'\n")
                os.chmod(fake_bin, 0o755)
                # But compile fails!
                return super().run(workspace, target_bin, target_argv, limits, stdin_content, **kwargs)

        planting_runner = StaleArtifactPlantingRunner()
        planting_runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 50, 40, 16384, "", "compile failed\n")
        )
        pipeline = ExecutionPipeline(self.registry, planting_runner)

        request = ExecutionRequest(
            request_id="req-stale-def",
            language_id=LanguageId.C,
            source_code="broken code;\n",
        )
        res = pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPILE_ERROR)
        # Verify execution stage was aborted and stale artifact was never executed
        milestone_names = [m.milestone for m in res.milestones]
        self.assertNotIn(Milestone.RUNNING, milestone_names)
        self.assertEqual(len(planting_runner.invocations), 1)

    def test_python3_truthful_milestones_no_compiling(self) -> None:
        # Python 3 execution success
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 30, 25, 12288, "hello python\n", "")
        )

        request = ExecutionRequest(
            request_id="req-py-1",
            language_id=LanguageId.PYTHON3,
            source_code="print('hello python')\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.stdout, "hello python\n")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)

        # Truthful emission: Python must NEVER emit COMPILING
        self.assertNotIn(Milestone.COMPILING, milestone_names)
        self.assertEqual(len(self.runner.invocations), 1)
        self.assertTrue(self.runner.invocations[0]["target_bin"].endswith("python3"))
        self.assertEqual(self.runner.invocations[0]["seccomp_profile"], SeccompPolicyProfile.PYTHON)

    def test_compile_stage_timeout_truthful_status(self) -> None:
        self.runner.queue_result(
            RunnerResult("TIMEOUT", 124, 9, 5000, 5000, 32768, "", "Compiler exceeded timeout\n")
        )
        request = ExecutionRequest(
            request_id="req-compile-timeout",
            language_id=LanguageId.C,
            source_code="#include <stdio.h>\nint main() { return 0; }\n",
        )
        res = self.pipeline.execute(request)

        # Must truthfully propagate TIMEOUT, not conflate as COMPILE_ERROR
        self.assertEqual(res.final_status, Milestone.TIMEOUT)
        milestone_names = [m.milestone for m in res.milestones]
        self.assertEqual(milestone_names, [Milestone.ACCEPTED, Milestone.COMPILING, Milestone.TIMEOUT])
        self.assertNotIn(Milestone.RUNNING, milestone_names)

    def test_compile_stage_memory_limit_truthful_status(self) -> None:
        self.runner.queue_result(
            RunnerResult("MEMORY_LIMIT", 137, 9, 200, 150, 262144, "", "Compiler killed by cgroup OOM\n")
        )
        request = ExecutionRequest(
            request_id="req-compile-oom",
            language_id=LanguageId.C,
            source_code="#include <stdio.h>\nint main() { return 0; }\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.MEMORY_LIMIT)
        milestone_names = [m.milestone for m in res.milestones]
        self.assertEqual(milestone_names, [Milestone.ACCEPTED, Milestone.COMPILING, Milestone.MEMORY_LIMIT])
        self.assertNotIn(Milestone.RUNNING, milestone_names)

    def test_compile_stage_policy_block_truthful_status(self) -> None:
        self.runner.queue_result(
            RunnerResult("BLOCKED_BY_POLICY", 159, 31, 50, 20, 16384, "", "Syscall blocked by seccomp\n")
        )
        request = ExecutionRequest(
            request_id="req-compile-policy",
            language_id=LanguageId.C,
            source_code="#include <stdio.h>\nint main() { return 0; }\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.BLOCKED_BY_POLICY)
        milestone_names = [m.milestone for m in res.milestones]
        self.assertEqual(milestone_names, [Milestone.ACCEPTED, Milestone.COMPILING, Milestone.BLOCKED_BY_POLICY])
        self.assertNotIn(Milestone.RUNNING, milestone_names)

    def test_python3_runtime_error(self) -> None:
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 35, 30, 14336, "", "ZeroDivisionError: division by zero\n")
        )

        request = ExecutionRequest(
            request_id="req-py-err",
            language_id=LanguageId.PYTHON3,
            source_code="x = 1 / 0\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("ZeroDivisionError", res.stderr)

        milestone_names = [m.milestone for m in res.milestones]
        self.assertEqual(milestone_names, [Milestone.ACCEPTED, Milestone.RUNNING, Milestone.RUNTIME_ERROR])

    def test_python3_timeout(self) -> None:
        self.runner.queue_result(
            RunnerResult("TIMEOUT", 124, 9, 2000, 1000, 16384, "", "Execution exceeded wall-clock limit\n")
        )

        request = ExecutionRequest(
            request_id="req-py-timeout",
            language_id=LanguageId.PYTHON3,
            source_code="while True: pass\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.TIMEOUT)
        self.assertEqual(res.exit_code, 124)
        self.assertEqual(res.signal, 9)

    def test_python3_memory_limit(self) -> None:
        self.runner.queue_result(
            RunnerResult("MEMORY_LIMIT", 137, 9, 150, 120, 65536, "", "Process killed by cgroup OOM killer\n")
        )

        request = ExecutionRequest(
            request_id="req-py-mem",
            language_id=LanguageId.PYTHON3,
            source_code="a = ['x'] * 100000000\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.MEMORY_LIMIT)
        self.assertEqual(res.signal, 9)

    def test_oversized_source_rejected_before_workspace(self) -> None:
        oversized = "a" * (MAX_SOURCE_BYTES + 10)
        request = ExecutionRequest(
            request_id="req-oversized",
            language_id=LanguageId.PYTHON3,
            source_code=oversized,
        )
        initial_workspaces = glob.glob("/tmp/pes2-work-*")
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.SANDBOX_ERROR)
        self.assertIn("exceeds maximum allowed limit", res.stderr)
        self.assertEqual(len(self.runner.invocations), 0)
        # Ensure zero host temporary directories were provisioned
        after_workspaces = glob.glob("/tmp/pes2-work-*")
        self.assertEqual(len(initial_workspaces), len(after_workspaces))

    def test_unsupported_max_output_bytes_rejected(self) -> None:
        request = ExecutionRequest(
            request_id="req-big-output",
            language_id=LanguageId.PYTHON3,
            source_code="print('hi')\n",
            custom_limits=ResourceLimits(max_output_bytes=70000),
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.SANDBOX_ERROR)
        self.assertIn("exceeds runner cap", res.stderr)
        self.assertEqual(len(self.runner.invocations), 0)

    def test_custom_max_output_bytes_software_truncation(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 10, 5, 4096, "0123456789abcdefghij", "")
        )
        request = ExecutionRequest(
            request_id="req-cap-test",
            language_id=LanguageId.PYTHON3,
            source_code="print('0123456789abcdefghij')\n",
            custom_limits=ResourceLimits(max_output_bytes=10),
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.OUTPUT_LIMIT)
        self.assertEqual(res.stdout, "0123456789")
        self.assertEqual(len(res.stdout.encode("utf-8")), 10)

    def test_ephemeral_workspace_cleanup(self) -> None:
        initial_workspaces = glob.glob("/tmp/pes2-work-*")

        self.runner.queue_result(RunnerResult("OK", 0, 0, 10, 5, 4096, "ok\n", ""))
        request = ExecutionRequest(
            request_id="req-clean",
            language_id=LanguageId.PYTHON3,
            source_code="print('ok')\n",
        )
        self.pipeline.execute(request)

        after_workspaces = glob.glob("/tmp/pes2-work-*")
        # Ensure no ephemeral workspace directories were leaked
        self.assertEqual(len(initial_workspaces), len(after_workspaces))

    def test_unsupported_language_fails_safely(self) -> None:
        from enum import Enum
        class DummyLang(str, Enum):
            UNREGISTERED = "unregistered"

        request = ExecutionRequest(
            request_id="req-unknown",
            language_id=DummyLang.UNREGISTERED,  # type: ignore
            source_code="SELECT 1;\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.SANDBOX_ERROR)
        self.assertIn("not found in registry", res.stderr)

    def test_bash_success_milestones(self) -> None:
        # Bash stage 1 syntax check success, stage 2 execution success
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 10, 5, 1024, "", "")
        )
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 20, 10, 2048, "hello from bash\n", "")
        )

        request = ExecutionRequest(
            request_id="req-bash-1",
            language_id=LanguageId.BASH,
            source_code="echo 'hello from bash'\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.stdout, "hello from bash\n")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.COMPILING,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertEqual(len(self.runner.invocations), 2)
        # Stage 1: syntax check
        self.assertEqual(self.runner.invocations[0]["seccomp_profile"], SeccompPolicyProfile.BASH)
        self.assertIn("-n", self.runner.invocations[0]["target_argv"])
        # Stage 2: execution
        self.assertEqual(self.runner.invocations[1]["seccomp_profile"], SeccompPolicyProfile.BASH)
        self.assertIn("/workspace/solution.sh", self.runner.invocations[1]["target_argv"])

    def test_bash_syntax_error_emits_compile_error(self) -> None:
        # Syntax check returns exit 2 with diagnostic
        syntax_err_msg = "/workspace/solution.sh: line 1: syntax error near unexpected token `then'"
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 2, 0, 10, 5, 1024, "", syntax_err_msg)
        )

        request = ExecutionRequest(
            request_id="req-bash-bad-syntax",
            language_id=LanguageId.BASH,
            source_code="if then\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPILE_ERROR)
        self.assertEqual(res.exit_code, 2)
        self.assertIn("syntax error near unexpected token `then'", res.compile_output or "")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.COMPILING,
            Milestone.COMPILE_ERROR,
        ]
        self.assertEqual(milestone_names, expected)
        # Stage 2 MUST NOT have run
        self.assertEqual(len(self.runner.invocations), 1)

    def test_bash_runtime_error(self) -> None:
        # Syntax check passes, script exits with runtime error
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 10, 5, 1024, "", "")
        )
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 20, 10, 2048, "", "exit 1")
        )

        request = ExecutionRequest(
            request_id="req-bash-rt-err",
            language_id=LanguageId.BASH,
            source_code="exit 1\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(res.exit_code, 1)

    def test_bash_timeout(self) -> None:
        # Syntax check passes, script times out
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 10, 5, 1024, "", "")
        )
        self.runner.queue_result(
            RunnerResult("TIMEOUT", 124, 9, 2000, 100, 2048, "", "")
        )

        request = ExecutionRequest(
            request_id="req-bash-timeout",
            language_id=LanguageId.BASH,
            source_code="sleep 10\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.TIMEOUT)
        self.assertEqual(res.exit_code, 124)

    def test_uninstalled_go_rust_fail_safely(self) -> None:
        # Requesting Go
        go_req = ExecutionRequest(
            request_id="req-go",
            language_id=LanguageId.GO,
            source_code="package main\nfunc main() {}\n",
        )
        go_res = self.pipeline.execute(go_req)
        self.assertEqual(go_res.final_status, Milestone.SANDBOX_ERROR)
        self.assertIn("Toolchain for 'go' is not installed", go_res.stderr)

        # Requesting Rust
        rust_req = ExecutionRequest(
            request_id="req-rust",
            language_id=LanguageId.RUST,
            source_code="fn main() {}\n",
        )
        rust_res = self.pipeline.execute(rust_req)
        self.assertEqual(rust_res.final_status, Milestone.SANDBOX_ERROR)
        self.assertIn("Toolchain for 'rust' is not installed", rust_res.stderr)

    def test_java_success_milestones(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 300, 250, 65536, "", "")
        )
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 80, 50, 32768, "hello from java\n", "")
        )

        request = ExecutionRequest(
            request_id="req-java-1",
            language_id=LanguageId.JAVA,
            source_code="public class Solution { public static void main(String[] args) { System.out.println(\"hello from java\"); } }\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.stdout, "hello from java\n")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.COMPILING,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertEqual(len(self.runner.invocations), 2)
        # Stage 1: javac compiler
        self.assertTrue(self.runner.invocations[0]["target_bin"].endswith("javac"))
        self.assertEqual(self.runner.invocations[0]["seccomp_profile"], SeccompPolicyProfile.JVM)
        # Stage 2: java runtime
        self.assertTrue(self.runner.invocations[1]["target_bin"].endswith("java"))
        self.assertEqual(self.runner.invocations[1]["seccomp_profile"], SeccompPolicyProfile.JVM)
        self.assertIn("Solution", self.runner.invocations[1]["target_argv"])

    def test_java_compile_error(self) -> None:
        compile_err_msg = "/workspace/Solution.java:1: error: ';' expected"
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 150, 100, 49152, "", compile_err_msg)
        )

        request = ExecutionRequest(
            request_id="req-java-err",
            language_id=LanguageId.JAVA,
            source_code="public class Solution { int x }\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPILE_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("error: ';' expected", res.compile_output or "")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.COMPILING,
            Milestone.COMPILE_ERROR,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertEqual(len(self.runner.invocations), 1)

    def test_uninstalled_kotlin_scala_fail_safely(self) -> None:
        # Requesting Kotlin
        kt_req = ExecutionRequest(
            request_id="req-kt",
            language_id=LanguageId.KOTLIN,
            source_code="fun main() {}\n",
        )
        kt_res = self.pipeline.execute(kt_req)
        self.assertEqual(kt_res.final_status, Milestone.SANDBOX_ERROR)
        self.assertIn("Toolchain for 'kotlin' is not installed", kt_res.stderr)

        # Requesting Scala
        sc_req = ExecutionRequest(
            request_id="req-sc",
            language_id=LanguageId.SCALA,
            source_code="@main def main(): Unit = ()\n",
        )
        sc_res = self.pipeline.execute(sc_req)
        self.assertEqual(sc_res.final_status, Milestone.SANDBOX_ERROR)
        self.assertIn("Toolchain for 'scala' is not installed", sc_res.stderr)

    def test_dynamic_jvm_memory_baseline_admission(self) -> None:
        adapter = self.registry.get(LanguageId.JAVA)
        self.assertIsNotNone(adapter)
        assert adapter is not None

        # Case 1: Custom limits with memory smaller than JVM baseline (64 MiB requested)
        tight_limits = ResourceLimits(memory_kb=65536)
        args, limits = adapter.resolve_execution_limits(tight_limits)
        # Must be elevated to at least 192 MiB to avoid immediate VM startup abort
        self.assertGreaterEqual(limits.memory_kb, 196608)
        # Heap must be dynamically sized to match available space above metaspace reserve
        self.assertTrue(any(a.startswith("-Xmx") for a in args))
        xmx_arg = [a for a in args if a.startswith("-Xmx")][0]
        self.assertEqual(xmx_arg, "-Xmx64m")

        # Case 2: Custom limits with high memory (512 MiB requested)
        high_limits = ResourceLimits(memory_kb=524288)
        args_high, limits_high = adapter.resolve_execution_limits(high_limits)
        self.assertEqual(limits_high.memory_kb, 524288)
        xmx_high = [a for a in args_high if a.startswith("-Xmx")][0]
        # (524288 - 131072) // 1024 = 384m
        self.assertEqual(xmx_high, "-Xmx384m")

    def test_live_java_execution_with_native_runner(self) -> None:
        import subprocess
        from engine.runner import NativeSandboxRunner

        if not os.path.exists("sandbox/pes2-runner"):
            self.skipTest("sandbox/pes2-runner not built")
        try:
            res = subprocess.run(["unshare", "-U", "true"], capture_output=True)
            if res.returncode != 0:
                self.skipTest("User namespaces restricted in current environment")
        except FileNotFoundError:
            self.skipTest("unshare binary not found")

        cgroup_root = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice"
        if not os.path.exists(cgroup_root):
            self.skipTest("Delegated cgroup root not found")

        runner = NativeSandboxRunner(runner_path="sandbox/pes2-runner", cgroup_root=cgroup_root)
        pipeline = ExecutionPipeline(self.registry, runner)

        # Normal Java execution
        req = ExecutionRequest(
            request_id="req-live-java-ok",
            language_id=LanguageId.JAVA,
            source_code="public class Solution { public static void main(String[] args) { System.out.println(\"live java sandboxed\"); } }\n",
        )
        res = pipeline.execute(req)
        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.stdout.strip(), "live java sandboxed")

        # Evil network Java execution
        evil_req = ExecutionRequest(
            request_id="req-live-java-evil",
            language_id=LanguageId.JAVA,
            source_code="public class Solution { public static void main(String[] args) throws Exception { new java.net.Socket(\"1.1.1.1\", 80); } }\n",
        )
        evil_res = pipeline.execute(evil_req)
        self.assertEqual(evil_res.final_status, Milestone.BLOCKED_BY_POLICY)
        self.assertEqual(evil_res.signal, 31)

        # Verify zero cgroup leftovers
        leftovers = [
            d for d in os.listdir(cgroup_root)
            if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
        ]
        self.assertEqual(len(leftovers), 0)

        # Multi-class, nested classes, and lambdas validation under directory classpath handoff
        nested_src = (
            "public class Solution {\n"
            "    static class StaticNested {\n"
            "        String greet() { return \"nested-static\"; }\n"
            "    }\n"
            "    class Inner {\n"
            "        String greet() { return \"inner\"; }\n"
            "    }\n"
            "    public static void main(String[] args) {\n"
            "        Runnable lambda = () -> System.out.println(\"lambda-executed\");\n"
            "        System.out.println(new StaticNested().greet());\n"
            "        System.out.println(new Solution().new Inner().greet());\n"
            "        lambda.run();\n"
            "    }\n"
            "}\n"
        )
        nested_req = ExecutionRequest(
            request_id="req-live-java-nested",
            language_id=LanguageId.JAVA,
            source_code=nested_src,
        )
        nested_res = pipeline.execute(nested_req)
        self.assertEqual(nested_res.final_status, Milestone.COMPLETED)
        self.assertEqual(nested_res.exit_code, 0)
        self.assertIn("nested-static", nested_res.stdout)
        self.assertIn("inner", nested_res.stdout)
        self.assertIn("lambda-executed", nested_res.stdout)

    def test_ruby_truthful_milestones_no_compiling(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 35, 20, 16384, "hello ruby\n", "")
        )
        request = ExecutionRequest(
            request_id="req-rb-1",
            language_id=LanguageId.RUBY,
            source_code="puts 'hello ruby'\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.stdout, "hello ruby\n")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)
        # Truthful emission: Direct interpreted Ruby must NEVER emit COMPILING
        self.assertNotIn(Milestone.COMPILING, milestone_names)
        self.assertEqual(len(self.runner.invocations), 1)
        self.assertTrue(self.runner.invocations[0]["target_bin"].endswith("ruby"))
        self.assertEqual(self.runner.invocations[0]["seccomp_profile"], SeccompPolicyProfile.RUBY)
        self.assertEqual(self.runner.invocations[0]["target_argv"], ["/workspace/Solution.rb"])

    def test_ruby_syntax_or_runtime_error_milestone(self) -> None:
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 25, 15, 12288, "", "Solution.rb:1: syntax error\n")
        )
        request = ExecutionRequest(
            request_id="req-rb-err",
            language_id=LanguageId.RUBY,
            source_code="def broken(\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("syntax error", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.RUNTIME_ERROR,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)

    def test_javascript_truthful_milestones_no_compiling(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 30, 20, 49152, "hello javascript\n", "")
        )
        request = ExecutionRequest(
            request_id="req-js-ok",
            language_id=LanguageId.JAVASCRIPT,
            source_code="console.log('hello javascript');\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.stdout, "hello javascript\n")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)
        self.assertEqual(len(self.runner.invocations), 1)
        inv = self.runner.invocations[0]
        self.assertEqual(inv["target_bin"], "/usr/bin/node")
        self.assertEqual(inv["seccomp_profile"], SeccompPolicyProfile.NODE)

    def test_javascript_syntax_or_runtime_error_milestone(self) -> None:
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 25, 15, 49152, "", "SyntaxError: Unexpected token\n")
        )
        request = ExecutionRequest(
            request_id="req-js-err",
            language_id=LanguageId.JAVASCRIPT,
            source_code="function broken( {\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("SyntaxError", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.RUNTIME_ERROR,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)

    def test_php_truthful_milestones_no_compiling(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 20, 15, 24576, "hello php\n", "")
        )
        request = ExecutionRequest(
            request_id="req-php-ok",
            language_id=LanguageId.PHP,
            source_code="<?php echo 'hello php\n';\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.stdout, "hello php\n")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)
        self.assertEqual(len(self.runner.invocations), 1)
        inv = self.runner.invocations[0]
        self.assertEqual(inv["target_bin"], "/usr/bin/php")
        self.assertEqual(inv["seccomp_profile"], SeccompPolicyProfile.PHP)

    def test_php_syntax_or_runtime_error_milestone(self) -> None:
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 255, 0, 15, 10, 24576, "", "PHP Parse error: syntax error\n")
        )
        request = ExecutionRequest(
            request_id="req-php-err",
            language_id=LanguageId.PHP,
            source_code="<?php function broken( {\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(res.exit_code, 255)
        self.assertIn("syntax error", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.RUNTIME_ERROR,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)

    def test_dart_truthful_milestones_no_compiling(self) -> None:
        dart_adapter = self.registry.get(LanguageId.DART)
        if not dart_adapter or not dart_adapter.is_installed:
            self.skipTest("Dart toolchain not installed on host or /opt/dart-sdk inaccessible")
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 500, 480, 122880, "hello dart\n", "")
        )
        request = ExecutionRequest(
            request_id="req-dart-ok",
            language_id=LanguageId.DART,
            source_code="void main() { print('hello dart'); }\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.stdout, "hello dart\n")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)
        self.assertEqual(len(self.runner.invocations), 1)
        inv = self.runner.invocations[0]
        self.assertEqual(inv["target_bin"], "/usr/bin/dart")
        self.assertEqual(inv["seccomp_profile"], SeccompPolicyProfile.DART)

    def test_dart_syntax_or_runtime_error_milestone(self) -> None:
        dart_adapter = self.registry.get(LanguageId.DART)
        if not dart_adapter or not dart_adapter.is_installed:
            self.skipTest("Dart toolchain not installed on host or /opt/dart-sdk inaccessible")
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 254, 0, 400, 390, 118784, "", "Error: Can't find ')' to match '('\n")
        )
        request = ExecutionRequest(
            request_id="req-dart-err",
            language_id=LanguageId.DART,
            source_code="void main( {\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(res.exit_code, 254)
        self.assertIn("Error:", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.RUNTIME_ERROR,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)

    def test_racket_truthful_milestones_no_compiling(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 500, 480, 140000, "hello racket\n", "")
        )
        request = ExecutionRequest(
            request_id="req-racket-ok",
            language_id=LanguageId.RACKET,
            source_code="#lang racket\n(displayln \"hello racket\")\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.stdout, "hello racket\n")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)
        self.assertEqual(len(self.runner.invocations), 1)
        inv = self.runner.invocations[0]
        self.assertEqual(inv["target_bin"], "/usr/bin/racket")
        self.assertEqual(inv["seccomp_profile"], SeccompPolicyProfile.RACKET)

    def test_racket_syntax_or_runtime_error_milestone(self) -> None:
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 400, 390, 138000, "", "racket: error: test error\n")
        )
        request = ExecutionRequest(
            request_id="req-racket-err",
            language_id=LanguageId.RACKET,
            source_code="#lang racket\n(error 'test \"test error\")\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("error:", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.RUNTIME_ERROR,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)

    def test_erlang_truthful_milestones_no_compiling(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 300, 250, 60000, "hello erlang\n", "")
        )
        request = ExecutionRequest(
            request_id="req-erlang-ok",
            language_id=LanguageId.ERLANG,
            source_code="-module(solution).\n-export([start/0]).\nstart() -> io:format(\"hello erlang~n\").\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.stdout, "hello erlang\n")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)
        self.assertEqual(len(self.runner.invocations), 1)
        inv = self.runner.invocations[0]
        self.assertEqual(inv["target_bin"], "/usr/bin/erl")
        self.assertEqual(inv["seccomp_profile"], SeccompPolicyProfile.ERLANG)

    def test_erlang_syntax_or_runtime_error_milestone(self) -> None:
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 300, 250, 60000, "", "** exception error:badarith\n")
        )
        request = ExecutionRequest(
            request_id="req-erlang-err",
            language_id=LanguageId.ERLANG,
            source_code="-module(solution).\n-export([start/0]).\nstart() -> 1 / 0.\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("badarith", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.RUNTIME_ERROR,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)

    def test_elixir_truthful_milestones_no_compiling(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 500, 480, 65536, "hello elixir\n", "")
        )
        request = ExecutionRequest(
            request_id="req-elixir-ok",
            language_id=LanguageId.ELIXIR,
            source_code='IO.puts("hello elixir")\n',
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.stdout, "hello elixir\n")

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)
        self.assertEqual(len(self.runner.invocations), 1)
        inv = self.runner.invocations[0]
        self.assertEqual(inv["target_bin"], "/usr/bin/elixir")
        self.assertEqual(inv["seccomp_profile"], SeccompPolicyProfile.ELIXIR)

    def test_elixir_syntax_or_runtime_error_milestone(self) -> None:
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 500, 480, 65536, "", "** (ArithmeticError) bad argument in arithmetic expression\n")
        )
        request = ExecutionRequest(
            request_id="req-elixir-err",
            language_id=LanguageId.ELIXIR,
            source_code="defmodule Solution do\n  def start do\n    1 / 0\n  end\nend\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("ArithmeticError", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.RUNTIME_ERROR,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)

    def test_typescript_success_milestones(self) -> None:
        # Mock compilation and execution success
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 1500, 1400, 215000, "", "")
        )
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 30, 20, 16384, "hello typescript pipeline\n", "")
        )
        request = ExecutionRequest(
            request_id="req-ts-ok",
            language_id=LanguageId.TYPESCRIPT,
            source_code='const msg: string = "hello typescript pipeline"; console.log(msg);\n',
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.stdout, "hello typescript pipeline\n")
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.COMPILING,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertEqual(len(self.runner.invocations), 2)

        # Stage 1: Compilation invocation
        compile_inv = self.runner.invocations[0]
        self.assertTrue(compile_inv["target_bin"].endswith("env"))
        self.assertIn("NODE_DISABLE_COMPILE_CACHE=1", compile_inv["target_argv"])
        self.assertIn("/workspace/Solution.ts", compile_inv["target_argv"])
        self.assertEqual(compile_inv["seccomp_profile"], SeccompPolicyProfile.NODE)
        self.assertEqual(compile_inv["limits"].memory_kb, 262144)

        # Stage 2: Execution invocation
        exec_inv = self.runner.invocations[1]
        self.assertTrue(exec_inv["target_bin"].endswith("node"))
        self.assertIn("/workspace/Solution.js", exec_inv["target_argv"])
        self.assertEqual(exec_inv["seccomp_profile"], SeccompPolicyProfile.NODE)
        self.assertEqual(exec_inv["limits"].memory_kb, 131072)

    def test_typescript_compile_error_truthful_and_fail_closed(self) -> None:
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 2, 0, 1500, 1400, 215000, "", "Solution.ts:1:1 error TS1005: ';' expected.\n")
        )
        request = ExecutionRequest(
            request_id="req-ts-err",
            language_id=LanguageId.TYPESCRIPT,
            source_code="const broken: string = ;\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPILE_ERROR)
        self.assertEqual(res.exit_code, 2)
        self.assertIn("error TS1005", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.COMPILING,
            Milestone.COMPILE_ERROR,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.RUNNING, milestone_names)
        self.assertEqual(len(self.runner.invocations), 1)

    def test_typescript_runtime_error_milestone(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 1500, 1400, 215000, "", "")
        )
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 30, 20, 16384, "", "TypeError: obj.nonExistentMethod is not a function\n")
        )
        request = ExecutionRequest(
            request_id="req-ts-runtime-err",
            language_id=LanguageId.TYPESCRIPT,
            source_code="const obj: any = null; obj.nonExistentMethod();\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("TypeError", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.COMPILING,
            Milestone.RUNNING,
            Milestone.RUNTIME_ERROR,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertEqual(len(self.runner.invocations), 2)

    def test_csharp_success_milestones(self) -> None:
        # Mock compilation and execution success
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 1500, 1400, 105000, "", "")
        )
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 100, 80, 35000, "hello csharp pipeline\n", "")
        )
        request = ExecutionRequest(
            request_id="req-csharp-ok",
            language_id=LanguageId.CSHARP,
            source_code='using System; class Solution { static void Main() { Console.WriteLine("hello csharp pipeline"); } }\n',
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.stdout, "hello csharp pipeline\n")
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.COMPILING,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertEqual(len(self.runner.invocations), 2)

        # Stage 1: Compilation invocation
        compile_inv = self.runner.invocations[0]
        self.assertTrue(compile_inv["target_bin"].endswith("dotnet"))
        self.assertIn("-out:/workspace/Solution.dll", compile_inv["target_argv"])
        self.assertIn("/workspace/Solution.cs", compile_inv["target_argv"])
        self.assertEqual(compile_inv["seccomp_profile"], SeccompPolicyProfile.CSHARP)
        self.assertEqual(compile_inv["limits"].memory_kb, 262144)
        self.assertEqual(compile_inv["limits"].pids, 64)

        # Stage 2: Execution invocation
        exec_inv = self.runner.invocations[1]
        self.assertTrue(exec_inv["target_bin"].endswith("dotnet"))
        self.assertIn("--runtimeconfig", exec_inv["target_argv"])
        self.assertIn("/workspace/Solution.dll", exec_inv["target_argv"])
        self.assertEqual(exec_inv["seccomp_profile"], SeccompPolicyProfile.CSHARP)
        self.assertEqual(exec_inv["limits"].memory_kb, 131072)
        self.assertEqual(exec_inv["limits"].pids, 32)

    def test_csharp_compile_error_truthful_and_fail_closed(self) -> None:
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 1500, 1400, 105000, "", "Solution.cs(1,1): error CS1002: ; expected\n")
        )
        request = ExecutionRequest(
            request_id="req-csharp-compile-err",
            language_id=LanguageId.CSHARP,
            source_code="class Solution { broken syntax }\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPILE_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("error CS1002", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.COMPILING,
            Milestone.COMPILE_ERROR,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.RUNNING, milestone_names)
        self.assertEqual(len(self.runner.invocations), 1)

    def test_csharp_runtime_error_milestone(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 1500, 1400, 105000, "", "")
        )
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 100, 80, 35000, "", "Unhandled exception. System.DivideByZeroException: Attempted to divide by zero.\n")
        )
        request = ExecutionRequest(
            request_id="req-csharp-runtime-err",
            language_id=LanguageId.CSHARP,
            source_code="using System; class Solution { static void Main() { int a = 1, b = 0; int c = a / b; } }\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("DivideByZeroException", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.COMPILING,
            Milestone.RUNNING,
            Milestone.RUNTIME_ERROR,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertEqual(len(self.runner.invocations), 2)

    def test_csharp_execution_memory_limit_milestone(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 1500, 1400, 105000, "", "")
        )
        self.runner.queue_result(
            RunnerResult("MEMORY_LIMIT", 137, 9, 200, 150, 131072, "", "Process killed by cgroup OOM killer\n")
        )
        request = ExecutionRequest(
            request_id="req-csharp-oom",
            language_id=LanguageId.CSHARP,
            source_code="using System; class Solution { static void Main() {} }\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.MEMORY_LIMIT)
        self.assertEqual(res.signal, 9)
        self.assertGreaterEqual(res.peak_memory_kb, 131072)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.COMPILING,
            Milestone.RUNNING,
            Milestone.MEMORY_LIMIT,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertEqual(len(self.runner.invocations), 2)
        self.assertEqual(self.runner.invocations[1]["limits"].memory_kb, 131072)


    def test_unsupported_phase6_databases_fail_closed(self) -> None:
        unsupported = [
            LanguageId.POSTGRESQL,
            LanguageId.MYSQL,
            LanguageId.MSSQL,
            LanguageId.ORACLE,
        ]
        for lang_id in unsupported:
            req = ExecutionRequest(
                request_id=f"req-unsupported-db-{lang_id.value}",
                language_id=lang_id,
                source_code="SELECT 1;\n",
            )
            res = self.pipeline.execute(req)
            self.assertEqual(res.final_status, Milestone.SANDBOX_ERROR)
            self.assertIn("is not installed on this host", res.stderr)
            self.assertEqual(len(self.runner.invocations), 0)


    def test_sqlite_truthful_milestones_no_compiling(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 50, 30, 20000, '{"columns": ["id", "val"], "rows": [[1, "test"]]}\n', "")
        )
        request = ExecutionRequest(
            request_id="req-sqlite-ok",
            language_id=LanguageId.SQLITE,
            source_code="CREATE TABLE t (id INT, val TEXT); INSERT INTO t VALUES (1, 'test'); SELECT * FROM t;\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertIn("columns", res.stdout)
        self.assertIn("rows", res.stdout)

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)
        self.assertEqual(len(self.runner.invocations), 1)
        inv = self.runner.invocations[0]
        self.assertEqual(inv["target_bin"], "/usr/bin/python3")
        self.assertEqual(inv["seccomp_profile"], SeccompPolicyProfile.PYTHON)
        self.assertEqual(inv["limits"].wall_ms, 3000)
        self.assertEqual(inv["limits"].memory_kb, 65536)


    def test_sqlite_syntax_or_runtime_error_milestone(self) -> None:
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 50, 30, 20000, "", "SQLite error: near \"FRO\": syntax error\n")
        )
        request = ExecutionRequest(
            request_id="req-sqlite-err",
            language_id=LanguageId.SQLITE,
            source_code="SELECT * FRO t;\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("syntax error", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.RUNTIME_ERROR,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)


    def test_sqlite_timeout_milestone(self) -> None:
        self.runner.queue_result(
            RunnerResult("TIMEOUT", 124, 9, 3000, 2000, 30000, "", "Execution timed out\n")
        )
        request = ExecutionRequest(
            request_id="req-sqlite-timeout",
            language_id=LanguageId.SQLITE,
            source_code="WITH RECURSIVE cte(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cte) SELECT * FROM cte;\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.TIMEOUT)
        self.assertEqual(res.exit_code, 124)
        self.assertEqual(res.signal, 9)


    def test_sqlite_memory_limit_milestone(self) -> None:
        self.runner.queue_result(
            RunnerResult("MEMORY_LIMIT", 137, 9, 100, 80, 65536, "", "Process killed by cgroup OOM killer\n")
        )
        request = ExecutionRequest(
            request_id="req-sqlite-oom",
            language_id=LanguageId.SQLITE,
            source_code="SELECT 1;\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.MEMORY_LIMIT)
        self.assertEqual(res.exit_code, 137)
        self.assertEqual(res.signal, 9)


    def test_sqlite_harness_preserves_semicolons_in_strings_and_triggers(self) -> None:
        import subprocess, tempfile, json
        adapter = self.registry.get(LanguageId.SQLITE)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        harness_script = adapter.execution_spec.execution_args[2]

        sql = (
            "CREATE TABLE Items (id INT, description TEXT);\n"
            "CREATE TABLE Audit (item_id INT, log_msg TEXT);\n"
            "CREATE TRIGGER tr_items AFTER INSERT ON Items\n"
            "BEGIN\n"
            "    UPDATE Items SET description = 'modified; ' || new.description WHERE id = new.id;\n"
            "    INSERT INTO Audit VALUES (new.id, 'item;inserted;audit');\n"
            "END;\n"
            "INSERT INTO Items VALUES (1, 'initial; description; with; semicolons');\n"
            "SELECT id, description FROM Items;\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            sql_path = os.path.join(tmpdir, "solution.sql")
            with open(sql_path, "w", encoding="utf-8") as f:
                f.write(sql)
            res = subprocess.run(
                [sys.executable, "-I", "-c", harness_script, sql_path],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, f"Harness failed: {res.stderr}")
            parsed = json.loads(res.stdout)
            self.assertEqual(
                parsed,
                {
                    "columns": ["id", "description"],
                    "rows": [[1, "modified; initial; description; with; semicolons"]],
                },
            )


    def test_sqlite_harness_rejects_attach_and_detach(self) -> None:
        import subprocess, tempfile
        adapter = self.registry.get(LanguageId.SQLITE)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        harness_script = adapter.execution_spec.execution_args[2]

        with tempfile.TemporaryDirectory() as tmpdir:
            file1 = os.path.join(tmpdir, "evil1.db")
            file2 = os.path.join(tmpdir, "evil2.db")
            file3 = os.path.join(tmpdir, "evil3.db")
            file4 = os.path.join(tmpdir, "evil4.db")

            statements = [
                f"ATTACH '{file1}' AS evil1;\n",
                f"ATTACH\n  '{file2}'\n  AS evil2;\n",
                f"ATTACH/*comment*/'{file3}'/*comment*/AS evil3;\n",
                f"ATTACH -- comment\n '{file4}' AS evil4;\n",
                "DETACH evil;\n",
                "DETACH\n  evil;\n",
                "DETACH/*comment*/evil;\n",
                "DETACH -- comment\n evil;\n",
            ]
            for stmt in statements:
                sql_path = os.path.join(tmpdir, "test.sql")
                with open(sql_path, "w", encoding="utf-8") as f:
                    f.write(stmt)
                res = subprocess.run(
                    [sys.executable, "-I", "-c", harness_script, sql_path],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(res.returncode, 1, f"Expected denial for {stmt!r}, got: {res.stdout}")
                self.assertIn("not authorized", res.stderr)

            for fpath in (file1, file2, file3, file4):
                self.assertFalse(os.path.exists(fpath), f"File {fpath} must not be created")


    def test_sqlite_harness_rejects_vacuum_into_variations(self) -> None:
        import subprocess, tempfile
        adapter = self.registry.get(LanguageId.SQLITE)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        harness_script = adapter.execution_spec.execution_args[2]

        with tempfile.TemporaryDirectory() as tmpdir:
            f1 = os.path.join(tmpdir, "v1.db")
            f2 = os.path.join(tmpdir, "v2.db")
            f3 = os.path.join(tmpdir, "v3.db")
            f4 = os.path.join(tmpdir, "v4.db")
            f5 = os.path.join(tmpdir, "v5.db")

            variations = [
                f"CREATE TABLE t (id INT);\nVACUUM INTO '{f1}';\n",
                f"CREATE TABLE t (id INT);\nVACUUM\n  INTO\n  '{f2}';\n",
                f"CREATE TABLE t (id INT);\nVACUUM\n  main\n  INTO\n  '{f3}';\n",
                f"CREATE TABLE t (id INT);\nVACUUM/*comment*/INTO '{f4}';\n",
                f"CREATE TABLE t (id INT);\nVACUUM -- comment\n INTO '{f5}';\n",
            ]
            for var in variations:
                sql_path = os.path.join(tmpdir, "test.sql")
                with open(sql_path, "w", encoding="utf-8") as f:
                    f.write(var)
                res = subprocess.run(
                    [sys.executable, "-I", "-c", harness_script, sql_path],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(res.returncode, 1, f"Expected denial for {var!r}, got: {res.stdout}")
                self.assertIn("authorization denied: VACUUM INTO is disabled", res.stderr)

            for fpath in (f1, f2, f3, f4, f5):
                self.assertFalse(os.path.exists(fpath), f"VACUUM INTO created unexpected file: {fpath}")


    def test_sqlite_harness_rejects_file_affecting_pragmas(self) -> None:
        import subprocess, tempfile
        adapter = self.registry.get(LanguageId.SQLITE)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        harness_script = adapter.execution_spec.execution_args[2]

        with tempfile.TemporaryDirectory() as tmpdir:
            dir1 = os.path.join(tmpdir, "temp1")
            dir2 = os.path.join(tmpdir, "temp2")
            dir3 = os.path.join(tmpdir, "temp3")

            pragma_statements = [
                f"PRAGMA temp_store_directory = '{dir1}';\n",
                f"PRAGMA\n  temp_store_directory\n  = '{dir2}';\n",
                f"PRAGMA/*comment*/temp_store_directory = '{dir3}';\n",
                f"PRAGMA data_store_directory = '{tmpdir}';\n",
                "PRAGMA database_list;\n",
                "PRAGMA\n  database_list;\n",
                "PRAGMA/*comment*/database_list;\n",
                "PRAGMA journal_mode = WAL;\n",
                "PRAGMA\n  journal_mode\n  = WAL;\n",
                "PRAGMA/*comment*/journal_mode = WAL;\n",
                "PRAGMA auto_vacuum = FULL;\n",
                "PRAGMA mmap_size = 268435456;\n",
                "PRAGMA temp_store = FILE;\n",
            ]
            for stmt in pragma_statements:
                sql_path = os.path.join(tmpdir, "test.sql")
                with open(sql_path, "w", encoding="utf-8") as f:
                    f.write(stmt)
                res = subprocess.run(
                    [sys.executable, "-I", "-c", harness_script, sql_path],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(res.returncode, 1, f"Expected denial for PRAGMA {stmt!r}, got: {res.stdout}")
                self.assertIn("not authorized", res.stderr)

            for d in (dir1, dir2, dir3):
                self.assertFalse(os.path.exists(d), f"Directory {d} should not be created")


    def test_sqlite_harness_allows_normal_sql_and_safe_pragmas(self) -> None:
        import subprocess, tempfile, json
        adapter = self.registry.get(LanguageId.SQLITE)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        harness_script = adapter.execution_spec.execution_args[2]

        sql = (
            "CREATE TABLE Items (id INT PRIMARY KEY, name TEXT, qty INT);\n"
            "INSERT INTO Items VALUES (1, 'apple', 10), (2, 'banana', 20);\n"
            "UPDATE Items SET qty = 15 WHERE id = 1;\n"
            "DELETE FROM Items WHERE id = 2;\n"
            "WITH Stock AS (SELECT sum(qty) AS total_qty FROM Items) SELECT total_qty FROM Stock;\n"
            "CREATE TRIGGER tr_audit AFTER INSERT ON Items BEGIN UPDATE Items SET name = 'triggered' WHERE id = new.id; END;\n"
            "INSERT INTO Items VALUES (3, 'cherry', 30);\n"
            "CREATE VIEW v_items AS SELECT id, name FROM Items WHERE qty >= 15;\n"
            "SELECT * FROM v_items;\n"
            "PRAGMA table_info(Items);\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            sql_path = os.path.join(tmpdir, "test.sql")
            with open(sql_path, "w", encoding="utf-8") as f:
                f.write(sql)
            res = subprocess.run(
                [sys.executable, "-I", "-c", harness_script, sql_path],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, f"Harness failed on normal SQL: {res.stderr}")
            parsed = json.loads(res.stdout)
            self.assertTrue(isinstance(parsed, list) or isinstance(parsed, dict))


    def test_sqlite_harness_progress_handler_interrupts_at_one_million_opcodes(self) -> None:
        import subprocess, tempfile
        adapter = self.registry.get(LanguageId.SQLITE)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        harness_script = adapter.execution_spec.execution_args[2]

        with tempfile.TemporaryDirectory() as tmpdir:
            sql_path = os.path.join(tmpdir, "solution.sql")
            sql = "WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt) SELECT count(*) FROM cnt;\n"
            with open(sql_path, "w", encoding="utf-8") as f:
                f.write(sql)
            res = subprocess.run(
                [sys.executable, "-I", "-c", harness_script, sql_path],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("interrupted", res.stderr)


    def test_sqlite_harness_progress_handler_exact_boundary(self) -> None:
        """Verifies that progress callback stops at the exact 1,000,000 opcode interval (ticks >= max_ticks)."""
        max_opcodes = 1000000
        opcode_interval = 100
        max_ticks = max_opcodes // opcode_interval
        self.assertEqual(max_ticks, 10000)
        ticks = 0
        def progress_cb():
            nonlocal ticks
            ticks += 1
            return 1 if ticks >= max_ticks else 0

        ticks = 9998
        self.assertEqual(progress_cb(), 0)
        self.assertEqual(ticks, 9999)
        self.assertEqual(progress_cb(), 1)
        self.assertEqual(ticks, 10000)

    def test_sqlite_harness_supports_manual_transactions(self) -> None:
        """Verifies that SQLite harness supports autocommit mode and manual BEGIN/COMMIT blocks without conflict."""
        adapter = self.registry.get(LanguageId.SQLITE)
        assert adapter is not None
        harness_script = adapter.execution_spec.execution_args[2]

        with tempfile.TemporaryDirectory() as tmpdir:
            sql_path = os.path.join(tmpdir, "solution.sql")
            with open(sql_path, "w", encoding="utf-8") as f:
                f.write(
                    "CREATE TABLE t (id INT, val TEXT);\n"
                    "INSERT INTO t VALUES (1, 'pre_tx');\n"
                    "BEGIN TRANSACTION;\n"
                    "INSERT INTO t VALUES (2, 'in_tx');\n"
                    "COMMIT;\n"
                    "SELECT * FROM t;\n"
                )

            res = subprocess.run(
                [sys.executable, "-I", "-c", harness_script, sql_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(res.returncode, 0, f"stderr: {res.stderr}")
            data = json.loads(res.stdout)
            self.assertEqual(data["columns"], ["id", "val"])
            self.assertEqual(data["rows"], [[1, "pre_tx"], [2, "in_tx"]])

    def test_sqlite_harness_serializes_binary_blobs_and_nulls(self) -> None:
        """Verifies that SQLite harness serializes binary BLOBs as hex strings and NULLs as JSON null without error."""
        adapter = self.registry.get(LanguageId.SQLITE)
        assert adapter is not None
        harness_script = adapter.execution_spec.execution_args[2]

        with tempfile.TemporaryDirectory() as tmpdir:
            sql_path = os.path.join(tmpdir, "solution.sql")
            with open(sql_path, "w", encoding="utf-8") as f:
                f.write(
                    "CREATE TABLE t (id INT, data BLOB, note TEXT);\n"
                    "INSERT INTO t VALUES (1, X'484558', NULL);\n"
                    "SELECT * FROM t;\n"
                )

            res = subprocess.run(
                [sys.executable, "-I", "-c", harness_script, sql_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(res.returncode, 0, f"stderr: {res.stderr}")
            data = json.loads(res.stdout)
            self.assertEqual(data["columns"], ["id", "data", "note"])
            self.assertEqual(data["rows"], [[1, "484558", None]])



    def test_unsupported_phase5_runtimes_fail_closed(self) -> None:
        unsupported = [
            LanguageId.PYTHON2,
            LanguageId.SWIFT,
        ]
        for lang_id in unsupported:
            req = ExecutionRequest(
                request_id=f"req-unsupported-{lang_id.value}",
                language_id=lang_id,
                source_code="print('hello')\n",
            )
            res = self.pipeline.execute(req)
            self.assertEqual(res.final_status, Milestone.SANDBOX_ERROR)
            self.assertIn("is not installed on this host", res.stderr)
            self.assertEqual(len(self.runner.invocations), 0)

    def test_live_ruby_execution_with_native_runner(self) -> None:
        import subprocess
        from engine.runner import NativeSandboxRunner

        if not os.path.exists("sandbox/pes2-runner"):
            self.skipTest("sandbox/pes2-runner not built")
        try:
            res = subprocess.run(["unshare", "-U", "true"], capture_output=True)
            if res.returncode != 0:
                self.skipTest("User namespaces restricted in current environment")
        except FileNotFoundError:
            self.skipTest("unshare binary not found")

        cgroup_root = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice"
        if not os.path.exists(cgroup_root):
            self.skipTest("Delegated cgroup root not found")

        runner = NativeSandboxRunner(runner_path="sandbox/pes2-runner", cgroup_root=cgroup_root)
        pipeline = ExecutionPipeline(self.registry, runner)

        # 1. Normal Ruby execution
        ok_req = ExecutionRequest(
            request_id="req-live-ruby-ok",
            language_id=LanguageId.RUBY,
            source_code="puts 'live ruby sandboxed'\n",
        )
        ok_res = pipeline.execute(ok_req)
        self.assertEqual(ok_res.final_status, Milestone.COMPLETED)
        self.assertEqual(ok_res.exit_code, 0)
        self.assertEqual(ok_res.stdout.strip(), "live ruby sandboxed")
        milestone_names = [m.milestone for m in ok_res.milestones]
        self.assertEqual(milestone_names, [Milestone.ACCEPTED, Milestone.RUNNING, Milestone.COMPLETED])
        self.assertNotIn(Milestone.COMPILING, milestone_names)

        # 2. Syntax error handling
        err_req = ExecutionRequest(
            request_id="req-live-ruby-syntax-err",
            language_id=LanguageId.RUBY,
            source_code="def broken(\n",
        )
        err_res = pipeline.execute(err_req)
        self.assertEqual(err_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(err_res.exit_code, 1)
        self.assertIn("syntax error", err_res.stderr)

        # 3. Wall timeout containment
        timeout_req = ExecutionRequest(
            request_id="req-live-ruby-timeout",
            language_id=LanguageId.RUBY,
            source_code="sleep 10\n",
            custom_limits=ResourceLimits(wall_ms=500, cpu_ms=500, memory_kb=65536, pids=16, max_output_bytes=65536),
        )
        timeout_res = pipeline.execute(timeout_req)
        self.assertEqual(timeout_res.final_status, Milestone.TIMEOUT)

        # 4. Memory limit enforcement
        mem_req = ExecutionRequest(
            request_id="req-live-ruby-oom",
            language_id=LanguageId.RUBY,
            source_code="arr = []; loop { arr << ('x' * 1024 * 1024) }\n",
            custom_limits=ResourceLimits(wall_ms=5000, cpu_ms=2000, memory_kb=32768, pids=16, max_output_bytes=65536),
        )
        mem_res = pipeline.execute(mem_req)
        self.assertEqual(mem_res.final_status, Milestone.MEMORY_LIMIT)
        self.assertEqual(mem_res.signal, 9)

        # 5. Network denial (socket blocked by policy)
        evil_req = ExecutionRequest(
            request_id="req-live-ruby-socket-blocked",
            language_id=LanguageId.RUBY,
            source_code="require 'socket'; TCPSocket.new('1.1.1.1', 80)\n",
        )
        evil_res = pipeline.execute(evil_req)
        self.assertEqual(evil_res.final_status, Milestone.BLOCKED_BY_POLICY)
        self.assertEqual(evil_res.signal, 31)

        # 6. Fiber scheduler execution inside sandbox
        fiber_src = (
            "f = Fiber.new do |val|\n"
            "  Fiber.yield val * 2\n"
            "  'done'\n"
            "end\n"
            "res1 = f.resume(21)\n"
            "res2 = f.resume\n"
            "puts \"Fiber-Result: #{res1}, #{res2}\"\n"
        )
        fiber_req = ExecutionRequest(
            request_id="req-live-ruby-fiber",
            language_id=LanguageId.RUBY,
            source_code=fiber_src,
        )
        fiber_res = pipeline.execute(fiber_req)
        self.assertEqual(fiber_res.final_status, Milestone.COMPLETED)
        self.assertEqual(fiber_res.exit_code, 0)
        self.assertIn("Fiber-Result: 42, done", fiber_res.stdout)

        # 7. Verify zero cgroup leftovers
        leftovers = [
            d for d in os.listdir(cgroup_root)
            if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
        ]
        self.assertEqual(len(leftovers), 0)

    def test_live_javascript_execution_with_native_runner(self) -> None:
        import subprocess
        from engine.runner import NativeSandboxRunner

        if not os.path.exists("sandbox/pes2-runner"):
            self.skipTest("sandbox/pes2-runner not built")
        try:
            res = subprocess.run(["unshare", "-U", "true"], capture_output=True)
            if res.returncode != 0:
                self.skipTest("User namespaces restricted in current environment")
        except FileNotFoundError:
            self.skipTest("unshare binary not found")

        cgroup_root = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice"
        if not os.path.exists(cgroup_root):
            self.skipTest("Delegated cgroup root not found")

        runner = NativeSandboxRunner(runner_path="sandbox/pes2-runner", cgroup_root=cgroup_root)
        pipeline = ExecutionPipeline(self.registry, runner)

        # 1. Normal Node.js execution
        ok_req = ExecutionRequest(
            request_id="req-live-js-ok",
            language_id=LanguageId.JAVASCRIPT,
            source_code="console.log('live node sandboxed', 20 + 22);\n",
        )
        ok_res = pipeline.execute(ok_req)
        self.assertEqual(ok_res.final_status, Milestone.COMPLETED)
        self.assertEqual(ok_res.exit_code, 0)
        self.assertEqual(ok_res.stdout.strip(), "live node sandboxed 42")
        milestone_names = [m.milestone for m in ok_res.milestones]
        self.assertEqual(milestone_names, [Milestone.ACCEPTED, Milestone.RUNNING, Milestone.COMPLETED])
        self.assertNotIn(Milestone.COMPILING, milestone_names)

        # 2. Syntax error handling
        err_req = ExecutionRequest(
            request_id="req-live-js-syntax-err",
            language_id=LanguageId.JAVASCRIPT,
            source_code="function broken( {\n",
        )
        err_res = pipeline.execute(err_req)
        self.assertEqual(err_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(err_res.exit_code, 1)
        self.assertIn("SyntaxError", err_res.stderr)

        # 3a. Wall timeout containment (idle process waiting on timer)
        wall_timeout_req = ExecutionRequest(
            request_id="req-live-js-wall-timeout",
            language_id=LanguageId.JAVASCRIPT,
            source_code="setTimeout(() => {}, 10000);\n",
            custom_limits=ResourceLimits(wall_ms=500, cpu_ms=500, memory_kb=131072, pids=32, max_output_bytes=65536),
        )
        wall_timeout_res = pipeline.execute(wall_timeout_req)
        self.assertEqual(wall_timeout_res.final_status, Milestone.TIMEOUT)

        # 3b. CPU timeout containment (CPU exhaustion via infinite busy loop)
        cpu_timeout_req = ExecutionRequest(
            request_id="req-live-js-cpu-timeout",
            language_id=LanguageId.JAVASCRIPT,
            source_code="while(true) {}\n",
            custom_limits=ResourceLimits(wall_ms=5000, cpu_ms=200, memory_kb=131072, pids=32, max_output_bytes=65536),
        )
        cpu_timeout_res = pipeline.execute(cpu_timeout_req)
        self.assertEqual(cpu_timeout_res.final_status, Milestone.TIMEOUT)
        self.assertGreaterEqual(cpu_timeout_res.cpu_ms, 200)
        self.assertLess(cpu_timeout_res.wall_ms, 4000)

        # 4. Memory limit enforcement at configured adapter default (128 MiB cgroup limit)
        mem_src = (
            "const arr = [];\n"
            "for (let i = 0; i < 20; i++) {\n"
            "  const b = Buffer.alloc(10 * 1024 * 1024);\n"
            "  b.fill(1);\n"
            "  arr.push(b);\n"
            "}\n"
        )
        mem_req = ExecutionRequest(
            request_id="req-live-js-oom-default-128mb",
            language_id=LanguageId.JAVASCRIPT,
            source_code=mem_src,
            # Exercises adapter's default_resource_limits (memory_kb=131072) without custom overrides
        )
        mem_res = pipeline.execute(mem_req)
        self.assertEqual(mem_res.final_status, Milestone.MEMORY_LIMIT)
        self.assertEqual(mem_res.signal, 9)
        self.assertGreaterEqual(mem_res.peak_memory_kb, 131072)

        # 5. Network denial (socket blocked by policy)
        evil_req = ExecutionRequest(
            request_id="req-live-js-socket-blocked",
            language_id=LanguageId.JAVASCRIPT,
            source_code="require('http').get('http://1.1.1.1');\n",
        )
        evil_res = pipeline.execute(evil_req)
        self.assertEqual(evil_res.final_status, Milestone.BLOCKED_BY_POLICY)
        self.assertEqual(evil_res.signal, 31)

        # 6. Libuv async event loop timer execution inside sandbox
        async_src = (
            "setTimeout(() => {\n"
            "  console.log('Async-Timer-Result: 42, done');\n"
            "}, 10);\n"
        )
        async_req = ExecutionRequest(
            request_id="req-live-js-async",
            language_id=LanguageId.JAVASCRIPT,
            source_code=async_src,
        )
        async_res = pipeline.execute(async_req)
        self.assertEqual(async_res.final_status, Milestone.COMPLETED)
        self.assertEqual(async_res.exit_code, 0)
        self.assertIn("Async-Timer-Result: 42, done", async_res.stdout)

        # 7. Verify zero cgroup leftovers
        leftovers = [
            d for d in os.listdir(cgroup_root)
            if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
        ]
        self.assertEqual(len(leftovers), 0)

    def test_live_php_execution_with_native_runner(self) -> None:
        import subprocess
        from engine.runner import NativeSandboxRunner

        if not os.path.exists("sandbox/pes2-runner"):
            self.skipTest("sandbox/pes2-runner not built")
        try:
            res = subprocess.run(["unshare", "-U", "true"], capture_output=True)
            if res.returncode != 0:
                self.skipTest("User namespaces restricted in current environment")
        except FileNotFoundError:
            self.skipTest("unshare binary not found")

        cgroup_root = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice"
        if not os.path.exists(cgroup_root):
            self.skipTest("Delegated cgroup root not found")

        runner = NativeSandboxRunner(runner_path="sandbox/pes2-runner", cgroup_root=cgroup_root)
        pipeline = ExecutionPipeline(self.registry, runner)

        # 1. Normal PHP execution
        ok_req = ExecutionRequest(
            request_id="req-live-php-ok",
            language_id=LanguageId.PHP,
            source_code='<?php echo "live php sandboxed " . (20 + 22) . "\\n";\n',
        )
        ok_res = pipeline.execute(ok_req)
        self.assertEqual(ok_res.final_status, Milestone.COMPLETED)
        self.assertEqual(ok_res.exit_code, 0)
        self.assertEqual(ok_res.stdout.strip(), "live php sandboxed 42")
        milestone_names = [m.milestone for m in ok_res.milestones]
        self.assertEqual(milestone_names, [Milestone.ACCEPTED, Milestone.RUNNING, Milestone.COMPLETED])
        self.assertNotIn(Milestone.COMPILING, milestone_names)

        # 2. Syntax error handling
        err_req = ExecutionRequest(
            request_id="req-live-php-syntax-err",
            language_id=LanguageId.PHP,
            source_code="<?php function broken( {\n",
        )
        err_res = pipeline.execute(err_req)
        self.assertEqual(err_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(err_res.exit_code, 255)

        # 3a. Wall timeout containment (idle process sleeping)
        wall_timeout_req = ExecutionRequest(
            request_id="req-live-php-wall-timeout",
            language_id=LanguageId.PHP,
            source_code="<?php sleep(10);\n",
            custom_limits=ResourceLimits(wall_ms=500, cpu_ms=500, memory_kb=65536, pids=16, max_output_bytes=65536),
        )
        wall_timeout_res = pipeline.execute(wall_timeout_req)
        self.assertEqual(wall_timeout_res.final_status, Milestone.TIMEOUT)

        # 3b. CPU timeout containment (CPU exhaustion via infinite busy loop)
        cpu_timeout_req = ExecutionRequest(
            request_id="req-live-php-cpu-timeout",
            language_id=LanguageId.PHP,
            source_code="<?php while(true) {}\n",
            custom_limits=ResourceLimits(wall_ms=5000, cpu_ms=200, memory_kb=65536, pids=16, max_output_bytes=65536),
        )
        cpu_timeout_res = pipeline.execute(cpu_timeout_req)
        self.assertEqual(cpu_timeout_res.final_status, Milestone.TIMEOUT)
        self.assertGreaterEqual(cpu_timeout_res.cpu_ms, 200)
        self.assertLess(cpu_timeout_res.wall_ms, 4000)

        # 4. Memory limit enforcement at configured adapter default (64 MiB cgroup limit)
        mem_src = (
            "<?php\n"
            "$arr = [];\n"
            "for ($i = 0; $i < 20; $i++) {\n"
            '  $arr[] = str_repeat("A", 5 * 1024 * 1024);\n'
            "}\n"
            'echo "allocated " . count($arr) . "\\n";\n'
        )
        mem_req = ExecutionRequest(
            request_id="req-live-php-oom-default-64mb",
            language_id=LanguageId.PHP,
            source_code=mem_src,
            # Exercises adapter's default_resource_limits (memory_kb=65536) without custom overrides
        )
        mem_res = pipeline.execute(mem_req)
        self.assertEqual(mem_res.final_status, Milestone.MEMORY_LIMIT)
        self.assertEqual(mem_res.signal, 9)
        self.assertGreaterEqual(mem_res.peak_memory_kb, 65536)

        # 5. Network denial (socket blocked by policy)
        evil_req = ExecutionRequest(
            request_id="req-live-php-socket-blocked",
            language_id=LanguageId.PHP,
            source_code="<?php file_get_contents('http://1.1.1.1');\n",
        )
        evil_res = pipeline.execute(evil_req)
        self.assertEqual(evil_res.final_status, Milestone.BLOCKED_BY_POLICY)
        self.assertEqual(evil_res.signal, 31)

        # 6. PHP Fiber / standard feature execution inside sandbox
        fiber_src = (
            "<?php\n"
            "$f = new Fiber(function() { Fiber::suspend('fiber-ok'); });\n"
            'echo "PHP-Fiber-Result: " . $f->start() . "\\n";\n'
        )
        fiber_req = ExecutionRequest(
            request_id="req-live-php-fiber",
            language_id=LanguageId.PHP,
            source_code=fiber_src,
        )
        fiber_res = pipeline.execute(fiber_req)
        self.assertEqual(fiber_res.final_status, Milestone.COMPLETED)
        self.assertEqual(fiber_res.exit_code, 0)
        self.assertIn("PHP-Fiber-Result: fiber-ok", fiber_res.stdout)

        # 7. Verify zero cgroup leftovers
        leftovers = [
            d for d in os.listdir(cgroup_root)
            if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
        ]
        self.assertEqual(len(leftovers), 0)

    def test_live_typescript_execution_with_native_runner(self) -> None:
        import subprocess
        from engine.runner import NativeSandboxRunner

        if not os.path.exists("sandbox/pes2-runner"):
            self.skipTest("sandbox/pes2-runner not built")
        try:
            res = subprocess.run(["unshare", "-U", "true"], capture_output=True)
            if res.returncode != 0:
                self.skipTest("User namespaces restricted in current environment")
        except FileNotFoundError:
            self.skipTest("unshare binary not found")

        cgroup_root = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice"
        if not os.path.exists(cgroup_root):
            self.skipTest("Delegated cgroup root not found")

        runner = NativeSandboxRunner(runner_path="sandbox/pes2-runner", cgroup_root=cgroup_root)
        pipeline = ExecutionPipeline(self.registry, runner)

        # 1. Normal TypeScript compilation and execution
        ok_req = ExecutionRequest(
            request_id="req-live-ts-ok",
            language_id=LanguageId.TYPESCRIPT,
            source_code='const greeting: string = "TypeScript Live Execution OK"; console.log(greeting, 20 + 22);\n',
        )
        ok_res = pipeline.execute(ok_req)
        self.assertEqual(ok_res.final_status, Milestone.COMPLETED)
        self.assertEqual(ok_res.exit_code, 0)
        self.assertEqual(ok_res.stdout.strip(), "TypeScript Live Execution OK 42")
        milestone_names = [m.milestone for m in ok_res.milestones]
        self.assertEqual(milestone_names, [Milestone.ACCEPTED, Milestone.COMPILING, Milestone.RUNNING, Milestone.COMPLETED])

        # 2. Type/Syntax error resulting in COMPILE_ERROR with no execution stage
        type_err_req = ExecutionRequest(
            request_id="req-live-ts-type-err",
            language_id=LanguageId.TYPESCRIPT,
            source_code='const x: number = "cannot assign string to number";\n',
        )
        type_err_res = pipeline.execute(type_err_req)
        self.assertEqual(type_err_res.final_status, Milestone.COMPILE_ERROR)
        self.assertNotEqual(type_err_res.exit_code, 0)
        diag = (type_err_res.compile_output or "") + type_err_res.stdout + type_err_res.stderr
        self.assertTrue("TS2322" in diag or "Type 'string' is not assignable" in diag)
        milestones = [m.milestone for m in type_err_res.milestones]
        self.assertEqual(milestones, [Milestone.ACCEPTED, Milestone.COMPILING, Milestone.COMPILE_ERROR])
        self.assertNotIn(Milestone.RUNNING, milestones)

        # 3. JavaScript runtime exception resulting in RUNTIME_ERROR
        rt_err_req = ExecutionRequest(
            request_id="req-live-ts-rt-err",
            language_id=LanguageId.TYPESCRIPT,
            source_code='const obj: any = null;\nobj.triggerRuntimeError();\n',
        )
        rt_err_res = pipeline.execute(rt_err_req)
        self.assertEqual(rt_err_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertNotEqual(rt_err_res.exit_code, 0)
        self.assertIn("TypeError", rt_err_res.stderr)
        milestones = [m.milestone for m in rt_err_res.milestones]
        self.assertEqual(milestones, [Milestone.ACCEPTED, Milestone.COMPILING, Milestone.RUNNING, Milestone.RUNTIME_ERROR])

        # 4a. Wall timeout containment (idle process sleeping)
        wall_timeout_req = ExecutionRequest(
            request_id="req-live-ts-wall-timeout",
            language_id=LanguageId.TYPESCRIPT,
            source_code='setTimeout(() => {}, 10000);\n',
            custom_limits=ResourceLimits(wall_ms=500, cpu_ms=500, memory_kb=131072, pids=32, max_output_bytes=65536),
        )
        wall_timeout_res = pipeline.execute(wall_timeout_req)
        self.assertEqual(wall_timeout_res.final_status, Milestone.TIMEOUT)

        # 4b. CPU timeout containment (CPU exhaustion via infinite busy loop)
        cpu_timeout_req = ExecutionRequest(
            request_id="req-live-ts-cpu-timeout",
            language_id=LanguageId.TYPESCRIPT,
            source_code='while (true) {}\n',
            custom_limits=ResourceLimits(wall_ms=5000, cpu_ms=200, memory_kb=131072, pids=32, max_output_bytes=65536),
        )
        cpu_timeout_res = pipeline.execute(cpu_timeout_req)
        self.assertEqual(cpu_timeout_res.final_status, Milestone.TIMEOUT)
        self.assertGreaterEqual(cpu_timeout_res.cpu_ms, 200)
        self.assertLess(cpu_timeout_res.wall_ms, 4000)

        # 5. Network denial (socket blocked by policy)
        evil_req = ExecutionRequest(
            request_id="req-live-ts-socket-blocked",
            language_id=LanguageId.TYPESCRIPT,
            source_code='declare function require(name: string): any;\nconst http = require("http"); http.get("http://1.1.1.1");\n',
        )
        evil_res = pipeline.execute(evil_req)
        self.assertEqual(evil_res.final_status, Milestone.BLOCKED_BY_POLICY)
        self.assertEqual(evil_res.signal, 31)

        # 6. Verify zero cgroup leftovers
        leftovers = [
            d for d in os.listdir(cgroup_root)
            if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
        ]
        self.assertEqual(len(leftovers), 0)

    def test_live_dart_execution_with_native_runner(self) -> None:
        import subprocess
        from engine.runner import NativeSandboxRunner

        if not os.path.exists("sandbox/pes2-runner"):
            self.skipTest("sandbox/pes2-runner not built")
        try:
            res = subprocess.run(["unshare", "-U", "true"], capture_output=True)
            if res.returncode != 0:
                self.skipTest("User namespaces restricted in current environment")
        except FileNotFoundError:
            self.skipTest("unshare binary not found")

        cgroup_root = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice"
        if not os.path.exists(cgroup_root):
            self.skipTest("Delegated cgroup root not found")

        dart_adapter = self.registry.get(LanguageId.DART)
        if not dart_adapter or not dart_adapter.is_installed:
            self.skipTest("Dart toolchain not installed on host")

        runner = NativeSandboxRunner(runner_path="sandbox/pes2-runner", cgroup_root=cgroup_root)
        pipeline = ExecutionPipeline(registry=self.registry, runner=runner)

        # 1. Normal execution with procfs and read-only verification
        dart_src = (
            "import 'dart:io';\n"
            "void main() {\n"
            "  final maps = File('/proc/self/maps').readAsStringSync();\n"
            "  final exe = Link('/proc/self/exe').targetSync();\n"
            "  final pids = Directory('/proc').listSync()\n"
            "      .map((e) => e.path.split('/').last)\n"
            "      .where((n) => RegExp(r'^\\d+$').hasMatch(n)).toList();\n"
            "  bool procWriteBlocked = false;\n"
            "  try {\n"
            "    File('/proc/1/comm').writeAsStringSync('bad');\n"
            "  } catch (e) {\n"
            "    procWriteBlocked = true;\n"
            "  }\n"
            "  bool sdkWriteBlocked = false;\n"
            "  try {\n"
            "    File('/opt/dart-sdk/leak.txt').writeAsStringSync('bad');\n"
            "  } catch (e) {\n"
            "    sdkWriteBlocked = true;\n"
            "  }\n"
            "  print('MAPS_LEN:' + maps.length.toString());\n"
            "  print('EXE_TARGET:' + exe);\n"
            "  print('PIDS_LEN:' + pids.length.toString());\n"
            "  print('PROC_RO:' + procWriteBlocked.toString());\n"
            "  print('SDK_RO:' + sdkWriteBlocked.toString());\n"
            "  print('DART_EXEC_SUCCESS');\n"
            "}\n"
        )
        ok_req = ExecutionRequest(
            request_id="req-live-dart-ok",
            language_id=LanguageId.DART,
            source_code=dart_src,
        )
        ok_res = pipeline.execute(ok_req)
        self.assertEqual(ok_res.final_status, Milestone.COMPLETED)
        self.assertEqual(ok_res.exit_code, 0)
        self.assertIn("DART_EXEC_SUCCESS", ok_res.stdout)
        self.assertIn("PROC_RO:true", ok_res.stdout)
        self.assertIn("SDK_RO:true", ok_res.stdout)
        self.assertIn("PIDS_LEN:1", ok_res.stdout)

        # 2. Syntax / runtime error
        err_req = ExecutionRequest(
            request_id="req-live-dart-err",
            language_id=LanguageId.DART,
            source_code="void main() { throw Exception('dart test error'); }\n",
        )
        err_res = pipeline.execute(err_req)
        self.assertEqual(err_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertIn("dart test error", err_res.stderr)

        # 3a. Wall timeout
        wall_to_req = ExecutionRequest(
            request_id="req-live-dart-wall-timeout",
            language_id=LanguageId.DART,
            source_code="import 'dart:io'; void main() { sleep(Duration(seconds: 10)); }\n",
            custom_limits=ResourceLimits(wall_ms=500, cpu_ms=500, memory_kb=196608, pids=32, max_output_bytes=65536),
        )
        wall_to_res = pipeline.execute(wall_to_req)
        self.assertEqual(wall_to_res.final_status, Milestone.TIMEOUT)

        # 3b. CPU timeout
        cpu_to_req = ExecutionRequest(
            request_id="req-live-dart-cpu-timeout",
            language_id=LanguageId.DART,
            source_code="void main() { while(true) {} }\n",
            custom_limits=ResourceLimits(wall_ms=5000, cpu_ms=200, memory_kb=196608, pids=32, max_output_bytes=65536),
        )
        cpu_to_res = pipeline.execute(cpu_to_req)
        self.assertEqual(cpu_to_res.final_status, Milestone.TIMEOUT)

        # 4. Memory limit enforcement at adapter default (192 MiB)
        oom_src = (
            "void main() {\n"
            "  final list = <List<int>>[];\n"
            "  while (true) {\n"
            "    list.add(List<int>.filled(10000000, 42));\n"
            "  }\n"
            "}\n"
        )
        oom_req = ExecutionRequest(
            request_id="req-live-dart-oom-default-192mb",
            language_id=LanguageId.DART,
            source_code=oom_src,
        )
        oom_res = pipeline.execute(oom_req)
        self.assertEqual(oom_res.final_status, Milestone.MEMORY_LIMIT)
        self.assertEqual(oom_res.signal, 9)
        self.assertGreaterEqual(oom_res.peak_memory_kb, 196608)

        # 5. Network denial (socket blocked by policy)
        evil_req = ExecutionRequest(
            request_id="req-live-dart-socket-blocked",
            language_id=LanguageId.DART,
            source_code="import 'dart:io'; void main() async { await Socket.connect('127.0.0.1', 80); }\n",
        )
        evil_res = pipeline.execute(evil_req)
        self.assertEqual(evil_res.final_status, Milestone.BLOCKED_BY_POLICY)
        self.assertEqual(evil_res.signal, 31)

        # 6. Verify zero cgroup leftovers
        leftovers = [
            d for d in os.listdir(cgroup_root)
            if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
        ]
        self.assertEqual(len(leftovers), 0)

    def test_live_racket_execution_with_native_runner(self) -> None:
        import subprocess
        from engine.runner import NativeSandboxRunner

        if not os.path.exists("sandbox/pes2-runner"):
            self.skipTest("sandbox/pes2-runner not built")
        try:
            res = subprocess.run(["unshare", "-U", "true"], capture_output=True)
            if res.returncode != 0:
                self.skipTest("User namespaces restricted in current environment")
        except FileNotFoundError:
            self.skipTest("unshare binary not found")

        cgroup_root = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice"
        if not os.path.exists(cgroup_root):
            self.skipTest("Delegated cgroup root not found")

        racket_adapter = self.registry.get(LanguageId.RACKET)
        if not racket_adapter or not racket_adapter.is_installed:
            self.skipTest("Racket toolchain not installed on host")

        runner = NativeSandboxRunner(runner_path="sandbox/pes2-runner", cgroup_root=cgroup_root)
        pipeline = ExecutionPipeline(registry=self.registry, runner=runner)

        # 1. Normal execution with read-only /etc/racket verification
        rkt_src = (
            "#lang racket\n"
            "(displayln \"RACKET_EXEC_SUCCESS\")\n"
            "(define etc-ro\n"
            "  (with-handlers ([exn:fail? (lambda (e) \"true\")])\n"
            "    (with-output-to-file \"/etc/racket/leak.txt\" (lambda () (displayln \"bad\")))\n"
            "    \"false\"))\n"
            "(displayln (string-append \"ETC_RO:\" etc-ro))\n"
        )
        ok_req = ExecutionRequest(
            request_id="req-live-racket-ok",
            language_id=LanguageId.RACKET,
            source_code=rkt_src,
        )
        ok_res = pipeline.execute(ok_req)
        self.assertEqual(ok_res.final_status, Milestone.COMPLETED)
        self.assertEqual(ok_res.exit_code, 0)
        self.assertIn("RACKET_EXEC_SUCCESS", ok_res.stdout)
        self.assertIn("ETC_RO:true", ok_res.stdout)

        # 2. Syntax / runtime error
        err_req = ExecutionRequest(
            request_id="req-live-racket-err",
            language_id=LanguageId.RACKET,
            source_code="#lang racket\n(error 'test \"racket test error\")\n",
        )
        err_res = pipeline.execute(err_req)
        self.assertEqual(err_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(err_res.exit_code, 1)
        self.assertIn("racket test error", err_res.stderr)

        # 3a. Wall timeout
        wall_to_req = ExecutionRequest(
            request_id="req-live-racket-wall-timeout",
            language_id=LanguageId.RACKET,
            source_code="#lang racket\n(sleep 10)\n",
            custom_limits=ResourceLimits(wall_ms=500, cpu_ms=500, memory_kb=196608, pids=32, max_output_bytes=65536),
        )
        wall_to_res = pipeline.execute(wall_to_req)
        self.assertEqual(wall_to_res.final_status, Milestone.TIMEOUT)

        # 3b. CPU timeout
        cpu_to_req = ExecutionRequest(
            request_id="req-live-racket-cpu-timeout",
            language_id=LanguageId.RACKET,
            source_code="#lang racket\n(let loop () (loop))\n",
            custom_limits=ResourceLimits(wall_ms=5000, cpu_ms=200, memory_kb=196608, pids=32, max_output_bytes=65536),
        )
        cpu_to_res = pipeline.execute(cpu_to_req)
        self.assertEqual(cpu_to_res.final_status, Milestone.TIMEOUT)

        # 4. Memory limit enforcement at adapter default (192 MiB)
        oom_src = (
            "#lang racket\n"
            "(define (bomb acc)\n"
            "  (bomb (cons (make-bytes 10000000 42) acc)))\n"
            "(bomb null)\n"
        )
        oom_req = ExecutionRequest(
            request_id="req-live-racket-oom-default-192mb",
            language_id=LanguageId.RACKET,
            source_code=oom_src,
        )
        oom_res = pipeline.execute(oom_req)
        self.assertEqual(oom_res.final_status, Milestone.MEMORY_LIMIT)
        self.assertEqual(oom_res.signal, 9)
        self.assertGreaterEqual(oom_res.peak_memory_kb, 196608)

        # 5. Network denial (socket blocked by policy)
        evil_req = ExecutionRequest(
            request_id="req-live-racket-socket-blocked",
            language_id=LanguageId.RACKET,
            source_code="#lang racket\n(require racket/tcp)\n(tcp-connect \"127.0.0.1\" 80)\n",
        )
        evil_res = pipeline.execute(evil_req)
        self.assertEqual(evil_res.final_status, Milestone.BLOCKED_BY_POLICY)
        self.assertEqual(evil_res.signal, 31)

        # 6. Verify zero cgroup leftovers
        leftovers = [
            d for d in os.listdir(cgroup_root)
            if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
        ]
        self.assertEqual(len(leftovers), 0)

    def test_live_execution_erlang(self) -> None:
        try:
            res = subprocess.run(["unshare", "-Ur", "true"], capture_output=True, timeout=2)
            if res.returncode != 0:
                self.skipTest("User namespaces restricted in current environment")
        except FileNotFoundError:
            self.skipTest("unshare binary not found")

        cgroup_root = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice"
        if not os.path.exists(cgroup_root):
            self.skipTest("Delegated cgroup root not found")

        erl_adapter = self.registry.get(LanguageId.ERLANG)
        if not erl_adapter or not erl_adapter.is_installed:
            self.skipTest("Erlang not installed on host")

        runner = NativeSandboxRunner(runner_path="sandbox/pes2-runner", cgroup_root=cgroup_root)
        pipeline = ExecutionPipeline(self.registry, runner)

        # 1. Normal execution with start/0: covers exact /workspace/solution.erl path
        erl_src = (
            "-module(solution).\n"
            "-export([start/0]).\n"
            "start() ->\n"
            "    io:format(\"ERLANG_START_SUCCESS~n\").\n"
        )
        ok_req = ExecutionRequest(
            request_id="req-live-erlang-start",
            language_id=LanguageId.ERLANG,
            source_code=erl_src,
        )
        ok_res = pipeline.execute(ok_req)
        self.assertEqual(ok_res.final_status, Milestone.COMPLETED)
        self.assertEqual(ok_res.exit_code, 0)
        self.assertIn("ERLANG_START_SUCCESS", ok_res.stdout)

        # 1b. Normal execution with main/1
        erl_main_src = (
            "-module(solution).\n"
            "-export([main/1]).\n"
            "main(_) ->\n"
            "    io:format(\"ERLANG_MAIN_SUCCESS~n\").\n"
        )
        main_req = ExecutionRequest(
            request_id="req-live-erlang-main",
            language_id=LanguageId.ERLANG,
            source_code=erl_main_src,
        )
        main_res = pipeline.execute(main_req)
        self.assertEqual(main_res.final_status, Milestone.COMPLETED)
        self.assertEqual(main_res.exit_code, 0)
        self.assertIn("ERLANG_MAIN_SUCCESS", main_res.stdout)

        # 2. Syntax error
        syntax_err_req = ExecutionRequest(
            request_id="req-live-erlang-syntax-err",
            language_id=LanguageId.ERLANG,
            source_code="-module(solution).\nstart() -> syntax error here.\n",
        )
        syntax_err_res = pipeline.execute(syntax_err_req)
        self.assertEqual(syntax_err_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(syntax_err_res.exit_code, 1)

        # 2b. Runtime exception (badarith)
        crash_req = ExecutionRequest(
            request_id="req-live-erlang-crash",
            language_id=LanguageId.ERLANG,
            source_code="-module(solution).\n-export([start/0]).\nstart() -> 1 / 0.\n",
        )
        crash_res = pipeline.execute(crash_req)
        self.assertEqual(crash_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(crash_res.exit_code, 1)
        self.assertIn("badarith", crash_res.stderr)

        # 3a. Wall timeout
        wall_to_req = ExecutionRequest(
            request_id="req-live-erlang-wall-timeout",
            language_id=LanguageId.ERLANG,
            source_code="-module(solution).\n-export([start/0]).\nstart() -> timer:sleep(10000).\n",
            custom_limits=ResourceLimits(wall_ms=500, cpu_ms=500, memory_kb=196608, pids=64, max_output_bytes=65536),
        )
        wall_to_res = pipeline.execute(wall_to_req)
        self.assertEqual(wall_to_res.final_status, Milestone.TIMEOUT)

        # 3b. CPU timeout
        cpu_to_req = ExecutionRequest(
            request_id="req-live-erlang-cpu-timeout",
            language_id=LanguageId.ERLANG,
            source_code="-module(solution).\n-export([start/0, loop/0]).\nloop() -> loop().\nstart() -> loop().\n",
            custom_limits=ResourceLimits(wall_ms=5000, cpu_ms=200, memory_kb=196608, pids=64, max_output_bytes=65536),
        )
        cpu_to_res = pipeline.execute(cpu_to_req)
        self.assertEqual(cpu_to_res.final_status, Milestone.TIMEOUT)

        # 4. Memory limit enforcement at adapter default (192 MiB)
        oom_src = (
            "-module(solution).\n"
            "-export([start/0, bomb/1]).\n"
            "bomb(Acc) -> bomb([binary:copy(<<42>>, 20000000) | Acc]).\n"
            "start() -> bomb([]).\n"
        )
        oom_req = ExecutionRequest(
            request_id="req-live-erlang-oom-default-192mb",
            language_id=LanguageId.ERLANG,
            source_code=oom_src,
        )
        oom_res = pipeline.execute(oom_req)
        self.assertEqual(oom_res.final_status, Milestone.MEMORY_LIMIT)
        self.assertEqual(oom_res.signal, 9)
        self.assertGreaterEqual(oom_res.peak_memory_kb, 196608)

        # 5. Network denial (socket() returns EPERM via seccomp filter)
        evil_src = (
            "-module(solution).\n"
            "-export([start/0]).\n"
            "start() ->\n"
            "    case gen_tcp:connect(\"127.0.0.1\", 80, []) of\n"
            "        {error, eperm} ->\n"
            "            io:format(\"NETWORK_DENIED_EPERM~n\"),\n"
            "            halt(42);\n"
            "        _ ->\n"
            "            halt(1)\n"
            "    end.\n"
        )
        evil_req = ExecutionRequest(
            request_id="req-live-erlang-socket-blocked",
            language_id=LanguageId.ERLANG,
            source_code=evil_src,
        )
        evil_res = pipeline.execute(evil_req)
        self.assertEqual(evil_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(evil_res.exit_code, 42)
        self.assertIn("NETWORK_DENIED_EPERM", evil_res.stdout)

        # 6. Verify zero cgroup leftovers
        leftovers = [
            d for d in os.listdir(cgroup_root)
            if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
        ]
        self.assertEqual(len(leftovers), 0)

    def test_live_execution_elixir(self) -> None:
        try:
            res = subprocess.run(["unshare", "-Ur", "true"], capture_output=True, timeout=2)
            if res.returncode != 0:
                self.skipTest("User namespaces restricted in current environment")
        except FileNotFoundError:
            self.skipTest("unshare binary not found")

        cgroup_root = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice"
        if not os.path.exists(cgroup_root):
            self.skipTest("Delegated cgroup root not found")

        elixir_adapter = self.registry.get(LanguageId.ELIXIR)
        if not elixir_adapter or not elixir_adapter.is_installed:
            self.skipTest("Elixir not installed on host")

        runner = NativeSandboxRunner(runner_path="sandbox/pes2-runner", cgroup_root=cgroup_root)
        pipeline = ExecutionPipeline(self.registry, runner)

        # 1. Normal execution with Solution.start/0: covers exact /workspace/solution.exs path
        elixir_src = (
            "defmodule Solution do\n"
            "  def start do\n"
            "    IO.puts(\"ELIXIR_START_SUCCESS\")\n"
            "  end\n"
            "end\n"
        )
        ok_req = ExecutionRequest(
            request_id="req-live-elixir-start",
            language_id=LanguageId.ELIXIR,
            source_code=elixir_src,
        )
        ok_res = pipeline.execute(ok_req)
        self.assertEqual(ok_res.final_status, Milestone.COMPLETED)
        self.assertEqual(ok_res.exit_code, 0)
        self.assertIn("ELIXIR_START_SUCCESS", ok_res.stdout)

        # 1b. Normal execution with Solution.main/1
        elixir_main1_src = (
            "defmodule Solution do\n"
            "  def main(_args) do\n"
            "    IO.puts(\"ELIXIR_MAIN1_SUCCESS\")\n"
            "  end\n"
            "end\n"
        )
        main1_req = ExecutionRequest(
            request_id="req-live-elixir-main1",
            language_id=LanguageId.ELIXIR,
            source_code=elixir_main1_src,
        )
        main1_res = pipeline.execute(main1_req)
        self.assertEqual(main1_res.final_status, Milestone.COMPLETED)
        self.assertEqual(main1_res.exit_code, 0)
        self.assertIn("ELIXIR_MAIN1_SUCCESS", main1_res.stdout)

        # 1c. Normal execution with top-level script statements
        elixir_top_src = 'IO.puts("ELIXIR_TOP_SUCCESS")\n'
        top_req = ExecutionRequest(
            request_id="req-live-elixir-top",
            language_id=LanguageId.ELIXIR,
            source_code=elixir_top_src,
        )
        top_res = pipeline.execute(top_req)
        self.assertEqual(top_res.final_status, Milestone.COMPLETED)
        self.assertEqual(top_res.exit_code, 0)
        self.assertIn("ELIXIR_TOP_SUCCESS", top_res.stdout)

        # 2. Syntax error
        syntax_err_req = ExecutionRequest(
            request_id="req-live-elixir-syntax-err",
            language_id=LanguageId.ELIXIR,
            source_code="defmodule Solution do\n  def bad( do: :err\nend\n",
        )
        syntax_err_res = pipeline.execute(syntax_err_req)
        self.assertEqual(syntax_err_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(syntax_err_res.exit_code, 1)

        # 2b. Runtime exception (badarith)
        crash_req = ExecutionRequest(
            request_id="req-live-elixir-crash",
            language_id=LanguageId.ELIXIR,
            source_code="defmodule Solution do\n  def start do\n    1 / 0\n  end\nend\n",
        )
        crash_res = pipeline.execute(crash_req)
        self.assertEqual(crash_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(crash_res.exit_code, 1)
        self.assertIn("ArithmeticError", crash_res.stderr)

        # 3a. Wall timeout
        wall_to_req = ExecutionRequest(
            request_id="req-live-elixir-wall-timeout",
            language_id=LanguageId.ELIXIR,
            source_code="defmodule Solution do\n  def start do\n    Process.sleep(10000)\n  end\nend\n",
            custom_limits=ResourceLimits(wall_ms=500, cpu_ms=500, memory_kb=196608, pids=64, max_output_bytes=65536),
        )
        wall_to_res = pipeline.execute(wall_to_req)
        self.assertEqual(wall_to_res.final_status, Milestone.TIMEOUT)

        # 3b. CPU timeout
        cpu_to_req = ExecutionRequest(
            request_id="req-live-elixir-cpu-timeout",
            language_id=LanguageId.ELIXIR,
            source_code="defmodule Solution do\n  def loop, do: loop()\n  def start, do: loop()\nend\n",
            custom_limits=ResourceLimits(wall_ms=5000, cpu_ms=200, memory_kb=196608, pids=64, max_output_bytes=65536),
        )
        cpu_to_res = pipeline.execute(cpu_to_req)
        self.assertEqual(cpu_to_res.final_status, Milestone.TIMEOUT)

        # 4. Memory limit enforcement at adapter default (192 MiB)
        oom_src = (
            "defmodule Solution do\n"
            "  def bomb(acc) do\n"
            "    bomb([:binary.copy(<<42>>, 20000000) | acc])\n"
            "  end\n"
            "  def start, do: bomb([])\n"
            "end\n"
        )
        oom_req = ExecutionRequest(
            request_id="req-live-elixir-oom-default-192mb",
            language_id=LanguageId.ELIXIR,
            source_code=oom_src,
        )
        oom_res = pipeline.execute(oom_req)
        self.assertEqual(oom_res.final_status, Milestone.MEMORY_LIMIT)
        self.assertEqual(oom_res.signal, 9)
        self.assertGreaterEqual(oom_res.peak_memory_kb, 196608)

        # 5. Network denial (socket() returns EPERM via seccomp filter)
        evil_src = (
            "defmodule Solution do\n"
            "  def start do\n"
            "    case :gen_tcp.connect({127, 0, 0, 1}, 80, []) do\n"
            "      {:error, :eperm} ->\n"
            "        IO.puts(\"NETWORK_DENIED_EPERM\")\n"
            "        System.halt(42)\n"
            "      {:ok, _} ->\n"
            "        IO.puts(\"CONNECTED_UNEXPECTED\")\n"
            "    end\n"
            "  end\n"
            "end\n"
        )
        evil_req = ExecutionRequest(
            request_id="req-live-elixir-connect-blocked",
            language_id=LanguageId.ELIXIR,
            source_code=evil_src,
        )
        evil_res = pipeline.execute(evil_req)
        self.assertEqual(evil_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(evil_res.exit_code, 42)
        self.assertIn("NETWORK_DENIED_EPERM", evil_res.stdout)

        # 6. Verify zero cgroup leftovers
        leftovers = [
            d for d in os.listdir(cgroup_root)
            if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
        ]
        self.assertEqual(len(leftovers), 0)

    def test_live_csharp_execution_with_native_runner(self) -> None:
        import subprocess
        from engine.runner import NativeSandboxRunner

        if not os.path.exists("sandbox/pes2-runner"):
            self.skipTest("sandbox/pes2-runner not built")
        try:
            res = subprocess.run(["unshare", "-U", "true"], capture_output=True)
            if res.returncode != 0:
                self.skipTest("User namespaces restricted in current environment")
        except FileNotFoundError:
            self.skipTest("unshare binary not found")

        cgroup_root = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice"
        if not os.path.exists(cgroup_root):
            self.skipTest("Delegated cgroup root not found")

        csharp_adapter = self.registry.get(LanguageId.CSHARP)
        if not csharp_adapter or not csharp_adapter.is_installed:
            self.skipTest("C# (.NET) not installed on host")

        runner = NativeSandboxRunner(runner_path="sandbox/pes2-runner", cgroup_root=cgroup_root)
        pipeline = ExecutionPipeline(self.registry, runner)

        # 1. Normal compilation and execution
        cs_src = (
            "using System;\n"
            "class Solution {\n"
            "    static void Main() {\n"
            "        Console.WriteLine(\"CSHARP_LIVE_SUCCESS\");\n"
            "    }\n"
            "}\n"
        )
        ok_req = ExecutionRequest(
            request_id="req-live-csharp-ok",
            language_id=LanguageId.CSHARP,
            source_code=cs_src,
        )
        ok_res = pipeline.execute(ok_req)
        self.assertEqual(
            ok_res.final_status,
            Milestone.COMPLETED,
            "C# compile/execute failed: "
            f"exit={ok_res.exit_code} signal={ok_res.signal} "
            f"peak_memory_kb={ok_res.peak_memory_kb} "
            f"stdout={ok_res.stdout!r} stderr={ok_res.stderr!r} "
            f"compile_output={ok_res.compile_output!r}",
        )
        self.assertEqual(ok_res.exit_code, 0)
        self.assertIn("CSHARP_LIVE_SUCCESS", ok_res.stdout)

        # 2. Compilation error
        syntax_err_req = ExecutionRequest(
            request_id="req-live-csharp-syntax-err",
            language_id=LanguageId.CSHARP,
            source_code="class Solution { broken syntax here }\n",
        )
        syntax_err_res = pipeline.execute(syntax_err_req)
        self.assertEqual(syntax_err_res.final_status, Milestone.COMPILE_ERROR)
        self.assertEqual(syntax_err_res.exit_code, 1)

        # 3. Runtime exception
        crash_req = ExecutionRequest(
            request_id="req-live-csharp-crash",
            language_id=LanguageId.CSHARP,
            source_code=(
                "using System;\n"
                "class Solution {\n"
                "    static void Main() {\n"
                "        throw new InvalidOperationException(\"CSHARP_CRASH_TEST\");\n"
                "    }\n"
                "}\n"
            ),
        )
        crash_res = pipeline.execute(crash_req)
        self.assertEqual(crash_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertIn(crash_res.signal, (6, 11))
        self.assertIn("CSHARP_CRASH_TEST", crash_res.stderr)

        # 4. Timeout
        to_req = ExecutionRequest(
            request_id="req-live-csharp-timeout",
            language_id=LanguageId.CSHARP,
            source_code=(
                "using System;\n"
                "using System.Threading;\n"
                "class Solution {\n"
                "    static void Main() {\n"
                "        Thread.Sleep(10000);\n"
                "    }\n"
                "}\n"
            ),
            custom_limits=ResourceLimits(wall_ms=500, cpu_ms=500, memory_kb=131072, pids=32, max_output_bytes=65536),
        )
        to_res = pipeline.execute(to_req)
        self.assertEqual(to_res.final_status, Milestone.TIMEOUT)

        # 5. Memory limit enforcement at adapter default (128 MiB)
        oom_src = (
            "using System;\n"
            "using System.Collections.Generic;\n"
            "class Solution {\n"
            "    static void Main() {\n"
            "        var chunks = new List<byte[]>();\n"
            "        while (true) {\n"
            "            var chunk = new byte[10000000];\n"
            "            for (int i = 0; i < chunk.Length; i += 4096) chunk[i] = 1;\n"
            "            chunks.Add(chunk);\n"
            "        }\n"
            "    }\n"
            "}\n"
        )
        oom_req = ExecutionRequest(
            request_id="req-live-csharp-oom-default-128mb",
            language_id=LanguageId.CSHARP,
            source_code=oom_src,
        )
        oom_res = pipeline.execute(oom_req)
        self.assertEqual(oom_res.final_status, Milestone.MEMORY_LIMIT)
        self.assertEqual(oom_res.signal, 9)
        self.assertGreaterEqual(oom_res.peak_memory_kb, 131072)

        # 6. Network denial (socket() returns EPERM)
        evil_src = (
            "using System;\n"
            "using System.Net.Sockets;\n"
            "class Solution {\n"
            "    static void Main() {\n"
            "        try {\n"
            "            var s = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);\n"
            "            s.Connect(\"127.0.0.1\", 80);\n"
            "        } catch (Exception ex) {\n"
            "            Console.WriteLine($\"NETWORK_DENIED_{ex.GetType().Name}\");\n"
            "            Environment.Exit(42);\n"
            "        }\n"
            "    }\n"
            "}\n"
        )
        evil_req = ExecutionRequest(
            request_id="req-live-csharp-network-denied",
            language_id=LanguageId.CSHARP,
            source_code=evil_src,
        )
        evil_res = pipeline.execute(evil_req)
        self.assertEqual(evil_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(evil_res.exit_code, 42)
        self.assertIn("NETWORK_DENIED", evil_res.stdout)

        # 7. Verify zero cgroup leftovers
        leftovers = [
            d for d in os.listdir(cgroup_root)
            if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
        ]
        self.assertEqual(len(leftovers), 0)


    def test_live_sqlite_execution_with_native_runner(self) -> None:
        import json
        import subprocess
        from engine.runner import NativeSandboxRunner

        if not os.path.exists("sandbox/pes2-runner"):
            self.skipTest("sandbox/pes2-runner not built")
        try:
            res = subprocess.run(["unshare", "-U", "true"], capture_output=True)
            if res.returncode != 0:
                self.skipTest("User namespaces restricted in current environment")
        except FileNotFoundError:
            self.skipTest("unshare binary not found")

        cgroup_root = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice"
        if not os.path.exists(cgroup_root):
            self.skipTest("Delegated cgroup root not found")

        runner = NativeSandboxRunner(runner_path="sandbox/pes2-runner", cgroup_root=cgroup_root)
        pipeline = ExecutionPipeline(self.registry, runner)

        # 1. Normal SQLite execution: DDL, DML, and SELECT with JSON output
        sql_src = (
            "CREATE TABLE Employees (id INT, name TEXT, salary INT);\n"
            "INSERT INTO Employees VALUES (1, 'Alice', 70000), (2, 'Bob', 80000);\n"
            "SELECT name, salary FROM Employees WHERE salary > 75000;\n"
        )
        ok_req = ExecutionRequest(
            request_id="req-live-sqlite-ok",
            language_id=LanguageId.SQLITE,
            source_code=sql_src,
        )
        ok_res = pipeline.execute(ok_req)
        self.assertEqual(ok_res.final_status, Milestone.COMPLETED)
        self.assertEqual(ok_res.exit_code, 0)
        parsed = json.loads(ok_res.stdout)
        self.assertEqual(parsed, {"columns": ["name", "salary"], "rows": [["Bob", 80000]]})
        milestone_names = [m.milestone for m in ok_res.milestones]
        self.assertEqual(milestone_names, [Milestone.ACCEPTED, Milestone.RUNNING, Milestone.COMPLETED])
        self.assertNotIn(Milestone.COMPILING, milestone_names)

        # 2. Syntax error handling
        err_req = ExecutionRequest(
            request_id="req-live-sqlite-syntax-err",
            language_id=LanguageId.SQLITE,
            source_code="SELECT * FRO Employees;\n",
        )
        err_res = pipeline.execute(err_req)
        self.assertEqual(err_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(err_res.exit_code, 1)
        self.assertIn("syntax error", err_res.stderr)

        # 3. Missing table error
        missing_req = ExecutionRequest(
            request_id="req-live-sqlite-missing-tbl",
            language_id=LanguageId.SQLITE,
            source_code="SELECT * FROM NoSuchTable;\n",
        )
        missing_res = pipeline.execute(missing_req)
        self.assertEqual(missing_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(missing_res.exit_code, 1)
        self.assertIn("no such table", missing_res.stderr)

        # 4. Security: ATTACH external database blocked by authorizer
        attach_req = ExecutionRequest(
            request_id="req-live-sqlite-attach-denied",
            language_id=LanguageId.SQLITE,
            source_code="ATTACH '/workspace/evil.db' AS evil;\n",
        )
        attach_res = pipeline.execute(attach_req)
        self.assertEqual(attach_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(attach_res.exit_code, 1)
        self.assertIn("not authorized", attach_res.stderr)

        # 5. Security: Interactive dot-command rejected as syntax error
        dot_req = ExecutionRequest(
            request_id="req-live-sqlite-dot-cmd-denied",
            language_id=LanguageId.SQLITE,
            source_code=".shell id\n",
        )
        dot_res = pipeline.execute(dot_req)
        self.assertEqual(dot_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(dot_res.exit_code, 1)
        self.assertIn("syntax error", dot_res.stderr)

        # 6. Security: VACUUM INTO rejected safely without creating any file (including newlines)
        vacuum_req = ExecutionRequest(
            request_id="req-live-sqlite-vacuum-into-denied",
            language_id=LanguageId.SQLITE,
            source_code="CREATE TABLE secret (val TEXT);\nINSERT INTO secret VALUES ('sensitive');\nVACUUM\n  INTO\n  '/workspace/leak.db';\n",
        )
        vacuum_res = pipeline.execute(vacuum_req)
        self.assertEqual(vacuum_res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(vacuum_res.exit_code, 1)
        self.assertIn("VACUUM INTO is disabled", vacuum_res.stderr)

        # 7. Parsing regression check: Semicolons in strings and trigger bodies
        trigger_sql = (
            "CREATE TABLE Items (id INT, note TEXT);\n"
            "CREATE TRIGGER tr_note AFTER INSERT ON Items\n"
            "BEGIN\n"
            "    UPDATE Items SET note = 'triggered; ' || new.note WHERE id = new.id;\n"
            "END;\n"
            "INSERT INTO Items VALUES (1, 'val;with;multiple;semicolons');\n"
            "SELECT id, note FROM Items;\n"
        )
        trigger_req = ExecutionRequest(
            request_id="req-live-sqlite-triggers-strings-ok",
            language_id=LanguageId.SQLITE,
            source_code=trigger_sql,
        )
        trigger_res = pipeline.execute(trigger_req)
        self.assertEqual(trigger_res.final_status, Milestone.COMPLETED)
        self.assertEqual(trigger_res.exit_code, 0)
        parsed_trigger = json.loads(trigger_res.stdout)
        self.assertEqual(
            parsed_trigger,
            {"columns": ["id", "note"], "rows": [[1, "triggered; val;with;multiple;semicolons"]]},
        )

        # 8. Verify zero cgroup leftovers
        leftovers = [
            d for d in os.listdir(cgroup_root)
            if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
        ]
        self.assertEqual(len(leftovers), 0)

    # =========================================================================
    # Phase 7: Data Science (Pandas) & Browser Frontend Runtimes
    # =========================================================================

    def test_pandas_truthful_milestones_no_compiling(self) -> None:
        adapter = self.registry.get(LanguageId.PANDAS)
        if not adapter or not adapter.is_installed:
            self.skipTest(f"Pandas toolchain unavailable: {adapter.installed_version if adapter else 'None'}")
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 150, 120, 45000, '{"columns": ["id", "val"], "rows": [[1, "alpha"], [2, "beta"]]}\n', "")
        )
        request = ExecutionRequest(
            request_id="req-pandas-ok",
            language_id=LanguageId.PANDAS,
            source_code="import pandas as pd\ndf = pd.DataFrame({'id': [1, 2], 'val': ['alpha', 'beta']})\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertIn("columns", res.stdout)
        self.assertIn("rows", res.stdout)

        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.COMPLETED,
        ]
        self.assertEqual(milestone_names, expected)
        self.assertNotIn(Milestone.COMPILING, milestone_names)
        self.assertEqual(len(self.runner.invocations), 1)
        inv = self.runner.invocations[0]
        self.assertEqual(inv["seccomp_profile"], SeccompPolicyProfile.PYTHON)
        self.assertEqual(inv["limits"].memory_kb, 262144)  # 256 MiB
        self.assertEqual(inv["limits"].cpu_ms, 3000)
        self.assertEqual(inv["limits"].wall_ms, 5000)

    def test_pandas_syntax_or_runtime_error_milestone(self) -> None:
        adapter = self.registry.get(LanguageId.PANDAS)
        if not adapter or not adapter.is_installed:
            self.skipTest(f"Pandas toolchain unavailable: {adapter.installed_version if adapter else 'None'}")
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 1, 0, 80, 60, 40000, "", "KeyError: 'nonexistent_col'\n")
        )
        request = ExecutionRequest(
            request_id="req-pandas-err",
            language_id=LanguageId.PANDAS,
            source_code="import pandas as pd\ndf = pd.DataFrame({'a': [1]})\nprint(df['nonexistent_col'])\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.RUNTIME_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("KeyError", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.RUNTIME_ERROR,
        ]
        self.assertEqual(milestone_names, expected)

    def test_pandas_timeout_milestone(self) -> None:
        adapter = self.registry.get(LanguageId.PANDAS)
        if not adapter or not adapter.is_installed:
            self.skipTest(f"Pandas toolchain unavailable: {adapter.installed_version if adapter else 'None'}")
        self.runner.queue_result(
            RunnerResult("TIMEOUT", 124, 9, 5001, 3000, 50000, "", "Execution exceeded wall timeout\n")
        )
        request = ExecutionRequest(
            request_id="req-pandas-timeout",
            language_id=LanguageId.PANDAS,
            source_code="import time\nwhile True: time.sleep(1)\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.TIMEOUT)
        self.assertEqual(res.exit_code, 124)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.TIMEOUT,
        ]
        self.assertEqual(milestone_names, expected)

    def test_pandas_memory_limit_milestone(self) -> None:
        adapter = self.registry.get(LanguageId.PANDAS)
        if not adapter or not adapter.is_installed:
            self.skipTest(f"Pandas toolchain unavailable: {adapter.installed_version if adapter else 'None'}")
        self.runner.queue_result(
            RunnerResult("MEMORY_LIMIT", 137, 9, 500, 400, 262144, "", "Killed (Out of memory)\n")
        )
        request = ExecutionRequest(
            request_id="req-pandas-oom",
            language_id=LanguageId.PANDAS,
            source_code="import numpy as np\narr = np.ones((10000, 10000), dtype=float)\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.MEMORY_LIMIT)
        self.assertEqual(res.exit_code, 137)
        milestone_names = [m.milestone for m in res.milestones]
        expected = [
            Milestone.ACCEPTED,
            Milestone.RUNNING,
            Milestone.MEMORY_LIMIT,
        ]
        self.assertEqual(milestone_names, expected)

    def test_uninstalled_or_unavailable_pandas_fails_safely(self) -> None:
        adapter = self.registry.get(LanguageId.PANDAS)
        if not adapter or adapter.is_installed:
            self.skipTest("Pandas is installed; skipping fail-closed test")
        request = ExecutionRequest(
            request_id="req-pandas-fail-closed",
            language_id=LanguageId.PANDAS,
            source_code="import pandas as pd\n",
        )
        res = self.pipeline.execute(request)
        self.assertEqual(res.final_status, Milestone.SANDBOX_ERROR)
        self.assertIn("not installed", res.stderr.lower())
        self.assertIn("unavailable", res.stderr.lower())

    def test_pandas_harness_direct_dataframe_serialization(self) -> None:
        adapter = self.registry.get(LanguageId.PANDAS)
        self.assertIsNotNone(adapter)
        assert adapter is not None

        harness_script = adapter.execution_spec.execution_args[2]

        with tempfile.TemporaryDirectory(prefix="pes2-test-pandas-") as tmpdir:
            sol_path = os.path.join(tmpdir, "solution.py")
            with open(sol_path, "w", encoding="utf-8") as f:
                f.write(
                    "import pandas as pd, numpy as np\n"
                    "df = pd.DataFrame({\n"
                    "    'num': [1, 2, 3],\n"
                    "    'name': ['Alice', 'Bob', None],\n"
                    "    'val': [1.5, np.nan, 3.0],\n"
                    "    'active': [True, False, True],\n"
                    "})\n"
                )

            proc = subprocess.run(
                [sys.executable, "-I", "-c", harness_script, sol_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0, f"Harness failed: {proc.stderr}")
            parsed = json.loads(proc.stdout.strip())
            self.assertEqual(parsed["columns"], ["num", "name", "val", "active"])
            self.assertEqual(
                parsed["rows"],
                [
                    [1, "Alice", 1.5, True],
                    [2, "Bob", None, False],
                    [3, None, 3.0, True],
                ],
            )

    def test_pandas_harness_handles_series_and_nulls(self) -> None:
        adapter = self.registry.get(LanguageId.PANDAS)
        self.assertIsNotNone(adapter)
        assert adapter is not None

        harness_script = adapter.execution_spec.execution_args[2]

        with tempfile.TemporaryDirectory(prefix="pes2-test-pandas-series-") as tmpdir:
            sol_path = os.path.join(tmpdir, "solution.py")
            with open(sol_path, "w", encoding="utf-8") as f:
                f.write(
                    "import pandas as pd, numpy as np\n"
                    "s = pd.Series([10, None, 30], name='totals')\n"
                )

            proc = subprocess.run(
                [sys.executable, "-I", "-c", harness_script, sol_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0, f"Harness failed: {proc.stderr}")
            parsed = json.loads(proc.stdout.strip())
            self.assertEqual(parsed["columns"], ["totals"])
            self.assertEqual(parsed["rows"], [[10], [None], [30]])

    def test_pandas_harness_handles_custom_function_or_result_variable(self) -> None:
        adapter = self.registry.get(LanguageId.PANDAS)
        self.assertIsNotNone(adapter)
        assert adapter is not None

        harness_script = adapter.execution_spec.execution_args[2]

        with tempfile.TemporaryDirectory(prefix="pes2-test-pandas-fn-") as tmpdir:
            sol_path = os.path.join(tmpdir, "solution.py")
            with open(sol_path, "w", encoding="utf-8") as f:
                f.write(
                    "import pandas as pd\n"
                    "def solve():\n"
                    "    return pd.DataFrame({'x': ['A', 'B'], 'y': [100, 200]})\n"
                )

            proc = subprocess.run(
                [sys.executable, "-I", "-c", harness_script, sol_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0, f"Harness failed: {proc.stderr}")
            parsed = json.loads(proc.stdout.strip())
            self.assertEqual(parsed["columns"], ["x", "y"])
            self.assertEqual(parsed["rows"], [["A", 100], ["B", 200]])

    def test_react_browser_only_rejected_by_server_pipeline(self) -> None:
        request = ExecutionRequest(
            request_id="req-react-rej",
            language_id=LanguageId.REACT,
            source_code="export default function App() { return <div>Hello React</div>; }\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.SANDBOX_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("Browser-only runtime", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        self.assertIn(Milestone.SANDBOX_ERROR, milestone_names)
        self.assertEqual(len(self.runner.invocations), 0)

    def test_vanilla_js_browser_only_rejected_by_server_pipeline(self) -> None:
        request = ExecutionRequest(
            request_id="req-vjs-rej",
            language_id=LanguageId.VANILLA_JS,
            source_code="const root = document.getElementById('root'); root.textContent = 'Hello DOM';\n",
        )
        res = self.pipeline.execute(request)

        self.assertEqual(res.final_status, Milestone.SANDBOX_ERROR)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("Browser-only runtime", res.stderr)
        milestone_names = [m.milestone for m in res.milestones]
        self.assertIn(Milestone.SANDBOX_ERROR, milestone_names)
        self.assertEqual(len(self.runner.invocations), 0)

    def test_live_pandas_execution_with_native_runner(self) -> None:
        pandas_adapter = self.registry.get(LanguageId.PANDAS)
        if not pandas_adapter or not pandas_adapter.is_installed:
            self.skipTest(f"Pandas toolchain unavailable: {pandas_adapter.installed_version if pandas_adapter else 'None'}")

        runner_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sandbox", "pes2-runner"))
        if not os.path.exists(runner_path):
            self.skipTest("Native runner binary pes2-runner not found")

        cgroup_root = "/sys/fs/cgroup/pes2-runner-pool"
        if not os.path.exists(cgroup_root) or not os.access(cgroup_root, os.W_OK):
            self.skipTest("Cgroup v2 hierarchy unavailable for native runner execution")

        native_runner = NativeSandboxRunner(runner_path=runner_path)
        pipeline = ExecutionPipeline(self.registry, native_runner)

        req = ExecutionRequest(
            request_id="req-live-pandas-df-ok",
            language_id=LanguageId.PANDAS,
            source_code="import pandas as pd\ndf = pd.DataFrame({'a': [1, 2], 'b': [10, 20]})\n",
        )
        res = pipeline.execute(req)
        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        parsed = json.loads(res.stdout.strip())
        self.assertEqual(parsed["columns"], ["a", "b"])
        self.assertEqual(parsed["rows"], [[1, 10], [2, 20]])


if __name__ == "__main__":
    unittest.main()
