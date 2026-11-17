"""Command-line interface for invoking the PES 2 Runtime Adapter Engine."""

import argparse
import json
import sys
import uuid
from typing import Optional, Tuple

from engine.models import (
    ExecutionRequest,
    ExecutionResult,
    LanguageId,
    MAX_RUNNER_OUTPUT_BYTES,
    MAX_SOURCE_BYTES,
    Milestone,
    ResourceLimits,
    RUNNER_MAX_MEMORY_KB,
    RUNNER_MAX_PIDS,
    RUNNER_MAX_TIMEOUT_MS,
    RUNNER_MIN_MEMORY_KB,
    RUNNER_MIN_PIDS,
    RUNNER_MIN_TIMEOUT_MS,
)
from engine.pipeline import ExecutionPipeline
from engine.registry import RuntimeRegistry
from engine.runner import MockSandboxRunner, NativeSandboxRunner, RunnerInterface


def read_bounded_source_file(file_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Reads at most MAX_SOURCE_BYTES + 1 bytes from file in binary mode.
    Rejects oversized input without loading the entire file into memory.
    Validates UTF-8 encoding.
    """
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(MAX_SOURCE_BYTES + 1)
    except OSError as err:
        return None, f"Error reading source file '{file_path}': {err}"

    if len(chunk) > MAX_SOURCE_BYTES:
        return None, f"Source file '{file_path}' exceeds maximum allowed size ({MAX_SOURCE_BYTES} bytes)."

    try:
        return chunk.decode("utf-8"), None
    except UnicodeDecodeError as err:
        return None, f"Invalid UTF-8 encoding in source file '{file_path}': {err}"


def read_bounded_source_stream(stream) -> Tuple[Optional[str], Optional[str]]:
    """Reads at most MAX_SOURCE_BYTES + 1 bytes from stream buffer in binary mode.
    Rejects oversized input without loading the entire stream into memory.
    Validates UTF-8 encoding.
    """
    try:
        buffer = getattr(stream, "buffer", stream)
        chunk = buffer.read(MAX_SOURCE_BYTES + 1)
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
    except Exception as err:
        return None, f"Error reading standard input: {err}"

    if len(chunk) > MAX_SOURCE_BYTES:
        return None, f"Standard input source exceeds maximum allowed size ({MAX_SOURCE_BYTES} bytes)."

    try:
        return chunk.decode("utf-8"), None
    except UnicodeDecodeError as err:
        return None, f"Invalid UTF-8 encoding in standard input: {err}"


def validate_resource_limits(
    wall_ms: Optional[int],
    cpu_ms: Optional[int],
    memory_kb: Optional[int],
    pids: Optional[int],
    max_output_bytes: Optional[int],
) -> Optional[str]:
    """Validates that custom resource limits are positive and within runner bounds."""
    if wall_ms is not None:
        if wall_ms < RUNNER_MIN_TIMEOUT_MS or wall_ms > RUNNER_MAX_TIMEOUT_MS:
            return f"Invalid wall-ms ({wall_ms}): must be between {RUNNER_MIN_TIMEOUT_MS} and {RUNNER_MAX_TIMEOUT_MS} ms"
    if cpu_ms is not None:
        if cpu_ms < RUNNER_MIN_TIMEOUT_MS or cpu_ms > RUNNER_MAX_TIMEOUT_MS:
            return f"Invalid cpu-ms ({cpu_ms}): must be between {RUNNER_MIN_TIMEOUT_MS} and {RUNNER_MAX_TIMEOUT_MS} ms"
    if memory_kb is not None:
        if memory_kb < RUNNER_MIN_MEMORY_KB or memory_kb > RUNNER_MAX_MEMORY_KB:
            return f"Invalid memory-kb ({memory_kb}): must be between {RUNNER_MIN_MEMORY_KB} and {RUNNER_MAX_MEMORY_KB} KiB"
    if pids is not None:
        if pids < RUNNER_MIN_PIDS or pids > RUNNER_MAX_PIDS:
            return f"Invalid pids ({pids}): must be between {RUNNER_MIN_PIDS} and {RUNNER_MAX_PIDS}"
    if max_output_bytes is not None:
        if max_output_bytes < 1 or max_output_bytes > MAX_RUNNER_OUTPUT_BYTES:
            return f"Invalid max-output-bytes ({max_output_bytes}): must be between 1 and {MAX_RUNNER_OUTPUT_BYTES} bytes"
    return None


def result_to_dict(result: ExecutionResult) -> dict:
    return {
        "request_id": result.request_id,
        "language_id": result.language_id.value,
        "final_status": result.final_status.value,
        "milestones": [
            {
                "milestone": m.milestone.value,
                "timestamp_ms": m.timestamp_ms,
                "details": m.details,
            }
            for m in result.milestones
        ],
        "exit_code": result.exit_code,
        "signal": result.signal,
        "wall_ms": result.wall_ms,
        "cpu_ms": result.cpu_ms,
        "peak_memory_kb": result.peak_memory_kb,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "compile_output": result.compile_output,
    }


def main(args_list: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="PolyExecution Studio 2 Adapter Engine CLI")
    parser.add_argument("--language", "-l", required=True, help="Target language (e.g. c, cpp, python3)")
    parser.add_argument("--source", "-s", help="Source code string")
    parser.add_argument("--source-file", "-f", help="Path to file containing source code")
    parser.add_argument("--stdin", default="", help="Standard input passed to target execution")
    parser.add_argument("--request-id", default="", help="Optional unique request identifier")
    parser.add_argument("--runner", choices=["native", "mock"], default="native", help="Execution runner backend")
    parser.add_argument("--cgroup-root", default=None, help="Explicit cgroups v2 root path")
    parser.add_argument("--wall-ms", type=int, default=None, help="Wall-clock timeout in milliseconds")
    parser.add_argument("--cpu-ms", type=int, default=None, help="CPU time timeout in milliseconds")
    parser.add_argument("--memory-kb", type=int, default=None, help="Memory ceiling in kilobytes")
    parser.add_argument("--pids", type=int, default=None, help="Maximum thread/process count")
    parser.add_argument("--max-output-bytes", type=int, default=None, help="Maximum captured stdout/stderr bytes")

    args = parser.parse_args(args_list)

    # Validate custom resource limits early if provided
    limit_err = validate_resource_limits(
        wall_ms=args.wall_ms,
        cpu_ms=args.cpu_ms,
        memory_kb=args.memory_kb,
        pids=args.pids,
        max_output_bytes=args.max_output_bytes,
    )
    if limit_err:
        sys.stderr.write(f"Error: {limit_err}\n")
        return 1

    # Resolve source code with bounded reads
    source_code = ""
    if args.source_file:
        source_code_opt, err = read_bounded_source_file(args.source_file)
        if err or source_code_opt is None:
            sys.stderr.write(f"Error: {err}\n")
            return 1
        source_code = source_code_opt
    elif args.source is not None:
        source_bytes = args.source.encode("utf-8")
        if len(source_bytes) > MAX_SOURCE_BYTES:
            sys.stderr.write(f"Error: Source argument exceeds maximum allowed size ({MAX_SOURCE_BYTES} bytes).\n")
            return 1
        source_code = args.source
    elif not sys.stdin.isatty():
        source_code_opt, err = read_bounded_source_stream(sys.stdin)
        if err or source_code_opt is None:
            sys.stderr.write(f"Error: {err}\n")
            return 1
        source_code = source_code_opt
    else:
        sys.stderr.write("Error: Either --source, --source-file, or standard input must be provided.\n")
        return 1

    # Validate language
    try:
        lang_id = LanguageId(args.language.lower())
    except ValueError:
        sys.stderr.write(f"Error: Unknown or unsupported language '{args.language}'.\n")
        return 1

    registry = RuntimeRegistry()
    adapter = registry.get(lang_id)
    if adapter is None:
        sys.stderr.write(f"Error: Unknown or unsupported language '{args.language}'.\n")
        return 1

    # Resolve runner backend
    runner: RunnerInterface
    if args.runner == "mock":
        runner = MockSandboxRunner()
    else:
        runner = NativeSandboxRunner(cgroup_root=args.cgroup_root)

    pipeline = ExecutionPipeline(registry, runner)

    # Build custom limits preserving adapter defaults for unspecified fields
    custom_limits: Optional[ResourceLimits] = None
    has_custom = any(v is not None for v in [args.wall_ms, args.cpu_ms, args.memory_kb, args.pids, args.max_output_bytes])
    if has_custom:
        base_limits = adapter.execution_spec.default_resource_limits
        custom_limits = ResourceLimits(
            wall_ms=args.wall_ms if args.wall_ms is not None else base_limits.wall_ms,
            cpu_ms=args.cpu_ms if args.cpu_ms is not None else base_limits.cpu_ms,
            memory_kb=args.memory_kb if args.memory_kb is not None else base_limits.memory_kb,
            pids=args.pids if args.pids is not None else base_limits.pids,
            max_output_bytes=args.max_output_bytes if args.max_output_bytes is not None else base_limits.max_output_bytes,
        )

    req_id = args.request_id or f"req-{uuid.uuid4().hex[:8]}"
    request = ExecutionRequest(
        request_id=req_id,
        language_id=lang_id,
        source_code=source_code,
        stdin=args.stdin,
        custom_limits=custom_limits,
    )

    result = pipeline.execute(request)
    print(json.dumps(result_to_dict(result), indent=2))

    return 0 if result.final_status == Milestone.COMPLETED else 1


if __name__ == "__main__":
    sys.exit(main())
