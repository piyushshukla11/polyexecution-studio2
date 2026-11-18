"""Integration tests for the asynchronous gRPC Worker Daemon."""

import asyncio
import os
import unittest

import grpc

from engine.daemon.server import create_server
from engine.models import LanguageId, Milestone, ResourceLimits, RunnerResult
from engine.pipeline import ExecutionPipeline
from engine.registry import RuntimeRegistry
from engine.runner import MockSandboxRunner, NativeSandboxRunner
from proto import execution_pb2, execution_pb2_grpc


class DaemonMockRunner(MockSandboxRunner):
    """Produces mock output artifacts for compiled languages when needed."""

    def run(self, workspace, target_bin, target_argv, limits, stdin_content="", **kwargs):
        res = super().run(workspace, target_bin, target_argv, limits, stdin_content, **kwargs)
        if res.status == "OK" and res.exit_code == 0:
            if target_bin.endswith("gcc") or target_bin.endswith("clang++") or target_bin.endswith("g++"):
                out_path = os.path.join(workspace, "a.out")
                with open(out_path, "w") as f:
                    f.write("#!/bin/sh\n")
                os.chmod(out_path, 0o755)
        return res


class TestWorkerDaemon(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.registry = RuntimeRegistry()
        self.runner = DaemonMockRunner()
        self.pipeline = ExecutionPipeline(registry=self.registry, runner=self.runner)

        # Start daemon on random ephemeral port (port 0)
        self.server, self.port = create_server(
            host="127.0.0.1",
            port=0,
            max_workers=4,
            pipeline=self.pipeline,
        )
        await self.server.start()

        # Connect gRPC client channel
        self.channel = grpc.aio.insecure_channel(f"127.0.0.1:{self.port}")
        self.stub = execution_pb2_grpc.ExecutionWorkerServiceStub(self.channel)

    async def asyncTearDown(self) -> None:
        await self.channel.close()
        await self.server.stop(grace=1.0)

    async def test_health_check(self) -> None:
        req = execution_pb2.HealthCheckRequest()
        resp = await self.stub.HealthCheck(req)
        self.assertEqual(resp.status, execution_pb2.HealthCheckResponse.SERVING)
        self.assertEqual(resp.runner_version, "pes2-runner-2.0")
        self.assertEqual(resp.active_workers, 0)

    async def test_get_supported_runtimes(self) -> None:
        req = execution_pb2.GetSupportedRuntimesRequest()
        resp = await self.stub.GetSupportedRuntimes(req)

        runtime_map = {r.id: r for r in resp.runtimes}
        self.assertIn("c", runtime_map)
        self.assertIn("cpp", runtime_map)
        self.assertIn("python3", runtime_map)
        self.assertIn("bash", runtime_map)
        self.assertIn("go", runtime_map)
        self.assertIn("rust", runtime_map)

        # Bash verified installed
        self.assertTrue(runtime_map["bash"].is_installed)
        self.assertEqual(runtime_map["bash"].display_name, "Bash")

        # Go and Rust verified uninstalled
        self.assertFalse(runtime_map["go"].is_installed)
        self.assertEqual(runtime_map["go"].installed_version, "Not installed on host")
        self.assertFalse(runtime_map["rust"].is_installed)
        self.assertEqual(runtime_map["rust"].installed_version, "Not installed on host")

    async def test_execute_sync_success(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 10, 5, 2048, "daemon sync ok\n", "")
        )

        req = execution_pb2.ExecuteRequest(
            request_id="req-sync-1",
            language_id="python3",
            source_code="print('daemon sync ok')\n",
        )
        resp = await self.stub.ExecuteSync(req)

        self.assertEqual(resp.final_status, execution_pb2.MILESTONE_COMPLETED)
        self.assertEqual(resp.exit_code, 0)
        self.assertEqual(resp.stdout, "daemon sync ok\n")
        self.assertEqual(resp.request_id, "req-sync-1")

    async def test_execute_stream_milestones_c(self) -> None:
        # C compile success, execution success
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 100, 80, 16384, "", "")
        )
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 15, 10, 4096, "hello c stream\n", "")
        )

        req = execution_pb2.ExecuteRequest(
            request_id="req-stream-c",
            language_id="c",
            source_code="#include <stdio.h>\nint main() { printf(\"hello c stream\\n\"); return 0; }\n",
        )

        events = []
        async for progress in self.stub.ExecuteStream(req):
            events.append(progress)

        # Expected events: 4 MilestoneEvents (ACCEPTED, COMPILING, RUNNING, COMPLETED), followed by result
        milestone_events = [e.milestone_event.milestone for e in events if e.HasField("milestone_event")]
        expected_milestones = [
            execution_pb2.MILESTONE_ACCEPTED,
            execution_pb2.MILESTONE_COMPILING,
            execution_pb2.MILESTONE_RUNNING,
            execution_pb2.MILESTONE_COMPLETED,
        ]
        self.assertEqual(milestone_events, expected_milestones)

        # Final event is the result
        self.assertTrue(events[-1].HasField("result"))
        result = events[-1].result
        self.assertEqual(result.final_status, execution_pb2.MILESTONE_COMPLETED)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "hello c stream\n")

    async def test_execute_stream_compile_error_bash(self) -> None:
        syntax_err = "/workspace/solution.sh: line 1: syntax error near unexpected token `then'"
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 2, 0, 10, 5, 1024, "", syntax_err)
        )

        req = execution_pb2.ExecuteRequest(
            request_id="req-stream-bash-err",
            language_id="bash",
            source_code="if then\n",
        )

        events = []
        async for progress in self.stub.ExecuteStream(req):
            events.append(progress)

        milestone_events = [e.milestone_event.milestone for e in events if e.HasField("milestone_event")]
        expected_milestones = [
            execution_pb2.MILESTONE_ACCEPTED,
            execution_pb2.MILESTONE_COMPILING,
            execution_pb2.MILESTONE_COMPILE_ERROR,
        ]
        self.assertEqual(milestone_events, expected_milestones)

        self.assertTrue(events[-1].HasField("result"))
        result = events[-1].result
        self.assertEqual(result.final_status, execution_pb2.MILESTONE_COMPILE_ERROR)
        self.assertEqual(result.exit_code, 2)
        self.assertIn("syntax error near unexpected token `then'", result.compile_output)

    async def test_execute_uninstalled_toolchain(self) -> None:
        req = execution_pb2.ExecuteRequest(
            request_id="req-uninstalled",
            language_id="go",
            source_code="package main\n",
        )
        resp = await self.stub.ExecuteSync(req)

        self.assertEqual(resp.final_status, execution_pb2.MILESTONE_SANDBOX_ERROR)
        self.assertIn("Toolchain for 'go' is not installed", resp.stderr)

    def test_loopback_only_binding(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            create_server(host="0.0.0.0", port=0)
        self.assertIn("Security violation", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx2:
            create_server(host="192.168.1.1", port=0)
        self.assertIn("Security violation", str(ctx2.exception))

    async def test_admission_control_resource_exhausted(self) -> None:
        slow_runner = DaemonMockRunner()
        slow_runner.run_delay = 0.5
        pipeline = ExecutionPipeline(registry=self.registry, runner=slow_runner)
        server, port = create_server(host="127.0.0.1", port=0, max_workers=1, max_capacity=1, pipeline=pipeline)
        await server.start()
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        stub = execution_pb2_grpc.ExecutionWorkerServiceStub(channel)

        try:
            req1 = execution_pb2.ExecuteRequest(request_id="req-cap-1", language_id="python3", source_code="print(1)\n")
            req2 = execution_pb2.ExecuteRequest(request_id="req-cap-2", language_id="python3", source_code="print(2)\n")

            async def do_sync(req):
                return await stub.ExecuteSync(req)

            task1 = asyncio.create_task(do_sync(req1))
            await asyncio.sleep(0.05)

            with self.assertRaises(grpc.aio.AioRpcError) as ctx:
                await stub.ExecuteSync(req2)
            self.assertEqual(ctx.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
            self.assertIn("Worker daemon capacity exceeded", ctx.exception.details())

            await task1
        finally:
            await channel.close()
            await server.stop(grace=0.5)

    async def test_stream_cancellation_propagation(self) -> None:
        slow_runner = DaemonMockRunner()
        slow_runner.run_delay = 1.0
        pipeline = ExecutionPipeline(registry=self.registry, runner=slow_runner)
        server, port = create_server(host="127.0.0.1", port=0, max_workers=2, max_capacity=4, pipeline=pipeline)
        await server.start()
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        stub = execution_pb2_grpc.ExecutionWorkerServiceStub(channel)

        try:
            req = execution_pb2.ExecuteRequest(request_id="req-cancel-1", language_id="python3", source_code="import time; time.sleep(5)\n")
            call = stub.ExecuteStream(req)
            first_event = await call.read()
            self.assertTrue(first_event.HasField("milestone_event"))
            self.assertEqual(first_event.milestone_event.milestone, execution_pb2.MILESTONE_ACCEPTED)

            call.cancel()

            with self.assertRaises(asyncio.CancelledError):
                while True:
                    ev = await call.read()
                    if ev is grpc.aio.EOF:
                        raise asyncio.CancelledError()
        finally:
            await channel.close()
            await server.stop(grace=0.5)

    async def test_stream_cancellation_real_native_runner(self) -> None:
        """Integration test verifying live gRPC cancellation against real NativeSandboxRunner."""
        import subprocess

        if not os.path.exists("sandbox/pes2-runner"):
            self.skipTest("sandbox/pes2-runner not built")
        try:
            res = subprocess.run(["unshare", "-U", "true"], capture_output=True)
            if res.returncode != 0:
                self.skipTest("User namespaces restricted in current environment")
        except FileNotFoundError:
            self.skipTest("unshare binary not found")

        cgroup_root = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice"
        if not os.path.exists(cgroup_root):
            self.skipTest("Delegated cgroup root not found")

        runner = NativeSandboxRunner(runner_path="sandbox/pes2-runner", cgroup_root=cgroup_root)
        pipeline = ExecutionPipeline(registry=self.registry, runner=runner)
        server, port = create_server(host="127.0.0.1", port=0, max_workers=2, pipeline=pipeline)
        await server.start()
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        stub = execution_pb2_grpc.ExecutionWorkerServiceStub(channel)

        try:
            req = execution_pb2.ExecuteRequest(
                request_id="req-native-cancel-1",
                language_id="bash",
                source_code="#!/bin/bash\nsleep 5\n",
            )
            call = stub.ExecuteStream(req)
            while True:
                ev = await call.read()
                if ev is grpc.aio.EOF:
                    break
                if ev.HasField("milestone_event") and ev.milestone_event.milestone == execution_pb2.MILESTONE_RUNNING:
                    break

            call.cancel()

            with self.assertRaises(asyncio.CancelledError):
                while True:
                    ev = await call.read()
                    if ev is grpc.aio.EOF:
                        raise asyncio.CancelledError()

            await asyncio.sleep(0.3)
            pes2_pids = subprocess.run(["pgrep", "-f", "pes2-runner"], capture_output=True, text=True).stdout.strip().split()
            self.assertEqual(len(pes2_pids), 0)
        finally:
            await channel.close()
            await server.stop(grace=0.5)


if __name__ == "__main__":
    unittest.main()
