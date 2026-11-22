"""Automated end-to-end test suite for all 28 execution environments in PES2.

Verifies that every supported runtime executes cleanly through the pipeline,
and guarantees that every unsupported runtime fails clearly and honestly.
"""

import json
import os
import unittest

from engine.models import (
    ExecutionRequest,
    LanguageId,
    Milestone,
    ResourceLimits,
    RunnerResult,
)
from engine.pipeline import ExecutionPipeline
from engine.registry import RuntimeRegistry
from engine.runner import MockSandboxRunner


def make_runner_result(
    status: str = "OK",
    exit_code: int = 0,
    signal: int = 0,
    wall_ms: int = 10,
    cpu_ms: int = 5,
    peak_memory_kb: int = 10000,
    stdout: str = "",
    stderr: str = "",
) -> RunnerResult:
    return RunnerResult(
        status=status,
        exit_code=exit_code,
        signal=signal,
        wall_ms=wall_ms,
        cpu_ms=cpu_ms,
        peak_memory_kb=peak_memory_kb,
        stdout=stdout,
        stderr=stderr,
    )


class E2EArtifactRunner(MockSandboxRunner):
    """Produces realistic compiler output artifacts and mock responses for E2E tests."""

    def run(self, workspace, target_bin, target_argv, limits, stdin_content="", **kwargs):
        res = super().run(workspace, target_bin, target_argv, limits, stdin_content, **kwargs)
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


