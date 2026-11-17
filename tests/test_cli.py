"""Tests for engine.cli command-line execution interface."""

import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from engine.cli import main
from engine.models import (
    MAX_SOURCE_BYTES,
    RUNNER_MAX_MEMORY_KB,
    RUNNER_MAX_PIDS,
    RUNNER_MAX_TIMEOUT_MS,
    RUNNER_MIN_MEMORY_KB,
    RUNNER_MIN_PIDS,
    RUNNER_MIN_TIMEOUT_MS,
)
from engine.pipeline import ExecutionPipeline


class TestCLI(unittest.TestCase):
    def test_cli_mock_python3_success(self) -> None:
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        args = [
            "--language", "python3",
            "--source", "print('hello')",
            "--runner", "mock",
        ]
        with patch("sys.stdout", stdout_capture), patch("sys.stderr", stderr_capture):
            exit_code = main(args)

        self.assertEqual(exit_code, 0)
        output = json.loads(stdout_capture.getvalue())
        self.assertEqual(output["final_status"], "COMPLETED")
        self.assertEqual(output["language_id"], "python3")
        milestones = [m["milestone"] for m in output["milestones"]]
        self.assertIn("ACCEPTED", milestones)
        self.assertIn("RUNNING", milestones)
        self.assertNotIn("COMPILING", milestones)

    def test_cli_source_file_argument(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            tf.write("print('from file')")
            tf_path = tf.name

        try:
            stdout_capture = io.StringIO()
            args = [
                "--language", "python3",
                "--source-file", tf_path,
                "--runner", "mock",
            ]
            with patch("sys.stdout", stdout_capture):
                exit_code = main(args)

            self.assertEqual(exit_code, 0)
            output = json.loads(stdout_capture.getvalue())
            self.assertEqual(output["final_status"], "COMPLETED")
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_cli_invalid_language_fails(self) -> None:
        stderr_capture = io.StringIO()
        args = ["--language", "invalid_lang_xyz", "--source", "hello"]
        with patch("sys.stderr", stderr_capture):
            exit_code = main(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("Unknown or unsupported language", stderr_capture.getvalue())

    def test_cli_missing_source_fails(self) -> None:
        stderr_capture = io.StringIO()
        args = ["--language", "python3"]
        with patch("sys.stderr", stderr_capture), patch("sys.stdin.isatty", return_value=True):
            exit_code = main(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("Either --source, --source-file, or standard input must be provided", stderr_capture.getvalue())

    def test_cli_oversized_source_file_rejected(self) -> None:
        with tempfile.NamedTemporaryFile("wb", delete=False) as tf:
            tf.write(b"x" * (MAX_SOURCE_BYTES + 5))
            tf_path = tf.name

        try:
            stderr_capture = io.StringIO()
            args = [
                "--language", "python3",
                "--source-file", tf_path,
                "--runner", "mock",
            ]
            with patch("sys.stderr", stderr_capture):
                exit_code = main(args)

            self.assertEqual(exit_code, 1)
            self.assertIn("exceeds maximum allowed size", stderr_capture.getvalue())
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_cli_oversized_stdin_rejected(self) -> None:
        oversized_data = b"print('a')\n" + (b" " * (MAX_SOURCE_BYTES + 10))
        fake_stdin = io.BytesIO(oversized_data)
        stderr_capture = io.StringIO()

        args = [
            "--language", "python3",
            "--runner", "mock",
        ]
        with patch("sys.stdin", fake_stdin), patch("sys.stdin.isatty", return_value=False), patch("sys.stderr", stderr_capture):
            exit_code = main(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("exceeds maximum allowed size", stderr_capture.getvalue())

    def test_cli_invalid_utf8_file_rejected(self) -> None:
        with tempfile.NamedTemporaryFile("wb", delete=False) as tf:
            tf.write(b"\x80\x81\xff\xfe")
            tf_path = tf.name

        try:
            stderr_capture = io.StringIO()
            args = [
                "--language", "python3",
                "--source-file", tf_path,
                "--runner", "mock",
            ]
            with patch("sys.stderr", stderr_capture):
                exit_code = main(args)

            self.assertEqual(exit_code, 1)
            self.assertIn("Invalid UTF-8 encoding", stderr_capture.getvalue())
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_cli_invalid_utf8_stdin_rejected(self) -> None:
        fake_stdin = io.BytesIO(b"\xff\xfe\xfa")
        stderr_capture = io.StringIO()

        args = [
            "--language", "python3",
            "--runner", "mock",
        ]
        with patch("sys.stdin", fake_stdin), patch("sys.stdin.isatty", return_value=False), patch("sys.stderr", stderr_capture):
            exit_code = main(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("Invalid UTF-8 encoding", stderr_capture.getvalue())

    def test_cli_invalid_wall_ms_rejected(self) -> None:
        for bad_val in [-5, 0, RUNNER_MAX_TIMEOUT_MS + 1]:
            stderr_capture = io.StringIO()
            args = [
                "--language", "python3",
                "--source", "print(1)",
                "--wall-ms", str(bad_val),
            ]
            with patch("sys.stderr", stderr_capture):
                exit_code = main(args)
            self.assertEqual(exit_code, 1)
            self.assertIn("Invalid wall-ms", stderr_capture.getvalue())

    def test_cli_invalid_memory_kb_rejected(self) -> None:
        for bad_val in [RUNNER_MIN_MEMORY_KB - 1, RUNNER_MAX_MEMORY_KB + 1]:
            stderr_capture = io.StringIO()
            args = [
                "--language", "python3",
                "--source", "print(1)",
                "--memory-kb", str(bad_val),
            ]
            with patch("sys.stderr", stderr_capture):
                exit_code = main(args)
            self.assertEqual(exit_code, 1)
            self.assertIn("Invalid memory-kb", stderr_capture.getvalue())

    def test_cli_invalid_pids_rejected(self) -> None:
        for bad_val in [0, -1, RUNNER_MAX_PIDS + 1]:
            stderr_capture = io.StringIO()
            args = [
                "--language", "python3",
                "--source", "print(1)",
                "--pids", str(bad_val),
            ]
            with patch("sys.stderr", stderr_capture):
                exit_code = main(args)
            self.assertEqual(exit_code, 1)
            self.assertIn("Invalid pids", stderr_capture.getvalue())

    def test_cli_invalid_max_output_bytes_rejected(self) -> None:
        for bad_val in [0, -10, 65537]:
            stderr_capture = io.StringIO()
            args = [
                "--language", "python3",
                "--source", "print(1)",
                "--max-output-bytes", str(bad_val),
            ]
            with patch("sys.stderr", stderr_capture):
                exit_code = main(args)
            self.assertEqual(exit_code, 1)
            self.assertIn("Invalid max-output-bytes", stderr_capture.getvalue())

    def test_cli_preserves_adapter_defaults_on_single_override(self) -> None:
        # Override only wall-ms for python3
        captured_request = None
        orig_execute = ExecutionPipeline.execute

        def capture_execute(pipeline_self, req):
            nonlocal captured_request
            captured_request = req
            return orig_execute(pipeline_self, req)

        args = [
            "--language", "python3",
            "--source", "print('test')",
            "--runner", "mock",
            "--wall-ms", "4500",
        ]
        stdout_capture = io.StringIO()
        with patch.object(ExecutionPipeline, "execute", capture_execute), patch("sys.stdout", stdout_capture):
            exit_code = main(args)

        self.assertEqual(exit_code, 0)
        self.assertIsNotNone(captured_request)
        assert captured_request is not None
        self.assertIsNotNone(captured_request.custom_limits)
        limits = captured_request.custom_limits
        assert limits is not None

        # wall-ms was overridden
        self.assertEqual(limits.wall_ms, 4500)
        # Unspecified limits must match Python 3 adapter defaults:
        # cpu_ms: 2000, memory_kb: 131072 (128 MiB), pids: 16, max_output_bytes: 65536
        self.assertEqual(limits.cpu_ms, 2000)
        self.assertEqual(limits.memory_kb, 131072)
        self.assertEqual(limits.pids, 16)
        self.assertEqual(limits.max_output_bytes, 65536)


if __name__ == "__main__":
    unittest.main()
