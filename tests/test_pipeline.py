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
)
from engine.pipeline import ExecutionPipeline
from engine.registry import RuntimeRegistry
from engine.runner import MockSandboxRunner


class ArtifactCreatingMockRunner(MockSandboxRunner):
    """Simulates realistic compiler behavior by producing output artifact on compile success."""

    def run(self, workspace, target_bin, target_argv, limits, stdin_content=""):
        res = super().run(workspace, target_bin, target_argv, limits, stdin_content)
        # If compilation succeeded, create the expected output binary
        if res.status == "OK" and res.exit_code == 0:
            if target_bin.endswith("gcc") or target_bin.endswith("clang++") or target_bin.endswith("g++"):
                out_path = os.path.join(workspace, "a.out")
                with open(out_path, "w") as f:
                    f.write("#!/bin/sh\n")
                os.chmod(out_path, 0o755)
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
        # Stage 2: artifact bin
        self.assertEqual(self.runner.invocations[1]["target_bin"], "/workspace/a.out")

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
            def run(self, workspace, target_bin, target_argv, limits, stdin_content=""):
                # Simulate a malicious or leftover a.out artifact in workspace
                fake_bin = os.path.join(workspace, "a.out")
                with open(fake_bin, "w") as f:
                    f.write("echo 'malicious stale execution'\n")
                os.chmod(fake_bin, 0o755)
                # But compile fails!
                return super().run(workspace, target_bin, target_argv, limits, stdin_content)

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


if __name__ == "__main__":
    unittest.main()
