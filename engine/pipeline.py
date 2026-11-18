"""Execution pipeline orchestrating compilation, execution, and truthful milestone events."""

import os
import shutil
import tempfile
import threading
import time
from typing import Callable, List, Optional

from engine.models import (
    ExecutionRequest,
    ExecutionResult,
    LanguageId,
    MAX_RUNNER_OUTPUT_BYTES,
    MAX_SOURCE_BYTES,
    Milestone,
    MilestoneEvent,
    ResourceLimits,
    RuntimeAdapter,
)
from engine.registry import RuntimeRegistry
from engine.runner import RunnerInterface


def current_time_ms() -> int:
    return int(time.time() * 1000)


def map_runner_status(status_str: str) -> Milestone:
    mapping = {
        "OK": Milestone.COMPLETED,
        "RUNTIME_ERROR": Milestone.RUNTIME_ERROR,
        "TIMEOUT": Milestone.TIMEOUT,
        "OUTPUT_LIMIT": Milestone.OUTPUT_LIMIT,
        "MEMORY_LIMIT": Milestone.MEMORY_LIMIT,
        "BLOCKED_BY_POLICY": Milestone.BLOCKED_BY_POLICY,
        "SANDBOX_ERROR": Milestone.SANDBOX_ERROR,
        "CANCELLED": Milestone.CANCELLED,
    }
    return mapping.get(status_str.upper(), Milestone.SANDBOX_ERROR)