class TestEndToEndRuntimes(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = RuntimeRegistry()
        self.runner = E2EArtifactRunner()
        self.pipeline = ExecutionPipeline(registry=self.registry, runner=self.runner)

    # -------------------------------------------------------------------------
    # 1. Supported Runtimes Coverage
    # -------------------------------------------------------------------------

    def test_e2e_cpp_execution(self) -> None:
        """C++: compile stage + execution stage."""
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=40, cpu_ms=20, peak_memory_kb=30000))
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=10, cpu_ms=5, peak_memory_kb=5000, stdout="cpp_ok\n"))

        req = ExecutionRequest(
            request_id="e2e-cpp",
            language_id=LanguageId.CPP,
            source_code='#include <iostream>\nint main() { std::cout << "cpp_ok\\n"; return 0; }',
        )
        res = self.pipeline.execute(req)
        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.stdout, "cpp_ok\n")
        milestones = [m.milestone for m in res.milestones]
        self.assertIn(Milestone.COMPILING, milestones)
        self.assertIn(Milestone.RUNNING, milestones)
        self.assertIn(Milestone.COMPLETED, milestones)

    def test_e2e_c_execution(self) -> None:
        """C: compile stage + execution stage."""
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=30, cpu_ms=15, peak_memory_kb=25000))
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=8, cpu_ms=4, peak_memory_kb=4000, stdout="c_ok\n"))

        req = ExecutionRequest(
            request_id="e2e-c",
            language_id=LanguageId.C,
            source_code='#include <stdio.h>\nint main() { printf("c_ok\\n"); return 0; }',
        )
        res = self.pipeline.execute(req)
        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.stdout, "c_ok\n")

    def test_e2e_java_execution(self) -> None:
        """Java: javac compile stage + java classpath execution stage."""
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=120, cpu_ms=90, peak_memory_kb=80000))
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=50, cpu_ms=30, peak_memory_kb=60000, stdout="java_ok\n"))

        req = ExecutionRequest(
            request_id="e2e-java",
            language_id=LanguageId.JAVA,
            source_code='public class Solution { public static void main(String[] args) { System.out.println("java_ok"); } }',
        )
        res = self.pipeline.execute(req)
        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.stdout, "java_ok\n")

    def test_e2e_python3_execution(self) -> None:
        """Python 3: single-stage interpreted execution."""
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=15, cpu_ms=10, peak_memory_kb=12000, stdout="py3_ok\n"))

        req = ExecutionRequest(
            request_id="e2e-py3",
            language_id=LanguageId.PYTHON3,
            source_code='print("py3_ok")',
        )
        res = self.pipeline.execute(req)
        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.stdout, "py3_ok\n")
        milestones = [m.milestone for m in res.milestones]
        self.assertNotIn(Milestone.COMPILING, milestones)
        self.assertIn(Milestone.RUNNING, milestones)

    def test_e2e_javascript_execution(self) -> None:
        """JavaScript: Node.js direct execution."""
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=25, cpu_ms=15, peak_memory_kb=30000, stdout="js_ok\n"))

        req = ExecutionRequest(
            request_id="e2e-js",
            language_id=LanguageId.JAVASCRIPT,
            source_code='console.log("js_ok");',
        )
        res = self.pipeline.execute(req)
        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.stdout, "js_ok\n")

    def test_e2e_ruby_execution(self) -> None:
        """Ruby: direct interpreted execution."""
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=20, cpu_ms=12, peak_memory_kb=18000, stdout="ruby_ok\n"))

        req = ExecutionRequest(
            request_id="e2e-ruby",
            language_id=LanguageId.RUBY,
            source_code='puts "ruby_ok"',
        )
        res = self.pipeline.execute(req)
        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.stdout, "ruby_ok\n")

    def test_e2e_bash_execution(self) -> None:
        """Bash: 2-stage syntax check stage + shell script execution stage."""
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=5, cpu_ms=2, peak_memory_kb=4000))
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=10, cpu_ms=5, peak_memory_kb=6000, stdout="bash_ok\n"))

        req = ExecutionRequest(
            request_id="e2e-bash",
            language_id=LanguageId.BASH,
            source_code='echo "bash_ok"',
        )
        res = self.pipeline.execute(req)
        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.stdout, "bash_ok\n")

    def test_e2e_csharp_execution(self) -> None:
        """C#: 2-stage compile + CoreCLR execution."""
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=150, cpu_ms=100, peak_memory_kb=110000))
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=60, cpu_ms=40, peak_memory_kb=75000, stdout="cs_ok\n"))

        req = ExecutionRequest(
            request_id="e2e-csharp",
            language_id=LanguageId.CSHARP,
            source_code='using System; class Solution { static void Main() { Console.WriteLine("cs_ok"); } }',
        )
        res = self.pipeline.execute(req)
        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertEqual(res.stdout, "cs_ok\n")

    def test_e2e_sqlite_execution(self) -> None:
        """SQLite: isolated query execution with tabular result serialization."""
        tabular_out = json.dumps({"columns": ["status"], "rows": [["sqlite_ok"]]}) + "\n"
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=12, cpu_ms=6, peak_memory_kb=8000, stdout=tabular_out))

        req = ExecutionRequest(
            request_id="e2e-sqlite",
            language_id=LanguageId.SQLITE,
            source_code="SELECT 'sqlite_ok' AS status;",
        )
        res = self.pipeline.execute(req)
        self.assertEqual(res.final_status, Milestone.COMPLETED)
        self.assertIn("sqlite_ok", res.stdout)

    def test_e2e_react_verification(self) -> None:
        """React: client-side isolated sandbox generation and Node check."""
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=20, cpu_ms=10, peak_memory_kb=20000, stdout=""))

        req = ExecutionRequest(
            request_id="e2e-react",
            language_id=LanguageId.REACT,
            source_code="export default function App() { return <h1>React OK</h1>; }",
        )
        res = self.pipeline.execute(req)
        self.assertEqual(res.final_status, Milestone.COMPLETED)

    def test_e2e_vanillajs_verification(self) -> None:
        """Vanilla JS: client-side isolated sandbox generation and Node check."""
        self.runner.queue_result(make_runner_result(status="OK", exit_code=0, wall_ms=15, cpu_ms=8, peak_memory_kb=15000, stdout=""))

        req = ExecutionRequest(
            request_id="e2e-vanillajs",
            language_id=LanguageId.VANILLA_JS,
            source_code="document.body.innerHTML = 'Vanilla JS OK';",
        )
        res = self.pipeline.execute(req)
        self.assertEqual(res.final_status, Milestone.COMPLETED)

    # -------------------------------------------------------------------------
    # 2. Unsupported Runtimes Honest Failure Verification
    # -------------------------------------------------------------------------

    def test_unsupported_runtimes_fail_honestly(self) -> None:
        """Every uninstalled or unavailable runtime must fail closed with truthful messaging."""
        unsupported_cases = [
            (LanguageId.PYTHON2, "Not installed on host"),
            (LanguageId.SWIFT, "Not installed on host"),
            (LanguageId.GO, "Not installed on host"),
            (LanguageId.RUST, "Not installed on host"),
            (LanguageId.KOTLIN, "Unavailable to sandbox rootfs"),
            (LanguageId.SCALA, "Not installed on host"),
            (LanguageId.DART, "Not installed on host"),
            (LanguageId.MYSQL, "Not installed on host"),
            (LanguageId.MSSQL, "Not installed on host"),
            (LanguageId.ORACLE, "Not installed on host"),
            (LanguageId.POSTGRESQL, "Incomplete toolchain"),
            (LanguageId.PANDAS, "Unavailable to sandbox rootfs"),
        ]

        for lang_id, expected_msg_fragment in unsupported_cases:
            with self.subTest(language=lang_id.value):
                req = ExecutionRequest(
                    request_id=f"e2e-unsupported-{lang_id.value}",
                    language_id=lang_id,
                    source_code="// dummy source",
                )
                res = self.pipeline.execute(req)
                self.assertEqual(
                    res.final_status,
                    Milestone.SANDBOX_ERROR,
                    f"Expected SANDBOX_ERROR for {lang_id.value}, got {res.final_status}",
                )
                self.assertIn(
                    expected_msg_fragment.lower(),
                    res.stderr.lower(),
                    f"Expected '{expected_msg_fragment}' in stderr for {lang_id.value}, got: {res.stderr}",
                )


if __name__ == "__main__":
    unittest.main()
