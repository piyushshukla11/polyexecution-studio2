"""Asynchronous gRPC server setup and lifecycle management for PES 2 Worker Daemon."""

import argparse
import asyncio
import logging
import os
import sys
from typing import Optional, Tuple

import grpc

from engine.daemon.service import ExecutionWorkerServicer
from engine.pipeline import ExecutionPipeline
from engine.registry import RuntimeRegistry
from engine.runner import NativeSandboxRunner
from proto import execution_pb2_grpc


logger = logging.getLogger("pes2.daemon")

ALLOWED_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def create_server(
    host: str = "127.0.0.1",
    port: int = 50051,
    max_workers: int = 8,
    max_capacity: int = 16,
    pipeline: Optional[ExecutionPipeline] = None,
) -> Tuple[grpc.aio.Server, int]:
    """Creates and configures an asynchronous gRPC worker daemon server.
    Enforces loopback-only binding to prevent unauthenticated remote exposure.
    """
    if host not in ALLOWED_LOOPBACK_HOSTS:
        raise ValueError(
            f"Security violation: Binding to non-loopback host '{host}' is prohibited until authentication and TLS are implemented."
        )

    if pipeline is None:
        registry = RuntimeRegistry()
        runner = NativeSandboxRunner(runner_path="sandbox/pes2-runner")
        pipeline = ExecutionPipeline(registry=registry, runner=runner)

    servicer = ExecutionWorkerServicer(pipeline=pipeline, max_workers=max_workers, max_capacity=max_capacity)

    server = grpc.aio.server(
        options=[
            ("grpc.max_receive_message_length", 64 * 1024 * 1024),
            ("grpc.max_send_message_length", 64 * 1024 * 1024),
        ]
    )
    execution_pb2_grpc.add_ExecutionWorkerServiceServicer_to_server(servicer, server)

    listen_addr = f"{host}:{port}"
    bound_port = server.add_insecure_port(listen_addr)
    return server, bound_port


async def run_server(
    host: str = "127.0.0.1",
    port: int = 50051,
    max_workers: int = 8,
    max_capacity: int = 16,
    pipeline: Optional[ExecutionPipeline] = None,
) -> None:
    """Starts the gRPC server and waits until terminated."""
    server, bound_port = create_server(
        host=host,
        port=port,
        max_workers=max_workers,
        max_capacity=max_capacity,
        pipeline=pipeline,
    )
    await server.start()
    logger.info("PES 2 Worker Daemon listening on %s:%d", host, bound_port)

    try:
        await server.wait_for_termination()
    except asyncio.CancelledError:
        logger.info("Daemon received cancellation, shutting down...")
        await server.stop(grace=2.0)


def main(args_list: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="PolyExecution Studio 2 Worker Daemon")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host address (loopback only: 127.0.0.1, localhost, ::1)")
    parser.add_argument("--port", type=int, default=50051, help="Bind port (0 for random ephemeral port)")
    parser.add_argument("--workers", type=int, default=8, help="Maximum worker pool concurrency")
    parser.add_argument("--capacity", type=int, default=16, help="Maximum total active and queued request capacity")
    parser.add_argument("--cgroup-root", default=None, help="Explicit cgroups v2 root path")

    args = parser.parse_args(args_list)

    if args.host not in ALLOWED_LOOPBACK_HOSTS:
        sys.stderr.write(
            f"Error: Security restriction: Binding to non-loopback host '{args.host}' is prohibited until authentication and TLS are implemented.\n"
        )
        return 2

    if args.cgroup_root:
        os.environ["PES2_CGROUP_ROOT"] = args.cgroup_root

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        asyncio.run(
            run_server(
                host=args.host,
                port=args.port,
                max_workers=args.workers,
                max_capacity=args.capacity,
            )
        )
        return 0
    except KeyboardInterrupt:
        logger.info("Daemon interrupted by user")
        return 0
    except Exception as exc:
        logger.error("Daemon fatal error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
