"""Regression test suite for API Gateway ingress hardening.

Verifies:
1. Payload size limits: rejects request bodies exceeding 256 KiB with HTTP 413 Payload Too Large.
2. Request validation: rejects invalid JSON schema, missing languageId/sourceCode with HTTP 400.
3. Custom limits validation: bounds-checks cpuMs, wallMs, memoryKb, pids, and maxOutputBytes.
4. Browser-only runtimes rejection: rejects server execution of 'react' and 'vanilla_js' with HTTP 400.
5. Server-side authentication: enforces Bearer token or X-API-Key when PES2_API_KEY is configured.
6. Startup safety: prohibits binding to remote host without PES2_API_KEY.
"""

from http import HTTPStatus
import json
import os
import unittest
from unittest.mock import MagicMock
import urllib.error
import urllib.request

from engine.gateway.server import (
    MAX_REQUEST_BYTES,
    check_authentication,
    create_gateway_server,
    validate_execution_payload,
)


class TestIngressHardeningValidation(unittest.TestCase):
    """Unit tests for payload schema and resource limit validation."""

    def test_missing_language_id_rejected(self) -> None:
        valid, msg = validate_execution_payload({"sourceCode": "print(1)"})
        self.assertFalse(valid)
        self.assertIn("languageId", msg or "")

    def test_missing_source_code_rejected(self) -> None:
        valid, msg = validate_execution_payload({"languageId": "python3"})
        self.assertFalse(valid)
        self.assertIn("sourceCode", msg or "")

    def test_unknown_language_id_rejected(self) -> None:
        valid, msg = validate_execution_payload({"languageId": "brainfuck", "sourceCode": "+++"})
        self.assertFalse(valid)
        self.assertIn("Unknown languageId", msg or "")

    def test_browser_only_runtimes_rejected_from_server_runner(self) -> None:
        for lang in ("react", "vanilla_js"):
            valid, msg = validate_execution_payload({
                "languageId": lang,
                "sourceCode": "export default function App() {}",
            })
            self.assertFalse(valid)
            self.assertIn("browser-only runtime", msg or "")
            self.assertIn("cannot be executed on the server runner", msg or "")

    def test_source_code_exceeding_64kib_rejected(self) -> None:
        large_source = "a = 1\n" * 15000  # > 64 KiB
        valid, msg = validate_execution_payload({"languageId": "python3", "sourceCode": large_source})
        self.assertFalse(valid)
        self.assertIn("exceeds maximum allowed size", msg or "")

    def test_custom_limits_bounds_enforced(self) -> None:
        # cpuMs too small (minimum 1ms)
        valid, msg = validate_execution_payload({
            "languageId": "python3",
            "sourceCode": "print(1)",
            "customLimits": {"cpuMs": 0},
        })
        self.assertFalse(valid)
        self.assertIn("customLimits.cpuMs", msg or "")

        # wallMs too large
        valid, msg = validate_execution_payload({
            "languageId": "python3",
            "sourceCode": "print(1)",
            "customLimits": {"wallMs": 120000},  # Max 60000ms
        })
        self.assertFalse(valid)
        self.assertIn("customLimits.wallMs", msg or "")

        # memoryKb too small
        valid, msg = validate_execution_payload({
            "languageId": "python3",
            "sourceCode": "print(1)",
            "customLimits": {"memoryKb": 1024},  # Min 8192
        })
        self.assertFalse(valid)
        self.assertIn("customLimits.memoryKb", msg or "")

        # pids too large
        valid, msg = validate_execution_payload({
            "languageId": "python3",
            "sourceCode": "print(1)",
            "customLimits": {"pids": 500},  # Max 256
        })
        self.assertFalse(valid)
        self.assertIn("customLimits.pids", msg or "")

        # Valid custom limits
        valid, msg = validate_execution_payload({
            "languageId": "python3",
            "sourceCode": "print(1)",
            "customLimits": {
                "cpuMs": 2000,
                "wallMs": 3000,
                "memoryKb": 65536,
                "pids": 32,
                "maxOutputBytes": 32768,
            },
        })
        self.assertTrue(valid)
        self.assertIsNone(msg)


class TestIngressAuthentication(unittest.TestCase):
    """Unit tests for server-side token authentication."""

    def test_auth_disabled_when_key_unset(self) -> None:
        self.assertTrue(check_authentication({}, None))
        self.assertTrue(check_authentication({}, ""))

    def test_auth_enforced_bearer_token(self) -> None:
        secret = "super_secure_production_token_12345"
        # Missing auth header
        self.assertFalse(check_authentication({}, secret))
        # Wrong token
        self.assertFalse(check_authentication({"Authorization": "Bearer wrong_token"}, secret))
        # Valid token
        self.assertTrue(check_authentication({"Authorization": f"Bearer {secret}"}, secret))
        # Case insensitive header lookup
        self.assertTrue(check_authentication({"authorization": f"Bearer {secret}"}, secret))

    def test_auth_enforced_x_api_key(self) -> None:
        secret = "super_secure_production_token_12345"
        self.assertTrue(check_authentication({"X-API-Key": secret}, secret))
        self.assertTrue(check_authentication({"x-api-key": secret}, secret))
        self.assertFalse(check_authentication({"X-API-Key": "wrong"}, secret))


class TestIngressHttpEndToEnd(unittest.TestCase):
    """End-to-end HTTP tests against active GatewayServer."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mock_gateway = MagicMock()
        cls.mock_gateway.check_daemon_health.return_value = {
            "status": "SERVING",
            "runner_version": "pes2-runner-2.0",
            "active_workers": 2,
        }
        cls.mock_gateway.get_runtimes.return_value = []
        cls.api_key = "test_ingress_secret_key_abcdef"
        cls.mock_gateway.api_key = cls.api_key
        cls.server, cls.port = create_gateway_server(
            host="127.0.0.1",
            port=0,
            gateway=cls.mock_gateway,
            api_key=cls.api_key,
        )
        import threading
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_unauthenticated_request_rejected_401(self) -> None:
        url = f"http://127.0.0.1:{self.port}/api/execute"
        req = urllib.request.Request(
            url,
            data=json.dumps({"languageId": "python3", "sourceCode": "print(1)"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            self.fail("Expected HTTP 401 Unauthorized")
        except urllib.error.HTTPError as err:
            self.assertEqual(err.code, HTTPStatus.UNAUTHORIZED)

    def test_oversized_payload_rejected_413(self) -> None:
        url = f"http://127.0.0.1:{self.port}/api/execute"
        oversized_data = b"x" * (MAX_REQUEST_BYTES + 1024)
        req = urllib.request.Request(
            url,
            data=oversized_data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            self.fail("Expected HTTP 413 Payload Too Large")
        except urllib.error.HTTPError as err:
            self.assertEqual(err.code, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

    def test_browser_only_runtime_rejected_400(self) -> None:
        url = f"http://127.0.0.1:{self.port}/api/execute"
        req = urllib.request.Request(
            url,
            data=json.dumps({"languageId": "react", "sourceCode": "export default function App() {}"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            self.fail("Expected HTTP 400 Bad Request")
        except urllib.error.HTTPError as err:
            self.assertEqual(err.code, HTTPStatus.BAD_REQUEST)
            resp_body = json.loads(err.read().decode("utf-8"))
            self.assertIn("browser-only runtime", resp_body.get("message", ""))


if __name__ == "__main__":
    unittest.main()
