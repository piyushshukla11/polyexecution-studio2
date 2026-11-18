"""gRPC async service implementation for PolyExecution Studio 2 Worker Daemon."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator, Optional

import grpc

from engine.models import (
    ExecutionRequest,
    ExecutionResult,
    LanguageId,
    Milestone,
    MilestoneEvent,
    ResourceLimits,
)
from engine.pipeline import ExecutionPipeline
from engine.registry import RuntimeRegistry
from proto import execution_pb2, execution_pb2_grpc


MILESTONE_TO_PROTO = {
    Milestone.ACCEPTED: execution_pb2.MILESTONE_ACCEPTED,
    Milestone.COMPILING: execution_pb2.MILESTONE_COMPILING,
    Milestone.RUNNING: execution_pb2.MILESTONE_RUNNING,
    Milestone.COMPLETED: execution_pb2.MILESTONE_COMPLETED,
    Milestone.COMPILE_ERROR: execution_pb2.MILESTONE_COMPILE_ERROR,
    Milestone.RUNTIME_ERROR: execution_pb2.MILESTONE_RUNTIME_ERROR,
    Milestone.TIMEOUT: execution_pb2.MILESTONE_TIMEOUT,
    Milestone.OUTPUT_LIMIT: execution_pb2.MILESTONE_OUTPUT_LIMIT,
    Milestone.MEMORY_LIMIT: execution_pb2.MILESTONE_MEMORY_LIMIT,
    Milestone.BLOCKED_BY_POLICY: execution_pb2.MILESTONE_BLOCKED_BY_POLICY,
    Milestone.SANDBOX_ERROR: execution_pb2.MILESTONE_SANDBOX_ERROR,
    Milestone.CANCELLED: execution_pb2.MILESTONE_CANCELLED,
}

PROTO_TO_MILESTONE = {v: k for k, v in MILESTONE_TO_PROTO.items()}


def milestone_event_to_proto(event: MilestoneEvent) -> execution_pb2.MilestoneEvent:
    proto_m = MILESTONE_TO_PROTO.get(event.milestone, execution_pb2.MILESTONE_UNSPECIFIED)
    return execution_pb2.MilestoneEvent(
        milestone=proto_m,
        timestamp_ms=event.timestamp_ms,
        details=event.details or "",
    )


def execution_result_to_proto(result: ExecutionResult) -> execution_pb2.ExecutionResult:
    proto_final = MILESTONE_TO_PROTO.get(result.final_status, execution_pb2.MILESTONE_UNSPECIFIED)
    proto_milestones = [milestone_event_to_proto(m) for m in result.milestones]
    return execution_pb2.ExecutionResult(
        request_id=result.request_id,
        language_id=result.language_id.value,
        final_status=proto_final,
        milestones=proto_milestones,
        exit_code=result.exit_code,
        signal=result.signal,
        wall_ms=result.wall_ms,
        cpu_ms=result.cpu_ms,
        peak_memory_kb=result.peak_memory_kb,
        stdout=result.stdout,
        stderr=result.stderr,
        compile_output=result.compile_output or "",
    )


def proto_to_execution_request(req: execution_pb2.ExecuteRequest) -> ExecutionRequest:
    try:
        lang_id = LanguageId(req.language_id.lower())
    except ValueError:
        lang_id = LanguageId(req.language_id)

    custom_limits = None
    if req.HasField("custom_limits"):
        cl = req.custom_limits
        custom_limits = ResourceLimits(
            cpu_ms=cl.cpu_ms if cl.cpu_ms > 0 else 1000,
            wall_ms=cl.wall_ms if cl.wall_ms > 0 else 2000,
            memory_kb=cl.memory_kb if cl.memory_kb > 0 else 65536,
            pids=cl.pids if cl.pids > 0 else 16,
            max_output_bytes=cl.max_output_bytes if cl.max_output_bytes > 0 else 65536,
        )

    return ExecutionRequest(
        request_id=req.request_id,
        language_id=lang_id,
        source_code=req.source_code,
        stdin=req.stdin,
        custom_limits=custom_limits,
    )


class ExecutionWorkerServicer(execution_pb2_grpc.ExecutionWorkerServiceServicer):
    """Asynchronous worker servicer bridging gRPC RPCs to ExecutionPipeline."""

    def __init__(
        self,
        pipeline: ExecutionPipeline,
        max_workers: int = 8,
        executor: Optional[ThreadPoolExecutor] = None,
    ) -> None:
        self.pipeline = pipeline
        self.registry = pipeline.registry
        self.max_workers = max_workers
        self._executor = executor or ThreadPoolExecutor(max_workers=max_workers)
        self._active_workers = 0
        self._lock = asyncio.Lock()

    async def ExecuteSync(
        self,
        request: execution_pb2.ExecuteRequest,
        context: grpc.aio.ServicerContext,
    ) -> execution_pb2.ExecutionResult:
        """Executes request synchronously off the asyncio event loop."""
        async with self._lock:
            self._active_workers += 1

        try:
            domain_req = proto_to_execution_request(request)
            loop = asyncio.get_running_loop()
            fut = self._executor.submit(self.pipeline.execute, domain_req)
            result = await asyncio.wrap_future(fut)
            return execution_result_to_proto(result)
        except Exception as exc:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return execution_pb2.ExecutionResult(
                request_id=request.request_id,
                language_id=request.language_id,
                final_status=execution_pb2.MILESTONE_SANDBOX_ERROR,
                stderr=f"Internal daemon execution error: {exc}",
            )
        finally:
            async with self._lock:
                self._active_workers -= 1

    async def ExecuteStream(
        self,
        request: execution_pb2.ExecuteRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[execution_pb2.ExecutionProgressEvent]:
        """Streams live milestone events in real time followed by the final execution result."""
        async with self._lock:
            self._active_workers += 1

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_milestone(event: MilestoneEvent) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, ("milestone", event))

        def run_execution(req: ExecutionRequest) -> ExecutionResult:
            return self.pipeline.execute(req, milestone_callback=on_milestone)

        try:
            domain_req = proto_to_execution_request(request)
            fut = self._executor.submit(run_execution, domain_req)

            def on_done(f) -> None:
                try:
                    res = f.result()
                    loop.call_soon_threadsafe(queue.put_nowait, ("result", res))
                except Exception as err:
                    loop.call_soon_threadsafe(queue.put_nowait, ("error", err))

            fut.add_done_callback(on_done)

            while True:
                # Check for client cancellation
                if context.done():
                    fut.cancel()
                    break

                item_type, payload = await queue.get()
                if item_type == "milestone":
                    proto_event = milestone_event_to_proto(payload)
                    yield execution_pb2.ExecutionProgressEvent(milestone_event=proto_event)
                elif item_type == "result":
                    proto_result = execution_result_to_proto(payload)
                    yield execution_pb2.ExecutionProgressEvent(result=proto_result)
                    break
                elif item_type == "error":
                    context.set_code(grpc.StatusCode.INTERNAL)
                    context.set_details(str(payload))
                    break
        finally:
            async with self._lock:
                self._active_workers -= 1

    async def GetSupportedRuntimes(
        self,
        request: execution_pb2.GetSupportedRuntimesRequest,
        context: grpc.aio.ServicerContext,
    ) -> execution_pb2.GetSupportedRuntimesResponse:
        """Inspects and returns all registered language adapters and their installed versions."""
        adapters = self.registry.list_adapters()
        runtimes = []
        for lang_id, adapter in adapters.items():
            runtimes.append(
                execution_pb2.RuntimeInfo(
                    id=adapter.id.value,
                    display_name=adapter.display_name,
                    family=adapter.family.value,
                    official_version=adapter.official_version,
                    installed_version=adapter.installed_version,
                    is_installed=adapter.is_installed,
                )
            )
        return execution_pb2.GetSupportedRuntimesResponse(runtimes=runtimes)

    async def HealthCheck(
        self,
        request: execution_pb2.HealthCheckRequest,
        context: grpc.aio.ServicerContext,
    ) -> execution_pb2.HealthCheckResponse:
        """Truthful health check reporting daemon status and active workers."""
        async with self._lock:
            active = self._active_workers
        return execution_pb2.HealthCheckResponse(
            status=execution_pb2.HealthCheckResponse.SERVING,
            runner_version="pes2-runner-2.0",
            active_workers=active,
        )
