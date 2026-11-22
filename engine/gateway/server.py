"""HTTP API Gateway for PolyExecution Studio 2.

Exposes REST and SSE endpoints for the frontend Web Studio and external API clients,
translating HTTP requests to gRPC calls against the internal Worker Daemon.
"""

import argparse
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse

import grpc

from engine.registry import RuntimeRegistry
from proto import execution_pb2, execution_pb2_grpc


logger = logging.getLogger("pes2.gateway")

PROTO_TO_MILESTONE_STR: Dict[int, str] = {
    execution_pb2.MILESTONE_UNSPECIFIED: "UNKNOWN",
    execution_pb2.MILESTONE_ACCEPTED: "ACCEPTED",
    execution_pb2.MILESTONE_COMPILING: "COMPILING",
    execution_pb2.MILESTONE_RUNNING: "RUNNING",
    execution_pb2.MILESTONE_COMPLETED: "COMPLETED",
    execution_pb2.MILESTONE_COMPILE_ERROR: "COMPILE_ERROR",
    execution_pb2.MILESTONE_RUNTIME_ERROR: "RUNTIME_ERROR",
    execution_pb2.MILESTONE_TIMEOUT: "TIMEOUT",
    execution_pb2.MILESTONE_OUTPUT_LIMIT: "OUTPUT_LIMIT",
    execution_pb2.MILESTONE_MEMORY_LIMIT: "MEMORY_LIMIT",
    execution_pb2.MILESTONE_BLOCKED_BY_POLICY: "BLOCKED_BY_POLICY",
    execution_pb2.MILESTONE_SANDBOX_ERROR: "SANDBOX_ERROR",
    execution_pb2.MILESTONE_CANCELLED: "CANCELLED",
}


def proto_result_to_dict(res: execution_pb2.ExecutionResult) -> Dict[str, Any]:
    final_status = PROTO_TO_MILESTONE_STR.get(res.final_status, "UNKNOWN")
    milestones: List[Dict[str, Any]] = []
    for m in res.milestones:
        m_name = PROTO_TO_MILESTONE_STR.get(m.milestone, "UNKNOWN")
        milestones.append({
            "milestone": m_name,
            "timestampMs": m.timestamp_ms,
            "timestamp_ms": m.timestamp_ms,
            "details": m.details,
        })

    # Attempt to extract structured tabular JSON from stdout if present
    tabular_data = None
    if res.stdout and ("\"columns\"" in res.stdout or "'columns'" in res.stdout):
        try:
            # Look for lines containing JSON tabular format
            for line in res.stdout.strip().split("\n"):
                line_str = line.strip()
                if line_str.startswith("{") and line_str.endswith("}"):
                    parsed = json.loads(line_str)
                    if isinstance(parsed, dict) and "columns" in parsed and "rows" in parsed:
                        tabular_data = parsed
                        break
        except Exception:
            pass

    data: Dict[str, Any] = {
        "requestId": res.request_id,
        "request_id": res.request_id,
        "languageId": res.language_id,
        "language_id": res.language_id,
        "finalStatus": final_status,
        "status": final_status,
        "exitCode": res.exit_code,
        "exit_code": res.exit_code,
        "signal": res.signal,
        "wallMs": res.wall_ms,
        "wall_ms": res.wall_ms,
        "cpuMs": res.cpu_ms,
        "cpu_ms": res.cpu_ms,
        "peakMemoryKb": res.peak_memory_kb,
        "peak_memory_kb": res.peak_memory_kb,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "compileOutput": res.compile_output,
        "compile_output": res.compile_output,
        "milestones": milestones,
    }
    if tabular_data is not None:
        data["tabularResult"] = tabular_data
        data["tabular_result"] = tabular_data

    return data


class GatewayRequestHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests for PolyExecution Studio 2 API Gateway."""

    server_version = "PES2-Gateway/2.0"

    @property
    def gateway(self) -> "ExecutionGateway":
        return self.server.gateway  # type: ignore

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Request-ID")
        self.send_header("Access-Control-Max-Age", "86400")

    def _send_json_response(self, status_code: int, data: Any) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path.rstrip("/")

        if path in ("/health", "/api/health"):
            self.handle_health()
        elif path in ("/runtimes", "/api/runtimes"):
            self.handle_runtimes()
        else:
            self._send_json_response(HTTPStatus.NOT_FOUND, {
                "error": "Not Found",
                "message": f"Endpoint '{self.path}' does not exist on PES 2 API Gateway",
            })

    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path.rstrip("/")

        if path in ("/execute", "/api/execute"):
            self.handle_execute()
        elif path in ("/execute/stream", "/api/execute/stream"):
            self.handle_execute_stream()
        else:
            self._send_json_response(HTTPStatus.NOT_FOUND, {
                "error": "Not Found",
                "message": f"Endpoint '{self.path}' does not exist on PES 2 API Gateway",
            })

    def handle_health(self) -> None:
        """Checks worker daemon health via gRPC and reports serving status."""
        health = self.gateway.check_daemon_health()
        status_code = HTTPStatus.OK if health.get("status") == "SERVING" else HTTPStatus.SERVICE_UNAVAILABLE
        self._send_json_response(status_code, health)

    def handle_runtimes(self) -> None:
        """Returns truthful runtime inventory and installed versions."""
        runtimes = self.gateway.get_runtimes()
        self._send_json_response(HTTPStatus.OK, {"runtimes": runtimes})

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        content_length_str = self.headers.get("Content-Length")
        if not content_length_str:
            self._send_json_response(HTTPStatus.BAD_REQUEST, {"error": "Missing Content-Length header"})
            return None

        try:
            length = int(content_length_str)
            raw_body = self.rfile.read(length)
            return json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            self._send_json_response(HTTPStatus.BAD_REQUEST, {
                "error": "Malformed JSON payload",
                "details": str(exc),
            })
            return None

    def handle_execute(self) -> None:
        """Synchronously executes code through worker daemon gRPC."""
        payload = self._read_json_body()
        if payload is None:
            return

        try:
            result, status_code = self.gateway.execute_sync(payload)
            self._send_json_response(status_code, result)
        except Exception as exc:
            logger.exception("Unexpected error during execution handling: %s", exc)
            self._send_json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": "Internal execution error",
                "details": str(exc),
            })

    def handle_execute_stream(self) -> None:
        """Streams lifecycle milestones and execution result via Server-Sent Events (SSE)."""
        payload = self._read_json_body()
        if payload is None:
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._send_cors_headers()
        self.end_headers()
        self.close_connection = True

        try:
            for event in self.gateway.execute_stream(payload):
                line = f"data: {json.dumps(event)}\n\n".encode("utf-8")
                self.wfile.write(line)
                self.wfile.flush()
        except Exception as exc:
            error_event = {"type": "error", "error": str(exc)}
            line = f"data: {json.dumps(error_event)}\n\n".encode("utf-8")
            try:
                self.wfile.write(line)
                self.wfile.flush()
            except Exception:
                pass

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)


class ExecutionGateway:
    """Core gateway business logic bridging HTTP clients to Worker Daemon gRPC."""

    def __init__(self, daemon_addr: str = "127.0.0.1:50051", timeout: float = 30.0):
        self.daemon_addr = daemon_addr
        self.timeout = timeout
        self._channel: Optional[grpc.Channel] = None
        self._stub: Optional[execution_pb2_grpc.ExecutionWorkerServiceStub] = None
        self._registry = RuntimeRegistry()

    def get_stub(self) -> execution_pb2_grpc.ExecutionWorkerServiceStub:
        if self._stub is None:
            options = [
                ("grpc.max_receive_message_length", 64 * 1024 * 1024),
                ("grpc.max_send_message_length", 64 * 1024 * 1024),
            ]
            self._channel = grpc.insecure_channel(self.daemon_addr, options=options)
            self._stub = execution_pb2_grpc.ExecutionWorkerServiceStub(self._channel)
        return self._stub

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()
            self._channel = None
            self._stub = None

    def check_daemon_health(self) -> Dict[str, Any]:
        """Queries the worker daemon health endpoint."""
        stub = self.get_stub()
        try:
            req = execution_pb2.HealthCheckRequest()
            resp = stub.HealthCheck(req, timeout=3.0)
            status_str = "SERVING" if resp.status == execution_pb2.HealthCheckResponse.SERVING else "NOT_SERVING"
            return {
                "status": status_str,
                "runner_version": resp.runner_version or "pes2-runner-2.0",
                "active_workers": resp.active_workers,
            }
        except grpc.RpcError as rpc_err:
            logger.warning("Worker daemon unreachable at %s: %s", self.daemon_addr, rpc_err)
            return {
                "status": "NOT_SERVING",
                "runner_version": "unavailable",
                "active_workers": 0,
                "error": f"Worker daemon unreachable at {self.daemon_addr}",
            }
        except Exception as exc:
            return {
                "status": "NOT_SERVING",
                "runner_version": "unavailable",
                "active_workers": 0,
                "error": str(exc),
            }

    def get_runtimes(self) -> List[Dict[str, Any]]:
        """Queries supported runtimes from daemon or falls back to local registry."""
        stub = self.get_stub()
        try:
            req = execution_pb2.GetSupportedRuntimesRequest()
            resp = stub.GetSupportedRuntimes(req, timeout=3.0)
            items: List[Dict[str, Any]] = []
            for r in resp.runtimes:
                items.append({
                    "id": r.id,
                    "display_name": r.display_name,
                    "family": r.family,
                    "official_version": r.official_version,
                    "installed_version": r.installed_version,
                    "is_installed": r.is_installed,
                })
            return items
        except Exception as exc:
            logger.warning("Falling back to local registry for runtimes: %s", exc)
            adapters = self._registry.list_adapters()
            items = []
            for lang_id, adapter in adapters.items():
                items.append({
                    "id": adapter.id.value,
                    "display_name": adapter.display_name,
                    "family": adapter.family.value,
                    "official_version": adapter.official_version,
                    "installed_version": adapter.installed_version,
                    "is_installed": adapter.is_installed,
                })
            return items

    def _build_grpc_request(self, payload: Dict[str, Any]) -> execution_pb2.ExecuteRequest:
        req_id = payload.get("requestId") or payload.get("request_id") or f"req-{int(time.time() * 1000)}"
        lang_id = payload.get("languageId") or payload.get("language_id") or ""
        source = payload.get("sourceCode") or payload.get("source_code") or ""
        stdin_content = payload.get("stdin") or ""

        limits = payload.get("customLimits") or payload.get("custom_limits") or {}
        custom_limits = None
        if limits:
            custom_limits = execution_pb2.ResourceLimits(
                cpu_ms=limits.get("cpuMs", limits.get("cpu_ms", 1000)),
                wall_ms=limits.get("wallMs", limits.get("wall_ms", 2000)),
                memory_kb=limits.get("memoryKb", limits.get("memory_kb", 65536)),
                pids=limits.get("pids", 16),
                max_output_bytes=limits.get("maxOutputBytes", limits.get("max_output_bytes", 65536)),
            )

        return execution_pb2.ExecuteRequest(
            request_id=req_id,
            language_id=lang_id,
            source_code=source,
            stdin=stdin_content,
            custom_limits=custom_limits,
        )

    def execute_sync(self, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """Executes code synchronously and returns (result_dict, http_status_code)."""
        stub = self.get_stub()
        grpc_req = self._build_grpc_request(payload)

        try:
            proto_result = stub.ExecuteSync(grpc_req, timeout=self.timeout)
            result_dict = proto_result_to_dict(proto_result)
            return result_dict, HTTPStatus.OK
        except grpc.RpcError as rpc_err:
            code = rpc_err.code()
            details = rpc_err.details() or "RPC error"
            logger.warning("gRPC execution failed (%s): %s", code, details)

            if code == grpc.StatusCode.RESOURCE_EXHAUSTED:
                return {
                    "requestId": grpc_req.request_id,
                    "request_id": grpc_req.request_id,
                    "languageId": grpc_req.language_id,
                    "finalStatus": "SANDBOX_ERROR",
                    "status": "SANDBOX_ERROR",
                    "exitCode": 1,
                    "signal": 0,
                    "wallMs": 0,
                    "cpuMs": 0,
                    "peakMemoryKb": 0,
                    "stdout": "",
                    "stderr": f"Worker daemon resource exhausted: {details}",
                    "milestones": [
                        {"milestone": "ACCEPTED", "timestampMs": int(time.time() * 1000)},
                        {"milestone": "SANDBOX_ERROR", "timestampMs": int(time.time() * 1000), "details": details},
                    ],
                }, HTTPStatus.TOO_MANY_REQUESTS

            elif code == grpc.StatusCode.UNAVAILABLE:
                return {
                    "requestId": grpc_req.request_id,
                    "request_id": grpc_req.request_id,
                    "languageId": grpc_req.language_id,
                    "finalStatus": "SANDBOX_ERROR",
                    "status": "SANDBOX_ERROR",
                    "exitCode": 1,
                    "signal": 0,
                    "wallMs": 0,
                    "cpuMs": 0,
                    "peakMemoryKb": 0,
                    "stdout": "",
                    "stderr": f"Backend worker daemon offline or unreachable at {self.daemon_addr}: {details}",
                    "milestones": [
                        {"milestone": "ACCEPTED", "timestampMs": int(time.time() * 1000)},
                        {"milestone": "SANDBOX_ERROR", "timestampMs": int(time.time() * 1000), "details": "Daemon offline"},
                    ],
                }, HTTPStatus.SERVICE_UNAVAILABLE

            elif code == grpc.StatusCode.DEADLINE_EXCEEDED:
                return {
                    "requestId": grpc_req.request_id,
                    "request_id": grpc_req.request_id,
                    "languageId": grpc_req.language_id,
                    "finalStatus": "TIMEOUT",
                    "status": "TIMEOUT",
                    "exitCode": 124,
                    "signal": 9,
                    "wallMs": int(self.timeout * 1000),
                    "cpuMs": 0,
                    "peakMemoryKb": 0,
                    "stdout": "",
                    "stderr": f"Execution deadline exceeded ({self.timeout}s): {details}",
                    "milestones": [
                        {"milestone": "ACCEPTED", "timestampMs": int(time.time() * 1000)},
                        {"milestone": "TIMEOUT", "timestampMs": int(time.time() * 1000), "details": "Deadline exceeded"},
                    ],
                }, HTTPStatus.GATEWAY_TIMEOUT

            return {
                "requestId": grpc_req.request_id,
                "request_id": grpc_req.request_id,
                "languageId": grpc_req.language_id,
                "finalStatus": "SANDBOX_ERROR",
                "status": "SANDBOX_ERROR",
                "exitCode": 1,
                "signal": 0,
                "wallMs": 0,
                "cpuMs": 0,
                "peakMemoryKb": 0,
                "stdout": "",
                "stderr": f"RPC error: {details}",
                "milestones": [
                    {"milestone": "ACCEPTED", "timestampMs": int(time.time() * 1000)},
                    {"milestone": "SANDBOX_ERROR", "timestampMs": int(time.time() * 1000), "details": details},
                ],
            }, HTTPStatus.INTERNAL_SERVER_ERROR

    def execute_stream(self, payload: Dict[str, Any]):
        """Streams progress events from gRPC ExecuteStream."""
        stub = self.get_stub()
        grpc_req = self._build_grpc_request(payload)

        try:
            for progress_event in stub.ExecuteStream(grpc_req, timeout=self.timeout):
                event_type = progress_event.WhichOneof("event")
                if event_type == "milestone_event":
                    me = progress_event.milestone_event
                    yield {
                        "type": "milestone",
                        "milestone": PROTO_TO_MILESTONE_STR.get(me.milestone, "UNKNOWN"),
                        "timestampMs": me.timestamp_ms,
                        "details": me.details,
                    }
                elif event_type == "result":
                    res = progress_event.result
                    yield {
                        "type": "result",
                        "result": proto_result_to_dict(res),
                    }
        except grpc.RpcError as rpc_err:
            yield {
                "type": "error",
                "error": str(rpc_err.details() or rpc_err.code()),
            }


class GatewayServer(ThreadingHTTPServer):
    """Threading HTTPServer with reference to ExecutionGateway."""

    def __init__(self, server_address: Tuple[str, int], gateway: ExecutionGateway):
        self.gateway = gateway
        super().__init__(server_address, GatewayRequestHandler)


def create_gateway_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    daemon_addr: str = "127.0.0.1:50051",
    timeout: float = 30.0,
) -> Tuple[GatewayServer, int]:
    gateway = ExecutionGateway(daemon_addr=daemon_addr, timeout=timeout)
    server = GatewayServer((host, port), gateway)
    bound_port = server.server_port
    return server, bound_port


def run_gateway(
    host: str = "0.0.0.0",
    port: int = 8000,
    daemon_addr: str = "127.0.0.1:50051",
    timeout: float = 30.0,
) -> None:
    server, bound_port = create_gateway_server(host=host, port=port, daemon_addr=daemon_addr, timeout=timeout)
    logger.info("PES 2 API Gateway listening on http://%s:%d -> daemon at %s", host, bound_port, daemon_addr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Gateway server interrupted by user")
    finally:
        server.server_close()
        server.gateway.close()


def main(args_list: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="PolyExecution Studio 2 API Gateway")
    parser.add_argument("--host", default=os.environ.get("PES2_GATEWAY_HOST", "0.0.0.0"), help="HTTP listen host")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PES2_GATEWAY_PORT", "8000")), help="HTTP listen port")
    parser.add_argument(
        "--daemon-addr",
        default=os.environ.get("PES2_DAEMON_ADDR", "127.0.0.1:50051"),
        help="Worker Daemon gRPC address (host:port)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("PES2_TIMEOUT_SEC", "30.0")),
        help="Execution timeout ceiling in seconds",
    )

    args = parser.parse_args(args_list)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    run_gateway(host=args.host, port=args.port, daemon_addr=args.daemon_addr, timeout=args.timeout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
