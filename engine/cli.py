"""Command-line interface for invoking the PES 2 Runtime Adapter Engine."""

import argparse
import json
import sys
import uuid
from typing import Optional

from engine.models import (
    ExecutionRequest,
    ExecutionResult,
    LanguageId,
    Milestone,
    ResourceLimits,
)
from engine.pipeline import ExecutionPipeline
from engine.registry import RuntimeRegistry
from engine.runner import MockSandboxRunner, NativeSandboxRunner, RunnerInterface


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

    # Resolve source code
    source_code = ""
    if args.source_file:
        try:
            with open(args.source_file, "r", encoding="utf-8") as f:
                source_code = f.read()
        except OSError as err:
            sys.stderr.write(f"Error reading source file: {err}\n")
            return 1
    elif args.source is not None:
        source_code = args.source
    elif not sys.stdin.isatty():
        source_code = sys.stdin.read()
    else:
        sys.stderr.write("Error: Either --source, --source-file, or standard input must be provided.\n")
        return 1

    # Validate language
    try:
        lang_id = LanguageId(args.language.lower())
    except ValueError:
        sys.stderr.write(f"Error: Unknown or unsupported language '{args.language}'.\n")
        return 1

    # Resolve runner backend
    runner: RunnerInterface
    if args.runner == "mock":
        runner = MockSandboxRunner()
    else:
        runner = NativeSandboxRunner(cgroup_root=args.cgroup_root)

    registry = RuntimeRegistry()
    pipeline = ExecutionPipeline(registry, runner)

    # Build custom limits if specified
    custom_limits: Optional[ResourceLimits] = None
    if any(v is not None for v in [args.wall_ms, args.cpu_ms, args.memory_kb, args.pids, args.max_output_bytes]):
        custom_limits = ResourceLimits(
            wall_ms=args.wall_ms if args.wall_ms is not None else 2000,
            cpu_ms=args.cpu_ms if args.cpu_ms is not None else 1000,
            memory_kb=args.memory_kb if args.memory_kb is not None else 65536,
            pids=args.pids if args.pids is not None else 32,
            max_output_bytes=args.max_output_bytes if args.max_output_bytes is not None else 65536,
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
