"""Unit and integration tests for PolyExecution Studio 2 API Gateway."""

import json
from http import HTTPStatus
import threading
import time
import unittest
import urllib.request
import urllib.error

import grpc

from engine.daemon.server import create_server
from engine.gateway.server import ExecutionGateway, create_gateway_server, proto_result_to_dict
from engine.models import LanguageId, Milestone, ResourceLimits, RunnerResult
from engine.pipeline import ExecutionPipeline
from engine.registry import RuntimeRegistry
from engine.runner import MockSandboxRunner
from proto import execution_pb2


class GatewayMockRunner(MockSandboxRunner):
    """Produces mock output artifacts for tests."""

    def run(self, workspace, target_bin, target_argv, limits, stdin_content="", **kwargs):
        res = super().run(workspace, target_bin, target_argv, limits, stdin_content, **kwargs)
        return res


class TestApiGateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Start in-process gRPC daemon inside its own asyncio event loop
        cls.registry = RuntimeRegistry()
        cls.runner = GatewayMockRunner()
        cls.pipeline = ExecutionPipeline(registry=cls.registry, runner=cls.runner)

        cls.daemon_server = None
        cls.daemon_port = None
        cls.daemon_loop = None
        cls.daemon_ready = threading.Event()
        cls.daemon_thread = threading.Thread(target=cls._run_daemon, daemon=True)
        cls.daemon_thread.start()
        cls.daemon_ready.wait(timeout=5.0)
        time.sleep(0.3)

        # Start API Gateway HTTP server
        cls.gateway_server, cls.gateway_port = create_gateway_server(
            host="127.0.0.1",
            port=0,
            daemon_addr=f"127.0.0.1:{cls.daemon_port}",
            timeout=10.0,
        )
        cls.gateway_thread = threading.Thread(target=cls.gateway_server.serve_forever, daemon=True)
        cls.gateway_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.gateway_port}"
        time.sleep(0.2)

    @classmethod
    def _run_daemon(cls) -> None:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        cls.daemon_loop = loop

        cls.daemon_server, cls.daemon_port = create_server(
            host="127.0.0.1",
            port=0,
            max_workers=4,
            max_capacity=8,
            max_memory_kb=1048576,
            pipeline=cls.pipeline,
        )
        cls.daemon_ready.set()
        loop.run_until_complete(cls.daemon_server.start())
        loop.run_until_complete(cls.daemon_server.wait_for_termination())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.gateway_server.shutdown()
        cls.gateway_server.server_close()
        cls.gateway_server.gateway.close()
        if cls.daemon_loop:
            import asyncio
            asyncio.run_coroutine_threadsafe(cls.daemon_server.stop(grace=0.5), cls.daemon_loop)

    def test_health_check_endpoint(self) -> None:
        """GET /api/health should report SERVING and runner version."""
        url = f"{self.base_url}/api/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, HTTPStatus.OK)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["status"], "SERVING")
            self.assertIn("pes2-runner", data["runner_version"])
            self.assertGreaterEqual(data["active_workers"], 0)

    def test_runtimes_endpoint(self) -> None:
        """GET /api/runtimes should return all registered environments."""
        url = f"{self.base_url}/api/runtimes"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, HTTPStatus.OK)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("runtimes", data)
            self.assertGreaterEqual(len(data["runtimes"]), 27)

            # Check Python 3 runtime
            py3 = next((r for r in data["runtimes"] if r["id"] == "python3"), None)
            self.assertIsNotNone(py3)
            self.assertEqual(py3["family"], "INTERPRETED")

    def test_execute_sync_success(self) -> None:
        """POST /api/execute runs user code and returns ExecutionResult."""
        self.runner.queue_result(
            RunnerResult(
                status="OK",
                exit_code=0,
                signal=0,
                wall_ms=35,
                cpu_ms=10,
                peak_memory_kb=15000,
                stdout="Hello from PES2 Gateway\n",
                stderr="",
            )
        )

        url = f"{self.base_url}/api/execute"
        payload = {
            "requestId": "req-gateway-test-1",
            "languageId": "python3",
            "sourceCode": "print('Hello from PES2 Gateway')",
            "stdin": "",
            "customLimits": {
                "cpuMs": 1000,
                "wallMs": 2000,
                "memoryKb": 65536,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")

        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, HTTPStatus.OK)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["requestId"], "req-gateway-test-1")
            self.assertEqual(data["languageId"], "python3")
            self.assertEqual(data["finalStatus"], "COMPLETED")
            self.assertEqual(data["exitCode"], 0)
            self.assertEqual(data["stdout"], "Hello from PES2 Gateway\n")
            self.assertGreaterEqual(len(data["milestones"]), 2)

    def test_execute_stream_sse(self) -> None:
        """POST /api/execute/stream streams SSE events until completion."""
        self.runner.queue_result(
            RunnerResult(
                status="OK",
                exit_code=0,
                signal=0,
                wall_ms=20,
                cpu_ms=5,
                peak_memory_kb=12000,
                stdout="Streamed Output\n",
                stderr="",
            )
        )

        url = f"{self.base_url}/api/execute/stream"
        payload = {
            "requestId": "req-gateway-stream-1",
            "languageId": "python3",
            "sourceCode": "print('Streamed Output')",
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")

        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, HTTPStatus.OK)
            self.assertEqual(resp.headers.get("Content-Type"), "text/event-stream")
            content = resp.read().decode("utf-8")
            self.assertIn("data:", content)
            self.assertIn("ACCEPTED", content)
            self.assertIn("COMPLETED", content)

    def test_cors_preflight(self) -> None:
        """OPTIONS request from allowed origin should return 204 with CORS headers."""
        url = f"{self.base_url}/api/execute"
        req = urllib.request.Request(url, headers={"Origin": "http://127.0.0.1:8088"}, method="OPTIONS")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, HTTPStatus.NO_CONTENT)
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "http://127.0.0.1:8088")
            self.assertIn("POST", resp.headers.get("Access-Control-Allow-Methods", ""))

    def test_not_found(self) -> None:
        """Invalid path should return 404."""
        url = f"{self.base_url}/api/nonexistent"
        req = urllib.request.Request(url, method="GET")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, HTTPStatus.NOT_FOUND)

    def test_bad_request_malformed_json(self) -> None:
        """Malformed JSON body should return 400."""
        url = f"{self.base_url}/api/execute"
        bad_body = b"NOT_A_JSON"
        req = urllib.request.Request(url, data=bad_body, headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, HTTPStatus.BAD_REQUEST)

    def test_tabular_data_parsing(self) -> None:
        """Extracts JSON tabular format from stdout when present."""
        tabular_json = '{"columns": ["id", "val"], "rows": [[1, "alpha"], [2, "beta"]]}'
        res = execution_pb2.ExecutionResult(
            request_id="req-tab",
            language_id="sqlite",
            final_status=execution_pb2.MILESTONE_COMPLETED,
            stdout=tabular_json + "\n",
            stderr="",
            exit_code=0,
        )
        data = proto_result_to_dict(res)
        self.assertIn("tabularResult", data)
        self.assertEqual(data["tabularResult"]["columns"], ["id", "val"])
        self.assertEqual(len(data["tabularResult"]["rows"]), 2)

    def test_daemon_offline_handling(self) -> None:
        """Gateway returns 503 when daemon address is offline."""
        offline_gw = ExecutionGateway(daemon_addr="127.0.0.1:59999", timeout=1.0)
        try:
            health = offline_gw.check_daemon_health()
            self.assertEqual(health["status"], "NOT_SERVING")

            payload = {"languageId": "python3", "sourceCode": "print(1)"}
            res, code = offline_gw.execute_sync(payload)
            self.assertEqual(code, HTTPStatus.SERVICE_UNAVAILABLE)
            self.assertEqual(res["finalStatus"], "SANDBOX_ERROR")
            self.assertIn("offline", res["stderr"].lower())
        finally:
            offline_gw.close()


if __name__ == "__main__":
    unittest.main()
