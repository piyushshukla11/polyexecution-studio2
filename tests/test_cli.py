"""Tests for engine.cli command-line execution interface."""

import io
import json
import tempfile
import unittest
from unittest.mock import patch

from engine.cli import main


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

    def test_cli_unsupported_max_output_fails(self) -> None:
        stdout_capture = io.StringIO()
        args = [
            "--language", "python3",
            "--source", "print('hi')",
            "--runner", "mock",
            "--max-output-bytes", "99999",
        ]
        with patch("sys.stdout", stdout_capture):
            exit_code = main(args)

        self.assertEqual(exit_code, 1)
        output = json.loads(stdout_capture.getvalue())
        self.assertEqual(output["final_status"], "SANDBOX_ERROR")
        self.assertIn("exceeds runner cap", output["stderr"])


if __name__ == "__main__":
    unittest.main()
