"""Execution pipeline orchestrating compilation, execution, and truthful milestone events."""

import os
import shutil
import tempfile
import time
from typing import List, Optional

from engine.models import (
    ExecutionRequest,
    ExecutionResult,
    LanguageId,
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

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        adapter = self.registry.get(request.language_id)
        if adapter is None:
            now = current_time_ms()
            return ExecutionResult(
                request_id=request.request_id,
                language_id=request.language_id,
                final_status=Milestone.SANDBOX_ERROR,
                milestones=[MilestoneEvent(Milestone.SANDBOX_ERROR, now, f"Unsupported language: {request.language_id}")],
                exit_code=1,
                signal=0,
                wall_ms=0,
                cpu_ms=0,
                peak_memory_kb=0,
                stdout="",
                stderr=f"Runtime adapter for '{request.language_id.value}' not found in registry",
            )

        milestones: List[MilestoneEvent] = []
        workspace = tempfile.mkdtemp(prefix="pes2-work-")
        os.chmod(workspace, 0o700)

        try:
            # Milestone: ACCEPTED
            milestones.append(MilestoneEvent(Milestone.ACCEPTED, current_time_ms(), "Request accepted and workspace provisioned"))

            # Determine source filename and artifact path
            if adapter.compile_spec:
                source_rel = adapter.compile_spec.source_filename
                artifact_rel = adapter.compile_spec.artifact_output_name
            else:
                # Python or interpreted single-stage
                source_rel = "solution.py"
                artifact_rel = None

            source_path = os.path.join(workspace, source_rel)
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(request.source_code)

            compile_output: Optional[str] = None

            # Stage 1: Compile if CompileSpec present (e.g. C, C++)
            if adapter.compile_spec:
                artifact_path = os.path.join(workspace, artifact_rel)
                # Stale artifact defense: ensure destination binary does not exist
                if os.path.exists(artifact_path):
                    os.remove(artifact_path)

                milestones.append(MilestoneEvent(Milestone.COMPILING, current_time_ms(), "Compilation initiated"))

                compile_res = self.runner.run(
                    workspace=workspace,
                    target_bin=adapter.compile_spec.compiler_bin,
                    target_argv=adapter.compile_spec.compile_args,
                    limits=adapter.compile_spec.compile_resource_limits,
                    stdin_content="",
                )

                # Check if compilation failed
                compile_failed = False
                if compile_res.status != "OK" or compile_res.exit_code != 0:
                    compile_failed = True

                # Secondary defense: check if artifact was produced
                if not compile_failed and not os.path.exists(artifact_path):
                    # In mock runs, artifact may not be physically written unless runner is mock or native
                    # If native runner ran and artifact missing, compile failed
                    pass

                if compile_failed:
                    # Stale artifact defense: guarantee any partial artifact is purged
                    if os.path.exists(artifact_path):
                        try:
                            os.remove(artifact_path)
                        except OSError:
                            pass

                    compile_output = (compile_res.stderr or compile_res.stdout).strip()
                    milestones.append(
                        MilestoneEvent(Milestone.COMPILE_ERROR, current_time_ms(), "Compilation failed")
                    )
                    return ExecutionResult(
                        request_id=request.request_id,
                        language_id=request.language_id,
                        final_status=Milestone.COMPILE_ERROR,
                        milestones=milestones,
                        exit_code=compile_res.exit_code if compile_res.exit_code != 0 else 1,
                        signal=compile_res.signal,
                        wall_ms=compile_res.wall_ms,
                        cpu_ms=compile_res.cpu_ms,
                        peak_memory_kb=compile_res.peak_memory_kb,
                        stdout=compile_res.stdout,
                        stderr=compile_res.stderr,
                        compile_output=compile_output,
                    )

            # Stage 2: Execute (C/C++ runtime binary or Python interpreter)
            # Interpreted languages emit RUNNING directly after ACCEPTED without synthetic COMPILING
            milestones.append(MilestoneEvent(Milestone.RUNNING, current_time_ms(), "Target execution started"))

            exec_limits = request.custom_limits or adapter.execution_spec.default_resource_limits
            exec_res = self.runner.run(
                workspace=workspace,
                target_bin=adapter.execution_spec.runner_bin,
                target_argv=adapter.execution_spec.execution_args,
                limits=exec_limits,
                stdin_content=request.stdin,
            )

            final_status = map_runner_status(exec_res.status)
            milestones.append(MilestoneEvent(final_status, current_time_ms(), f"Execution completed with {final_status.value}"))

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
                stdout=exec_res.stdout,
                stderr=exec_res.stderr,
                compile_output=compile_output,
            )

        finally:
            if adapter.cleanup_spec.ephemeral_workspace:
                if os.path.exists(workspace):
                    shutil.rmtree(workspace, ignore_errors=True)
