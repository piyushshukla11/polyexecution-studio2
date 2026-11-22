"""HTTP API Gateway for PolyExecution Studio 2.

Exposes REST and SSE endpoints for the frontend Web Studio and external API clients,
translating HTTP requests to gRPC calls against the internal Worker Daemon.
Enforces request size bounds, schema validation, resource limits ceilings,
connection timeouts, and server-side authentication for remote access.
"""

import argparse
import hmac
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.parse

import grpc

from engine.models import LanguageId
from engine.registry import RuntimeRegistry
from proto import execution_pb2, execution_pb2_grpc


logger = logging.getLogger("pes2.gateway")

ALLOWED_LOOPBACK_HOSTS: Set[str] = {"127.0.0.1", "localhost", "::1"}

# Security boundaries
MAX_REQUEST_BYTES: int = 256 * 1024  # 256 KiB ceiling for HTTP request bodies
MAX_SOURCE_BYTES: int = 65536         # 64 KiB ceiling for source code / stdin
MAX_REQUEST_ID_LEN: int = 128
CONNECTION_TIMEOUT_SEC: float = 15.0
DEFAULT_MAX_CONCURRENCY: int = 64

DEFAULT_ALLOWED_ORIGINS: Set[str] = {
    "http://127.0.0.1:8088",
    "http://localhost:8088",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
}

# Resource limits validation bounds
RUNNER_MIN_TIMEOUT_MS: int = 1
RUNNER_MAX_TIMEOUT_MS: int = 60000
RUNNER_MIN_MEMORY_KB: int = 16384
RUNNER_MAX_MEMORY_KB: int = 1048576
RUNNER_MIN_PIDS: int = 1
RUNNER_MAX_PIDS: int = 128
MAX_OUTPUT_BYTES_MIN: int = 1024
MAX_OUTPUT_BYTES_MAX: int = 1048576

KNOWN_LANGUAGE_IDS: Set[str] = {lang.value for lang in LanguageId}

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


def check_authentication(headers: Dict[str, str], expected_api_key: Optional[str]) -> bool:
    """Validates Authorization Bearer token or X-API-Key header with constant-time comparison."""
    if not expected_api_key:
        return True

    auth_header = headers.get("authorization", headers.get("Authorization", "")).strip()
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        return hmac.compare_digest(token, expected_api_key)

    api_key_header = headers.get("x-api-key", headers.get("X-API-Key", "")).strip()
    if api_key_header:
        return hmac.compare_digest(api_key_header, expected_api_key)

    return False