class ExecutionPipeline:
    """Orchestrates multi-stage compilation and execution pipelines."""

    def __init__(self, registry: RuntimeRegistry, runner: RunnerInterface) -> None:
        self.registry = registry
        self.runner = runner

    def execute(
        self,
        request: ExecutionRequest,
        milestone_callback: Optional[Callable[[MilestoneEvent], None]] = None,
        cancel_token: Optional[threading.Event] = None,
    ) -> ExecutionResult:
        milestones: List[MilestoneEvent] = []

        def emit(event: MilestoneEvent) -> None:
            milestones.append(event)
            if milestone_callback:
                try:
                    milestone_callback(event)
                except Exception:
                    pass

        # Pre-check 1: Bounded source-code size before writing to temporary storage
        source_bytes = request.source_code.encode("utf-8")
        if len(source_bytes) > MAX_SOURCE_BYTES:
            now = current_time_ms()
            ev = MilestoneEvent(
                Milestone.SANDBOX_ERROR,
                now,
                f"Source code size ({len(source_bytes)} bytes) exceeds limit ({MAX_SOURCE_BYTES} bytes)",
            )
            emit(ev)
            return ExecutionResult(
                request_id=request.request_id,
                language_id=request.language_id,
                final_status=Milestone.SANDBOX_ERROR,
                milestones=list(milestones),
                exit_code=1,
                signal=0,
                wall_ms=0,
                cpu_ms=0,
                peak_memory_kb=0,
                stdout="",
                stderr=f"Source code size ({len(source_bytes)} bytes) exceeds maximum allowed limit ({MAX_SOURCE_BYTES} bytes)",
            )

        # Pre-check 2: Adapter existence and installation
        adapter = self.registry.get(request.language_id)
        if adapter is None:
            now = current_time_ms()
            ev = MilestoneEvent(Milestone.SANDBOX_ERROR, now, f"Unsupported language: {request.language_id}")
            emit(ev)
            return ExecutionResult(
                request_id=request.request_id,
                language_id=request.language_id,
                final_status=Milestone.SANDBOX_ERROR,
                milestones=list(milestones),
                exit_code=1,
                signal=0,
                wall_ms=0,
                cpu_ms=0,
                peak_memory_kb=0,
                stdout="",
                stderr=f"Runtime adapter for '{request.language_id.value}' not found in registry",
            )

        if not adapter.is_installed:
            now = current_time_ms()
            ev = MilestoneEvent(
                Milestone.SANDBOX_ERROR,
                now,
                f"Language '{request.language_id.value}' not installed on host: {adapter.installed_version}",
            )
            emit(ev)
            return ExecutionResult(
                request_id=request.request_id,
                language_id=request.language_id,
                final_status=Milestone.SANDBOX_ERROR,
                milestones=list(milestones),
                exit_code=1,
                signal=0,
                wall_ms=0,
                cpu_ms=0,
                peak_memory_kb=0,
                stdout="",
                stderr=f"Toolchain for '{request.language_id.value}' is not installed on this host ({adapter.installed_version})",
            )

        # Pre-check 3: Validate requested custom limits against runner capabilities
        exec_limits = request.custom_limits or adapter.execution_spec.default_resource_limits
        if exec_limits.max_output_bytes > MAX_RUNNER_OUTPUT_BYTES:
            now = current_time_ms()
            ev = MilestoneEvent(
                Milestone.SANDBOX_ERROR,
                now,
                f"Requested max_output_bytes ({exec_limits.max_output_bytes}) exceeds runner cap ({MAX_RUNNER_OUTPUT_BYTES})",
            )
            emit(ev)
            return ExecutionResult(
                request_id=request.request_id,
                language_id=request.language_id,
                final_status=Milestone.SANDBOX_ERROR,
                milestones=list(milestones),
                exit_code=1,
                signal=0,
                wall_ms=0,
                cpu_ms=0,
                peak_memory_kb=0,
                stdout="",
                stderr=f"Requested max_output_bytes ({exec_limits.max_output_bytes}) exceeds runner cap ({MAX_RUNNER_OUTPUT_BYTES} bytes)",
            )

        workspace = tempfile.mkdtemp(prefix="pes2-work-")
        os.chmod(workspace, 0o700)

        try:
            # Milestone: ACCEPTED
            emit(MilestoneEvent(Milestone.ACCEPTED, current_time_ms(), "Request accepted and workspace provisioned"))

            # Determine source filename and artifact path
            if adapter.compile_spec:
                source_rel = adapter.compile_spec.source_filename
                artifact_rel = adapter.compile_spec.artifact_output_name
            else:
                source_rel = adapter.execution_spec.source_filename or "solution.py"
                artifact_rel = None

            source_path = os.path.join(workspace, source_rel)
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(request.source_code)

            compile_output: Optional[str] = None

            # Stage 1: Compile if CompileSpec present (e.g. C, C++, or Bash syntax validation)
            if adapter.compile_spec:
                artifact_path = os.path.join(workspace, artifact_rel) if artifact_rel else None
                # Stale artifact defense: ensure destination binary does not exist
                # (Skipped when artifact_rel == source_rel, which occurs in syntax validation passes)
                if artifact_path and artifact_rel != source_rel and os.path.exists(artifact_path):
                    os.remove(artifact_path)

                emit(MilestoneEvent(Milestone.COMPILING, current_time_ms(), "Compilation initiated"))

                compile_res = self.runner.run(
                    workspace=workspace,
                    target_bin=adapter.compile_spec.compiler_bin,
                    target_argv=adapter.compile_spec.compile_args,
                    limits=adapter.compile_spec.compile_resource_limits,
                    stdin_content="",
                    seccomp_profile=adapter.compile_spec.seccomp_profile,
                    cancel_token=cancel_token,
                )

                # Check if compilation failed
                compile_failed = False
                compile_milestone = Milestone.COMPILE_ERROR
                compile_output = None

                if compile_res.status == "OK" and compile_res.exit_code == 0:
                    if artifact_path and not os.path.exists(artifact_path):
                        compile_failed = True
                        compile_milestone = Milestone.COMPILE_ERROR
                        compile_output = (compile_res.stderr or compile_res.stdout).strip()
                        if not compile_output:
                            compile_output = f"Compiler exited successfully but produced no artifact at {artifact_rel}"
                elif compile_res.status == "RUNTIME_ERROR":
                    # Actual compiler diagnostic (syntax error, linker error, non-zero exit)
                    compile_failed = True
                    compile_milestone = Milestone.COMPILE_ERROR
                    compile_output = (compile_res.stderr or compile_res.stdout).strip()
                else:
                    # Sandbox outcome: TIMEOUT, MEMORY_LIMIT, OUTPUT_LIMIT, BLOCKED_BY_POLICY, SANDBOX_ERROR, CANCELLED
                    compile_failed = True
                    compile_milestone = map_runner_status(compile_res.status)
                    compile_output = (compile_res.stderr or compile_res.stdout).strip()

                if compile_failed:
                    # Stale artifact defense: guarantee any partial artifact is purged if distinct from source
                    if artifact_path and artifact_rel != source_rel and os.path.exists(artifact_path):
                        try:
                            os.remove(artifact_path)
                        except OSError:
                            pass

                    emit(
                        MilestoneEvent(compile_milestone, current_time_ms(), f"Compilation stopped: {compile_milestone.value}")
                    )
                    return ExecutionResult(
                        request_id=request.request_id,
                        language_id=request.language_id,
                        final_status=compile_milestone,
                        milestones=list(milestones),
                        exit_code=compile_res.exit_code if compile_res.exit_code != 0 else 1,
                        signal=compile_res.signal,
                        wall_ms=compile_res.wall_ms,
                        cpu_ms=compile_res.cpu_ms,
                        peak_memory_kb=compile_res.peak_memory_kb,
                        stdout=compile_res.stdout,
                        stderr=compile_res.stderr,
                        compile_output=compile_output,
                    )

            # Check if cancelled before starting execution stage
            if cancel_token and cancel_token.is_set():
                emit(MilestoneEvent(Milestone.CANCELLED, current_time_ms(), "Execution cancelled by client"))
                return ExecutionResult(
                    request_id=request.request_id,
                    language_id=request.language_id,
                    final_status=Milestone.CANCELLED,
                    milestones=list(milestones),
                    exit_code=130,
                    signal=15,
                    wall_ms=0,
                    cpu_ms=0,
                    peak_memory_kb=0,
                    stdout="",
                    stderr="Execution cancelled by client",
                    compile_output=compile_output,
                )

            # Stage 2: Execute (C/C++ runtime binary or Python interpreter)
            # Interpreted languages emit RUNNING directly after ACCEPTED without synthetic COMPILING
            emit(MilestoneEvent(Milestone.RUNNING, current_time_ms(), "Target execution started"))

            exec_res = self.runner.run(
                workspace=workspace,
                target_bin=adapter.execution_spec.runner_bin,
                target_argv=adapter.execution_spec.execution_args,
                limits=exec_limits,
                stdin_content=request.stdin,
                seccomp_profile=adapter.execution_spec.seccomp_profile,
                cancel_token=cancel_token,
            )

            stdout = exec_res.stdout
            stderr = exec_res.stderr
            final_status = map_runner_status(exec_res.status)

            # Software output bounding if custom limit is smaller than runner's 64 KiB cap
            total_bytes = len(stdout.encode("utf-8")) + len(stderr.encode("utf-8"))
            if final_status in (Milestone.COMPLETED, Milestone.RUNTIME_ERROR) and total_bytes > exec_limits.max_output_bytes:
                final_status = Milestone.OUTPUT_LIMIT
                raw_stdout = stdout.encode("utf-8")
                if len(raw_stdout) > exec_limits.max_output_bytes:
                    stdout = raw_stdout[:exec_limits.max_output_bytes].decode("utf-8", errors="replace")
                    stderr = ""
                else:
                    remaining = exec_limits.max_output_bytes - len(raw_stdout)
                    stderr = stderr.encode("utf-8")[:remaining].decode("utf-8", errors="replace")

            emit(MilestoneEvent(final_status, current_time_ms(), f"Execution completed with {final_status.value}"))

            return ExecutionResult(
                request_id=request.request_id,
                language_id=request.language_id,
                final_status=final_status,
                milestones=milestones,
                exit_code=exec_res.exit_code,
                signal=exec_res.signal,
                wall_ms=exec_res.wall_ms,
                cpu_ms=exec_res.cpu_ms,
                peak_memory_kb=exec_res.peak_memory_kb,
                stdout=stdout,
                stderr=stderr,
                compile_output=compile_output,
            )

        finally:
            if adapter.cleanup_spec.ephemeral_workspace:
                if os.path.exists(workspace):
                    shutil.rmtree(workspace, ignore_errors=True)
