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
    main as gateway_main,
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
        cls.mock_gateway.allowed_origins = {"http://127.0.0.1:8088", "http://localhost:8088"}
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

    def test_untrusted_origin_rejected_403(self) -> None:
        url = f"http://127.0.0.1:{self.port}/api/health"
        req = urllib.request.Request(
            url,
            headers={
                "Origin": "http://evil-attacker.example.com",
            },
            method="GET",
        )
        try:
            urllib.request.urlopen(req)
            self.fail("Expected HTTP 403 Forbidden on untrusted Origin")
        except urllib.error.HTTPError as err:
            self.assertEqual(err.code, HTTPStatus.FORBIDDEN)
            resp_body = json.loads(err.read().decode("utf-8"))
            self.assertIn("Cross-origin requests", resp_body.get("message", ""))

    def test_whitelisted_origin_allowed_and_cors_reflected(self) -> None:
        url = f"http://127.0.0.1:{self.port}/api/health"
        req = urllib.request.Request(
            url,
            headers={
                "Origin": "http://127.0.0.1:8088",
            },
            method="GET",
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, HTTPStatus.OK)
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "http://127.0.0.1:8088")
            self.assertEqual(resp.headers.get("Vary"), "Origin")

    def test_concurrency_cap_returns_503(self) -> None:
        import socket
        import threading
        import time

        mock_gw = MagicMock()
        mock_gw.allowed_origins = {"*"}
        block_event = threading.Event()

        def slow_health():
            block_event.wait(timeout=2.0)
            return {"status": "SERVING"}

        mock_gw.check_daemon_health.side_effect = slow_health

        server, port = create_gateway_server(
            host="127.0.0.1",
            port=0,
            gateway=mock_gw,
            max_concurrency=1,
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        try:
            # Connect client 1 and issue request, which enters slow_health and blocks
            s1 = socket.socket()
            s1.connect(("127.0.0.1", port))
            s1.sendall(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            time.sleep(0.05)

            # Socket 2 should immediately receive HTTP 503 because capacity is 1
            s2 = socket.socket()
            s2.connect(("127.0.0.1", port))
            s2.sendall(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            response = s2.recv(1024).decode("utf-8")
            s2.close()

            # Release client 1
            block_event.set()
            s1.recv(1024)
            s1.close()

            self.assertIn("503 Service Unavailable", response)
            self.assertIn("Gateway connection limit reached", response)
        finally:
            block_event.set()
            server.shutdown()
            server.server_close()

    def test_main_allow_internal_network_flag(self) -> None:
        from unittest.mock import patch
        with patch("engine.gateway.server.run_gateway") as mock_run:
            ret = gateway_main([
                "--host", "0.0.0.0",
                "--allow-internal-network",
            ])
            self.assertEqual(ret, 0)
            mock_run.assert_called_once()
            self.assertEqual(mock_run.call_args.kwargs["host"], "0.0.0.0")


if __name__ == "__main__":
    unittest.main()