def validate_execution_payload(payload: Any) -> Tuple[bool, Optional[str]]:
    """Validates JSON schema, types, source code size, and custom resource limits."""
    if not isinstance(payload, dict):
        return False, "Request payload must be a JSON object"

    lang_id = payload.get("languageId") or payload.get("language_id")
    if not lang_id or not isinstance(lang_id, str):
        return False, "Field 'languageId' is required and must be a non-empty string"
    lang_id_clean = lang_id.strip().lower()
    if lang_id_clean not in KNOWN_LANGUAGE_IDS:
        return False, f"Unknown languageId '{lang_id}'. Must be a supported PES2 language identifier."
    if lang_id_clean in {"react", "vanilla_js"}:
        return False, (
            f"Language '{lang_id_clean}' is a browser-only runtime and cannot be executed on the server runner. "
            "Execute it client-side in the browser sandbox."
        )

    source = payload.get("sourceCode") or payload.get("source_code")
    if source is None or not isinstance(source, str):
        return False, "Field 'sourceCode' is required and must be a string"
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        return False, f"Field 'sourceCode' exceeds maximum allowed size of {MAX_SOURCE_BYTES} bytes (64 KiB)"

    stdin_content = payload.get("stdin")
    if stdin_content is not None:
        if not isinstance(stdin_content, str):
            return False, "Field 'stdin' must be a string"
        if len(stdin_content.encode("utf-8")) > MAX_SOURCE_BYTES:
            return False, f"Field 'stdin' exceeds maximum allowed size of {MAX_SOURCE_BYTES} bytes (64 KiB)"

    req_id = payload.get("requestId") or payload.get("request_id")
    if req_id is not None:
        if not isinstance(req_id, str) or len(req_id) > MAX_REQUEST_ID_LEN:
            return False, f"Field 'requestId' must be a string <= {MAX_REQUEST_ID_LEN} characters"

    limits = payload.get("customLimits") or payload.get("custom_limits")
    if limits is not None:
        if not isinstance(limits, dict):
            return False, "Field 'customLimits' must be a JSON object"

        cpu_ms = limits.get("cpuMs", limits.get("cpu_ms"))
        if cpu_ms is not None:
            if not isinstance(cpu_ms, int) or isinstance(cpu_ms, bool) or cpu_ms < RUNNER_MIN_TIMEOUT_MS or cpu_ms > RUNNER_MAX_TIMEOUT_MS:
                return False, f"customLimits.cpuMs must be an integer between {RUNNER_MIN_TIMEOUT_MS} and {RUNNER_MAX_TIMEOUT_MS}"

        wall_ms = limits.get("wallMs", limits.get("wall_ms"))
        if wall_ms is not None:
            if not isinstance(wall_ms, int) or isinstance(wall_ms, bool) or wall_ms < RUNNER_MIN_TIMEOUT_MS or wall_ms > RUNNER_MAX_TIMEOUT_MS:
                return False, f"customLimits.wallMs must be an integer between {RUNNER_MIN_TIMEOUT_MS} and {RUNNER_MAX_TIMEOUT_MS}"

        mem_kb = limits.get("memoryKb", limits.get("memory_kb"))
        if mem_kb is not None:
            if not isinstance(mem_kb, int) or isinstance(mem_kb, bool) or mem_kb < RUNNER_MIN_MEMORY_KB or mem_kb > RUNNER_MAX_MEMORY_KB:
                return False, f"customLimits.memoryKb must be an integer between {RUNNER_MIN_MEMORY_KB} and {RUNNER_MAX_MEMORY_KB}"

        pids = limits.get("pids")
        if pids is not None:
            if not isinstance(pids, int) or isinstance(pids, bool) or pids < RUNNER_MIN_PIDS or pids > RUNNER_MAX_PIDS:
                return False, f"customLimits.pids must be an integer between {RUNNER_MIN_PIDS} and {RUNNER_MAX_PIDS}"

        out_bytes = limits.get("maxOutputBytes", limits.get("max_output_bytes"))
        if out_bytes is not None:
            if not isinstance(out_bytes, int) or isinstance(out_bytes, bool) or out_bytes < MAX_OUTPUT_BYTES_MIN or out_bytes > MAX_OUTPUT_BYTES_MAX:
                return False, f"customLimits.maxOutputBytes must be an integer between {MAX_OUTPUT_BYTES_MIN} and {MAX_OUTPUT_BYTES_MAX}"

    return True, None


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
    timeout = CONNECTION_TIMEOUT_SEC

    @property
    def gateway(self) -> "ExecutionGateway":
        return self.server.gateway  # type: ignore

    def _get_validated_origin(self) -> Optional[str]:
        origin = self.headers.get("Origin")
        if not origin:
            return None
        clean_origin = origin.rstrip("/")
        if clean_origin in self.gateway.allowed_origins or "*" in self.gateway.allowed_origins:
            return clean_origin
        return None

    def _send_cors_headers(self) -> None:
        valid_origin = self._get_validated_origin()
        if valid_origin:
            self.send_header("Access-Control-Allow-Origin", valid_origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key, X-Request-ID")
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
        origin = self.headers.get("Origin")
        if origin and not self._get_validated_origin():
            self._send_json_response(HTTPStatus.FORBIDDEN, {
                "error": "Forbidden",
                "message": f"Cross-origin requests from origin '{origin}' are not permitted.",
            })
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        origin = self.headers.get("Origin")
        if origin and not self._get_validated_origin():
            self._send_json_response(HTTPStatus.FORBIDDEN, {
                "error": "Forbidden",
                "message": f"Cross-origin requests from origin '{origin}' are not permitted.",
            })
            return

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
        origin = self.headers.get("Origin")
        if origin and not self._get_validated_origin():
            self._send_json_response(HTTPStatus.FORBIDDEN, {
                "error": "Forbidden",
                "message": f"Cross-origin requests from origin '{origin}' are not permitted.",
            })
            return

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
        except ValueError:
            self._send_json_response(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length header"})
            return None

        if length < 0:
            self._send_json_response(HTTPStatus.BAD_REQUEST, {"error": "Invalid negative Content-Length header"})
            return None

        if length > MAX_REQUEST_BYTES:
            self._send_json_response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {
                "error": "Payload Too Large",
                "message": f"Request body length {length} bytes exceeds maximum allowed limit of {MAX_REQUEST_BYTES} bytes (256 KiB)",
            })
            return None

        try:
            raw_body = self.rfile.read(length)
            if len(raw_body) != length:
                self._send_json_response(HTTPStatus.BAD_REQUEST, {"error": "Incomplete request body"})
                return None
            return json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            self._send_json_response(HTTPStatus.BAD_REQUEST, {
                "error": "Malformed JSON payload",
                "details": str(exc),
            })
            return None

    def _verify_auth(self) -> bool:
        if self.gateway.api_key:
            headers_dict = {k.lower(): v for k, v in self.headers.items()}
            if not check_authentication(headers_dict, self.gateway.api_key):
                self._send_json_response(HTTPStatus.UNAUTHORIZED, {
                    "error": "Unauthorized",
                    "message": "Valid Bearer token or X-API-Key required for execution access",
                })
                return False
        return True

    def handle_execute(self) -> None:
        """Synchronously executes code through worker daemon gRPC."""
        if not self._verify_auth():
            return

        payload = self._read_json_body()
        if payload is None:
            return

        is_valid, validation_err = validate_execution_payload(payload)
        if not is_valid:
            self._send_json_response(HTTPStatus.BAD_REQUEST, {
                "error": "Schema Validation Error",
                "message": validation_err,
            })
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
        if not self._verify_auth():
            return

        payload = self._read_json_body()
        if payload is None:
            return

        is_valid, validation_err = validate_execution_payload(payload)
        if not is_valid:
            self._send_json_response(HTTPStatus.BAD_REQUEST, {
                "error": "Schema Validation Error",
                "message": validation_err,
            })
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

    def __init__(
        self,
        daemon_addr: str = "127.0.0.1:50051",
        timeout: float = 30.0,
        api_key: Optional[str] = None,
        require_auth: bool = False,
        allowed_origins: Optional[Set[str]] = None,
    ):
        self.daemon_addr = daemon_addr
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("PES2_API_KEY")
        self.require_auth = require_auth or os.environ.get("PES2_REQUIRE_AUTH") == "1"
        if self.require_auth and not self.api_key:
            raise ValueError(
                "Production security violation: PES2_API_KEY must be configured when authentication is required."
            )

        if allowed_origins is not None:
            self.allowed_origins = set(allowed_origins)
        else:
            env_origins = os.environ.get("PES2_ALLOWED_ORIGINS")
            if env_origins:
                self.allowed_origins = {o.strip() for o in env_origins.split(",") if o.strip()}
            else:
                self.allowed_origins = set(DEFAULT_ALLOWED_ORIGINS)

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
    """Threading HTTPServer with reference to ExecutionGateway and connection concurrency cap."""

    def __init__(
        self,
        server_address: Tuple[str, int],
        gateway: ExecutionGateway,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    ):
        self.gateway = gateway
        self.max_concurrency = max_concurrency
        self._concurrency_semaphore = threading.BoundedSemaphore(max_concurrency)
        super().__init__(server_address, GatewayRequestHandler)

    def process_request(self, request, client_address):
        acquired = self._concurrency_semaphore.acquire(blocking=False)
        if not acquired:
            try:
                msg = b'{"error": "Service Unavailable", "message": "Gateway connection limit reached. Please retry later."}\n'
                hdr = (
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Type: application/json; charset=utf-8\r\n"
                    b"Content-Length: " + str(len(msg)).encode("ascii") + b"\r\n"
                    b"Connection: close\r\n\r\n"
                )
                request.sendall(hdr + msg)
            except Exception:
                pass
            finally:
                self.shutdown_request(request)
            return

        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            try:
                self._concurrency_semaphore.release()
            except ValueError:
                pass


def create_gateway_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    daemon_addr: str = "127.0.0.1:50051",
    timeout: float = 30.0,
    api_key: Optional[str] = None,
    require_auth: bool = False,
    gateway: Optional[ExecutionGateway] = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    allowed_origins: Optional[Set[str]] = None,
) -> Tuple[GatewayServer, int]:
    if gateway is None:
        gateway = ExecutionGateway(
            daemon_addr=daemon_addr,
            timeout=timeout,
            api_key=api_key,
            require_auth=require_auth,
            allowed_origins=allowed_origins,
        )
    server = GatewayServer((host, port), gateway, max_concurrency=max_concurrency)
    bound_port = server.server_port
    return server, bound_port


