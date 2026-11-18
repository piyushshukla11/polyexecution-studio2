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

    def test_cmd_includes_seccomp_profile_compiler(self) -> None:
        from unittest.mock import patch, MagicMock
        from engine.models import SeccompPolicyProfile

        runner = NativeSandboxRunner(runner_path="/bin/true")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (
            "",
            "PES2_RESULT status=OK exit=0 signal=0 wall_ms=10 cpu_ms=5 peak_memory_kb=1024 stdout_bytes=0 stderr_bytes=0\n",
        )

        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            res = runner.run(
                workspace="/tmp",
                target_bin="/bin/true",
                target_argv=["foo"],
                limits=ResourceLimits(),
                seccomp_profile=SeccompPolicyProfile.COMPILER,
            )
            self.assertEqual(res.status, "OK")
            self.assertTrue(mock_popen.called)
            cmd = mock_popen.call_args[0][0]
            self.assertIn("--seccomp-profile", cmd)
            idx = cmd.index("--seccomp-profile")
            self.assertEqual(cmd[idx + 1], "compiler")

    def test_cmd_includes_seccomp_profile_strict_and_python(self) -> None:
        from unittest.mock import patch, MagicMock
        from engine.models import SeccompPolicyProfile

        runner = NativeSandboxRunner(runner_path="/bin/true")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (
            "",
            "PES2_RESULT status=OK exit=0 signal=0 wall_ms=10 cpu_ms=5 peak_memory_kb=1024 stdout_bytes=0 stderr_bytes=0\n",
        )

        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            runner.run(
                workspace="/tmp",
                target_bin="/bin/true",
                target_argv=["foo"],
                limits=ResourceLimits(),
                seccomp_profile=SeccompPolicyProfile.STRICT_NATIVE,
            )
            cmd = mock_popen.call_args[0][0]
            idx = cmd.index("--seccomp-profile")
            self.assertEqual(cmd[idx + 1], "strict")

            runner.run(
                workspace="/tmp",
                target_bin="/bin/true",
                target_argv=["foo"],
                limits=ResourceLimits(),
                seccomp_profile=SeccompPolicyProfile.BASH,
            )
            cmd = mock_popen.call_args[0][0]
            idx = cmd.index("--seccomp-profile")
            self.assertEqual(cmd[idx + 1], "bash")

    def test_cancel_token_terminates_process(self) -> None:
        import threading
        import time
        from unittest.mock import patch, MagicMock

        runner = NativeSandboxRunner(runner_path="/bin/true")
        mock_proc = MagicMock()
        mock_proc.returncode = 130
        cancel_token = threading.Event()

        def fake_communicate(*args, **kwargs):
            for _ in range(50):
                if mock_proc.terminate.called:
                    break
                time.sleep(0.01)
            return (
                "",
                "PES2_RESULT status=CANCELLED exit=0 signal=0 wall_ms=50 cpu_ms=10 peak_memory_kb=1024 stdout_bytes=0 stderr_bytes=0\n",
            )

        mock_proc.communicate.side_effect = fake_communicate

        with patch("subprocess.Popen", return_value=mock_proc):
            cancel_token.set()
            res = runner.run(
                workspace="/tmp",
                target_bin="/bin/true",
                target_argv=[],
                limits=ResourceLimits(),
                cancel_token=cancel_token,
            )
            self.assertEqual(res.status, "CANCELLED")
            self.assertEqual(res.exit_code, 0)
            self.assertEqual(res.signal, 0)
            self.assertTrue(mock_proc.terminate.called)


if __name__ == "__main__":
    unittest.main()
