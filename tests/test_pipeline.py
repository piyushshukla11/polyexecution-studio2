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

    def test_unsupported_phase5_runtimes_fail_closed(self) -> None:
        unsupported = [
            LanguageId.JAVASCRIPT,
            LanguageId.TYPESCRIPT,
            LanguageId.PHP,
            LanguageId.PYTHON2,
            LanguageId.DART,
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

        # 6. Verify zero cgroup leftovers
        leftovers = [
            d for d in os.listdir(cgroup_root)
            if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
        ]
        self.assertEqual(len(leftovers), 0)


if __name__ == "__main__":
    unittest.main()
