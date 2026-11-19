"""Tests for ExecutionPipeline, truthful milestone emissions, and security guarantees."""

import glob
import os
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
from engine.runner import MockSandboxRunner


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
        request = ExecutionRequest(
            request_id="req-unknown",
            language_id=LanguageId.MYSQL,
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

    def test_unsupported_phase5_runtimes_fail_closed(self) -> None:
        unsupported = [
            LanguageId.PYTHON2,
            LanguageId.SWIFT,
            LanguageId.RACKET,
            LanguageId.ERLANG,
            LanguageId.ELIXIR,
            LanguageId.CSHARP,
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


if __name__ == "__main__":
    unittest.main()
