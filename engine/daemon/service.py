"""gRPC async service implementation for PolyExecution Studio 2 Worker Daemon."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import threading
from typing import AsyncIterator, Optional

import grpc

from engine.models import (
    ExecutionRequest,
    ExecutionResult,
    LanguageId,
    Milestone,
    MilestoneEvent,
    ResourceLimits,
    RuntimeFamily,
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


def detect_cgroup_memory_limit_kb(
    proc_cgroup_path: str = "/proc/self/cgroup",
    cgroup_root: str = "/sys/fs/cgroup",
) -> Optional[int]:
    """Inspects cgroups v2 and v1 hierarchies to detect effective memory limits on the daemon process.

    Returns the minimum detected limit in KiB, or None if unconstrained.
    """
    limit_bytes: Optional[int] = None

    # Check cgroup v2
    # 1. Direct container root memory.max
    root_v2 = os.path.join(cgroup_root, "memory.max")
    if os.path.exists(root_v2):
        try:
            with open(root_v2, "r") as f:
                val = f.read().strip()
                if val and val != "max":
                    limit_bytes = int(val)
        except (ValueError, OSError):
            pass

    # 2. Check process cgroup path hierarchy from /proc/self/cgroup
    if os.path.exists(proc_cgroup_path):
        try:
            cgroup_rel = None
            with open(proc_cgroup_path, "r") as f:
                for line in f:
                    parts = line.strip().split("::")
                    if len(parts) == 2:
                        cgroup_rel = parts[1].strip()
                        break
            if cgroup_rel:
                cur = cgroup_rel.lstrip("/")
                while cur and cur != ".":
                    p = os.path.join(cgroup_root, cur, "memory.max")
                    if os.path.exists(p):
                        try:
                            with open(p, "r") as f:
                                val = f.read().strip()
                                if val and val != "max":
                                    cand = int(val)
                                    limit_bytes = cand if limit_bytes is None else min(limit_bytes, cand)
                        except (ValueError, OSError):
                            pass
                    cur = os.path.dirname(cur)
        except OSError:
            pass

    # Check cgroup v1 fallback
    v1_paths = [
        os.path.join(cgroup_root, "memory", "memory.limit_in_bytes"),
        os.path.join(cgroup_root, "memory.limit_in_bytes"),
    ]
    for p in v1_paths:
        if os.path.exists(p):
            try:
                with open(p, "r") as f:
                    val = f.read().strip()
                    if val:
                        cand = int(val)
                        # cgroup v1 unconstrained limit is typically near max int64
                        if cand < (1 << 60):
                            limit_bytes = cand if limit_bytes is None else min(limit_bytes, cand)
            except (ValueError, OSError):
                pass

    if limit_bytes is not None and limit_bytes > 0:
        return limit_bytes // 1024
    return None


def derive_default_memory_budget_kb(
    operator_budget_mb: Optional[int] = None,
    total_physical_kb: Optional[int] = None,
    cgroup_limit_kb: Optional[int] = None,
) -> int:
    """Derives a safe execution memory budget in KiB.

    If operator_budget_mb is explicitly provided (> 0), returns operator_budget_mb * 1024.
    Otherwise:
      1. Determines effective host memory: the minimum of host physical RAM and any active cgroup limit.
      2. Enforces safe headroom:
         - For environments with >= 4 GiB effective memory: reserves at least 25% or 1024 MiB headroom.
         - For smaller environments (< 4 GiB): reserves at least 25% headroom (min 64 MiB for daemon RSS).
      3. Calculates available memory = effective_total - headroom.
         If available memory is non-positive, raises ValueError requiring an explicit operator budget.
      4. Caps the default budget at 4 GiB (4194304 KiB) or available memory, strictly honoring headroom.
    """
    if operator_budget_mb is not None and operator_budget_mb > 0:
        return operator_budget_mb * 1024

    # Determine host physical memory
    if total_physical_kb is None:
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            phys_pages = os.sysconf("SC_PHYS_PAGES")
            total_physical_kb = (page_size * phys_pages) // 1024
        except (AttributeError, ValueError, OSError):
            total_physical_kb = 4194304  # Fallback baseline: 4 GiB

    # Determine cgroup limit
    if cgroup_limit_kb is None:
        cgroup_limit_kb = detect_cgroup_memory_limit_kb()

    # Effective total memory is bounded by physical RAM and cgroup quota
    effective_total_kb = total_physical_kb
    if cgroup_limit_kb is not None and cgroup_limit_kb > 0:
        effective_total_kb = min(effective_total_kb, cgroup_limit_kb)

    # Calculate safe headroom:
    # On systems >= 4 GiB, reserve at least 1024 MiB (or 25%, whichever is greater).
    # On systems < 4 GiB, reserve 25% (with at least 64 MiB for the daemon process itself).
    if effective_total_kb >= 4194304:
        headroom_kb = max(1048576, effective_total_kb // 4)
    else:
        headroom_kb = max(65536, effective_total_kb // 4)

    available_kb = effective_total_kb - headroom_kb
    if available_kb <= 0:
        raise ValueError(
            f"Insufficient host or cgroup memory to derive a safe execution budget "
            f"(effective memory: {effective_total_kb} KB, required headroom: {headroom_kb} KB). "
            f"An explicit execution budget must be configured via --memory-mb."
        )

    return min(4194304, available_kb)


class ExecutionWorkerServicer(execution_pb2_grpc.ExecutionWorkerServiceServicer):
    """Asynchronous worker servicer bridging gRPC RPCs to ExecutionPipeline."""

    def __init__(
        self,
        pipeline: ExecutionPipeline,
        max_workers: int = 8,
        max_capacity: int = 16,
        max_memory_kb: Optional[int] = None,
        executor: Optional[ThreadPoolExecutor] = None,
    ) -> None:
        self.pipeline = pipeline
        self.registry = pipeline.registry
        self.max_workers = max_workers
        self.max_capacity = max_capacity
        self.max_memory_kb = (
            max_memory_kb
            if max_memory_kb is not None and max_memory_kb > 0
            else derive_default_memory_budget_kb()
        )
        self._executor = executor or ThreadPoolExecutor(max_workers=max_workers)
        self._active_workers = 0
        self._allocated_memory_kb = 0
        self._lock = asyncio.Lock()

    def _release_capacity(self, req_memory_kb: int) -> None:
        """Atomically releases worker capacity and memory allocation on the event loop."""
        self._active_workers = max(0, self._active_workers - 1)
        self._allocated_memory_kb = max(0, self._allocated_memory_kb - req_memory_kb)

    def _estimate_request_memory_kb(self, request: execution_pb2.ExecuteRequest) -> int:
        adapter = self.registry.get_by_name(request.language_id)
        compile_mem = 0
        if adapter and adapter.compile_spec:
            compile_mem = adapter.compile_spec.compile_resource_limits.memory_kb

        if request.HasField("custom_limits") and request.custom_limits.memory_kb > 0:
            exec_mem = request.custom_limits.memory_kb
        elif adapter:
            exec_mem = adapter.execution_spec.default_resource_limits.memory_kb
        else:
            exec_mem = 65536

        if adapter and adapter.family == RuntimeFamily.JVM:
            exec_mem = max(exec_mem, 196608)

        return max(compile_mem, exec_mem)

    async def ExecuteSync(
        self,
        request: execution_pb2.ExecuteRequest,
        context: grpc.aio.ServicerContext,
    ) -> execution_pb2.ExecutionResult:
        """Executes request synchronously off the asyncio event loop with admission control and cancellation."""
        req_memory_kb = self._estimate_request_memory_kb(request)
        async with self._lock:
            if self._active_workers >= self.max_capacity:
                await context.abort(
                    grpc.StatusCode.RESOURCE_EXHAUSTED,
                    f"Worker daemon capacity exceeded (active {self._active_workers} >= max {self.max_capacity}). Request rejected.",
                )
            if self._allocated_memory_kb + req_memory_kb > self.max_memory_kb:
                await context.abort(
                    grpc.StatusCode.RESOURCE_EXHAUSTED,
                    f"Worker daemon host memory capacity exceeded (requested {req_memory_kb} KB, allocated {self._allocated_memory_kb} KB of max {self.max_memory_kb} KB). Request rejected.",
                )
            self._active_workers += 1
            self._allocated_memory_kb += req_memory_kb

        cancel_token = threading.Event()

        def on_done(ctx) -> None:
            cancel_token.set()

        context.add_done_callback(on_done)

        loop = asyncio.get_running_loop()
        fut = None
        released = False

        def on_fut_done(_f) -> None:
            nonlocal released
            if not released:
                released = True
                if not loop.is_closed():
                    loop.call_soon_threadsafe(self._release_capacity, req_memory_kb)

        try:
            domain_req = proto_to_execution_request(request)
            fut = self._executor.submit(self.pipeline.execute, domain_req, None, cancel_token)
            fut.add_done_callback(on_fut_done)
            result = await asyncio.wrap_future(fut)
            return execution_result_to_proto(result)
        except Exception as exc:
            if context.code() == grpc.StatusCode.RESOURCE_EXHAUSTED:
                raise
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return execution_pb2.ExecutionResult(
                request_id=request.request_id,
                language_id=request.language_id,
                final_status=execution_pb2.MILESTONE_SANDBOX_ERROR,
                stderr=f"Internal daemon execution error: {exc}",
            )
        finally:
            cancel_token.set()
            if fut is not None:
                # Hold memory reservation and worker slot until the executor future finishes cleanup
                try:
                    await asyncio.shield(asyncio.wrap_future(fut))
                except (Exception, asyncio.CancelledError):
                    pass
            else:
                if not released:
                    released = True
                    self._release_capacity(req_memory_kb)

    async def ExecuteStream(
        self,
        request: execution_pb2.ExecuteRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[execution_pb2.ExecutionProgressEvent]:
        """Streams live milestone events in real time followed by the final execution result with cancellation support."""
        req_memory_kb = self._estimate_request_memory_kb(request)
        async with self._lock:
            if self._active_workers >= self.max_capacity:
                await context.abort(
                    grpc.StatusCode.RESOURCE_EXHAUSTED,
                    f"Worker daemon capacity exceeded (active {self._active_workers} >= max {self.max_capacity}). Request rejected.",
                )
            if self._allocated_memory_kb + req_memory_kb > self.max_memory_kb:
                await context.abort(
                    grpc.StatusCode.RESOURCE_EXHAUSTED,
                    f"Worker daemon host memory capacity exceeded (requested {req_memory_kb} KB, allocated {self._allocated_memory_kb} KB of max {self.max_memory_kb} KB). Request rejected.",
                )
            self._active_workers += 1
            self._allocated_memory_kb += req_memory_kb

        cancel_token = threading.Event()

        def on_done(ctx) -> None:
            cancel_token.set()

        context.add_done_callback(on_done)

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        fut = None
        released = False

        def on_fut_done(_f) -> None:
            nonlocal released
            if not released:
                released = True
                if not loop.is_closed():
                    loop.call_soon_threadsafe(self._release_capacity, req_memory_kb)

        def on_milestone(event: MilestoneEvent) -> None:
            if not loop.is_closed():
                loop.call_soon_threadsafe(queue.put_nowait, ("milestone", event))

        def run_execution(req: ExecutionRequest) -> ExecutionResult:
            return self.pipeline.execute(req, milestone_callback=on_milestone, cancel_token=cancel_token)

        try:
            domain_req = proto_to_execution_request(request)
            fut = self._executor.submit(run_execution, domain_req)
            fut.add_done_callback(on_fut_done)

            def on_done(f) -> None:
                try:
                    res = f.result()
                    if not loop.is_closed():
                        loop.call_soon_threadsafe(queue.put_nowait, ("result", res))
                except Exception as err:
                    if not loop.is_closed():
                        loop.call_soon_threadsafe(queue.put_nowait, ("error", err))

            fut.add_done_callback(on_done)

            while True:
                # Check for client cancellation
                if context.done() or cancel_token.is_set():
                    cancel_token.set()
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
            cancel_token.set()
            if fut is not None:
                # Hold memory reservation and worker slot until the executor future finishes cleanup
                try:
                    await asyncio.shield(asyncio.wrap_future(fut))
                except (Exception, asyncio.CancelledError):
                    pass
            else:
                if not released:
                    released = True
                    self._release_capacity(req_memory_kb)

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
