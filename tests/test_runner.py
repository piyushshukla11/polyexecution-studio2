"""Tests for NativeSandboxRunner parsing and command assembly."""

import unittest

from engine.models import ResourceLimits
from engine.runner import NativeSandboxRunner


class TestNativeSandboxRunner(unittest.TestCase):
    def test_missing_binary_returns_sandbox_error(self) -> None:
        runner = NativeSandboxRunner(runner_path="/tmp/nonexistent-pes2-runner-bin")
        res = runner.run(
            workspace="/tmp",
            target_bin="/bin/true",
            target_argv=[],
            limits=ResourceLimits(),
        )
        self.assertEqual(res.status, "SANDBOX_ERROR")
        self.assertEqual(res.exit_code, 127)
        self.assertIn("not found", res.stderr)

    def test_parse_pes2_result_pattern(self) -> None:
        raw_stderr = (
            "compiling something...\n"
            "PES2_RESULT status=OK exit=0 signal=0 wall_ms=25 cpu_ms=15 peak_memory_kb=8192 stdout_bytes=12 stderr_bytes=0\n"
        )
        # Mock subprocess execution or check regex parsing
        from engine.runner import PES2_RESULT_PATTERN
        match = PES2_RESULT_PATTERN.search(raw_stderr)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group(1), "OK")
        self.assertEqual(int(match.group(2)), 0)
        self.assertEqual(int(match.group(3)), 0)
        self.assertEqual(int(match.group(4)), 25)
        self.assertEqual(int(match.group(5)), 15)
        self.assertEqual(int(match.group(6)), 8192)

    def test_parse_signal_and_nonzero_exit(self) -> None:
        raw_stderr = "PES2_RESULT status=MEMORY_LIMIT exit=137 signal=9 wall_ms=100 cpu_ms=80 peak_memory_kb=65536 stdout_bytes=0 stderr_bytes=10\n"
        from engine.runner import PES2_RESULT_PATTERN
        match = PES2_RESULT_PATTERN.search(raw_stderr)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group(1), "MEMORY_LIMIT")
        self.assertEqual(int(match.group(2)), 137)
        self.assertEqual(int(match.group(3)), 9)


if __name__ == "__main__":
    unittest.main()