def run_gateway(
    host: str = "0.0.0.0",
    port: int = 8000,
    daemon_addr: str = "127.0.0.1:50051",
    timeout: float = 30.0,
    api_key: Optional[str] = None,
    require_auth: bool = False,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    allowed_origins: Optional[Set[str]] = None,
) -> None:
    server, bound_port = create_gateway_server(
        host=host,
        port=port,
        daemon_addr=daemon_addr,
        timeout=timeout,
        api_key=api_key,
        require_auth=require_auth,
        max_concurrency=max_concurrency,
        allowed_origins=allowed_origins,
    )
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
    parser.add_argument(
        "--api-key",
        default=os.environ.get("PES2_API_KEY"),
        help="Secret API key / Bearer token required for execution",
    )
    parser.add_argument(
        "--require-auth",
        action="store_true",
        default=os.environ.get("PES2_REQUIRE_AUTH") == "1",
        help="Enforce mandatory Bearer token / X-API-Key authentication",
    )
    parser.add_argument(
        "--insecure-no-auth",
        action="store_true",
        default=False,
        help="Explicitly bypass authentication requirement on non-loopback interfaces (testing only)",
    )
    parser.add_argument(
        "--allow-internal-network",
        action="store_true",
        default=False,
        help="Permit binding to private internal container network interfaces (e.g. 0.0.0.0 in Docker Compose)",
    )
    parser.add_argument(
        "--public-host",
        default=os.environ.get("PES2_BIND_HOST"),
        help="Public host interface where reverse proxy or ingress exposes the studio",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=int(os.environ.get("PES2_GATEWAY_MAX_CONCURRENCY", str(DEFAULT_MAX_CONCURRENCY))),
        help="Ceiling for concurrent client connections before returning HTTP 503",
    )
    parser.add_argument(
        "--allowed-origins",
        default=os.environ.get("PES2_ALLOWED_ORIGINS"),
        help="Comma-separated whitelist of allowed CORS origins",
    )

    args = parser.parse_args(args_list)

    allow_internal = args.allow_internal_network or os.environ.get("PES2_ALLOW_INTERNAL_NETWORKING") == "1"
    public_host = args.public_host or os.environ.get("PES2_BIND_HOST")

    if args.api_key:
        args.require_auth = True

    # 1. Public ingress boundary check: If reverse proxy / ingress host (PES2_BIND_HOST) is non-loopback,
    # arbitrary code execution is exposed to external networks. Internal networking flags cannot bypass
    # authentication when public ingress is enabled.
    if public_host and public_host not in ALLOWED_LOOPBACK_HOSTS and not args.insecure_no_auth:
        if not args.api_key:
            sys.stderr.write(
                f"Error: Production security violation: Ingress host '{public_host}' is exposed on a "
                "non-loopback interface without server-side authentication (--api-key or PES2_API_KEY). "
                "Arbitrary code execution must not be exposed on public interfaces without authentication.\n"
            )
            return 2
        args.require_auth = True

    # 2. Gateway listener check: Non-loopback binding requires authentication unless explicitly bypassed or internal
    if args.host not in ALLOWED_LOOPBACK_HOSTS and not args.insecure_no_auth:
        if not allow_internal:
            if not args.api_key:
                sys.stderr.write(
                    f"Error: Production security violation: Binding to remote host '{args.host}' "
                    "requires server-side authentication (--api-key or PES2_API_KEY). "
                    "CORS is not an authentication boundary.\n"
                )
                return 2
            args.require_auth = True

    if allow_internal and args.host not in ALLOWED_LOOPBACK_HOSTS:
        logger.warning(
            "Internal service networking enabled: gateway binding to '%s'. "
            "Ensure service is strictly contained on private internal Docker networks and not exposed publicly.",
            args.host,
        )

    allowed_origins = None
    if args.allowed_origins:
        allowed_origins = {o.strip() for o in args.allowed_origins.split(",") if o.strip()}

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    run_gateway(
        host=args.host,
        port=args.port,
        daemon_addr=args.daemon_addr,
        timeout=args.timeout,
        api_key=args.api_key,
        require_auth=args.require_auth,
        max_concurrency=args.max_concurrency,
        allowed_origins=allowed_origins,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
