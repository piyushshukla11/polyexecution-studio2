"""Integration tests for the asynchronous gRPC Worker Daemon."""

import asyncio
import os
import threading
import unittest

import grpc

from engine.daemon.server import create_server
from engine.daemon.service import (
    derive_default_memory_budget_kb,
    detect_cgroup_memory_limit_kb,
)
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
            elif target_bin.endswith("javac"):
                out_path = os.path.join(workspace, "Solution.class")
                with open(out_path, "wb") as f:
                    f.write(b"\xca\xfe\xba\xbe")
            elif any("tsc" in str(arg) for arg in target_argv) or target_bin.endswith("tsc"):
                out_path = os.path.join(workspace, "Solution.js")
                with open(out_path, "w") as f:
                    f.write('"use strict";\n')
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
        self.assertIn("php", runtime_map)
        self.assertIn("typescript", runtime_map)

        # Bash, PHP, and TypeScript verified installed
        self.assertTrue(runtime_map["bash"].is_installed)
        self.assertEqual(runtime_map["bash"].display_name, "Bash")
        self.assertTrue(runtime_map["php"].is_installed)
        self.assertEqual(runtime_map["php"].display_name, "PHP")
        self.assertTrue(runtime_map["typescript"].is_installed)
        self.assertEqual(runtime_map["typescript"].display_name, "TypeScript")

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

    async def test_java_execution_stream(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 200, 150, 49152, "", "")
        )
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 50, 40, 24576, "hello from java daemon\n", "")
        )

        req = execution_pb2.ExecuteRequest(
            request_id="req-daemon-java",
            language_id="java",
            source_code="public class Solution { public static void main(String[] args) { System.out.println(\"hello from java daemon\"); } }\n",
        )
        call = self.stub.ExecuteStream(req)
        milestones = []
        result_response = None

        while True:
            resp = await call.read()
            if resp is grpc.aio.EOF:
                break
            if resp.HasField("milestone_event"):
                milestones.append(resp.milestone_event.milestone)
            if resp.HasField("result"):
                result_response = resp.result

        self.assertIn(execution_pb2.MILESTONE_ACCEPTED, milestones)
        self.assertIn(execution_pb2.MILESTONE_COMPILING, milestones)
        self.assertIn(execution_pb2.MILESTONE_RUNNING, milestones)
        self.assertIn(execution_pb2.MILESTONE_COMPLETED, milestones)
        self.assertIsNotNone(result_response)
        assert result_response is not None
        self.assertEqual(result_response.final_status, execution_pb2.MILESTONE_COMPLETED)
        self.assertEqual(result_response.stdout, "hello from java daemon\n")

    async def test_execute_stream_ruby_success(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 30, 20, 16384, "hello from ruby daemon\n", "")
        )

        req = execution_pb2.ExecuteRequest(
            request_id="req-daemon-ruby",
            language_id="ruby",
            source_code="puts 'hello from ruby daemon'\n",
        )
        call = self.stub.ExecuteStream(req)
        milestones = []
        result_response = None

        while True:
            resp = await call.read()
            if resp is grpc.aio.EOF:
                break
            if resp.HasField("milestone_event"):
                milestones.append(resp.milestone_event.milestone)
            if resp.HasField("result"):
                result_response = resp.result

        self.assertIn(execution_pb2.MILESTONE_ACCEPTED, milestones)
        self.assertIn(execution_pb2.MILESTONE_RUNNING, milestones)
        self.assertIn(execution_pb2.MILESTONE_COMPLETED, milestones)
        # Direct interpreted Ruby must NEVER emit COMPILING
        self.assertNotIn(execution_pb2.MILESTONE_COMPILING, milestones)
        self.assertIsNotNone(result_response)
        assert result_response is not None
        self.assertEqual(result_response.final_status, execution_pb2.MILESTONE_COMPLETED)
        self.assertEqual(result_response.stdout, "hello from ruby daemon\n")

    async def test_execute_stream_javascript_success(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 30, 20, 52428, "hello from node daemon\n", "")
        )

        req = execution_pb2.ExecuteRequest(
            request_id="req-daemon-js",
            language_id="javascript",
            source_code="console.log('hello from node daemon');\n",
        )
        call = self.stub.ExecuteStream(req)
        milestones = []
        result_response = None

        while True:
            resp = await call.read()
            if resp is grpc.aio.EOF:
                break
            if resp.HasField("milestone_event"):
                milestones.append(resp.milestone_event.milestone)
            if resp.HasField("result"):
                result_response = resp.result

        self.assertIn(execution_pb2.MILESTONE_ACCEPTED, milestones)
        self.assertIn(execution_pb2.MILESTONE_RUNNING, milestones)
        self.assertIn(execution_pb2.MILESTONE_COMPLETED, milestones)
        # Direct interpreted JavaScript must NEVER emit COMPILING
        self.assertNotIn(execution_pb2.MILESTONE_COMPILING, milestones)
        self.assertIsNotNone(result_response)
        assert result_response is not None
        self.assertEqual(result_response.final_status, execution_pb2.MILESTONE_COMPLETED)
        self.assertEqual(result_response.stdout, "hello from node daemon\n")

    async def test_execute_stream_php_success(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 30, 20, 25600, "hello from php daemon\n", "")
        )

        req = execution_pb2.ExecuteRequest(
            request_id="req-daemon-php",
            language_id="php",
            source_code="<?php echo 'hello from php daemon\n';\n",
        )
        call = self.stub.ExecuteStream(req)
        milestones = []
        result_response = None

        while True:
            resp = await call.read()
            if resp is grpc.aio.EOF:
                break
            if resp.HasField("milestone_event"):
                milestones.append(resp.milestone_event.milestone)
            if resp.HasField("result"):
                result_response = resp.result

        self.assertIn(execution_pb2.MILESTONE_ACCEPTED, milestones)
        self.assertIn(execution_pb2.MILESTONE_RUNNING, milestones)
        self.assertIn(execution_pb2.MILESTONE_COMPLETED, milestones)
        # Direct interpreted PHP must NEVER emit COMPILING
        self.assertNotIn(execution_pb2.MILESTONE_COMPILING, milestones)
        self.assertIsNotNone(result_response)
        assert result_response is not None
        self.assertEqual(result_response.final_status, execution_pb2.MILESTONE_COMPLETED)
        self.assertEqual(result_response.stdout, "hello from php daemon\n")

    async def test_execute_stream_typescript_success(self) -> None:
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 1500, 1400, 215000, "", "")
        )
        self.runner.queue_result(
            RunnerResult("OK", 0, 0, 30, 20, 25600, "hello from typescript daemon\n", "")
        )

        req = execution_pb2.ExecuteRequest(
            request_id="req-daemon-ts",
            language_id="typescript",
            source_code='const msg: string = "hello from typescript daemon"; console.log(msg);\n',
        )
        call = self.stub.ExecuteStream(req)
        milestones = []
        result_response = None

        while True:
            resp = await call.read()
            if resp is grpc.aio.EOF:
                break
            if resp.HasField("milestone_event"):
                milestones.append(resp.milestone_event.milestone)
            if resp.HasField("result"):
                result_response = resp.result

        # 2-stage TypeScript compilation emits COMPILING
        self.assertIn(execution_pb2.MILESTONE_ACCEPTED, milestones)
        self.assertIn(execution_pb2.MILESTONE_COMPILING, milestones)
        self.assertIn(execution_pb2.MILESTONE_RUNNING, milestones)
        self.assertIn(execution_pb2.MILESTONE_COMPLETED, milestones)
        self.assertIsNotNone(result_response)
        assert result_response is not None
        self.assertEqual(result_response.final_status, execution_pb2.MILESTONE_COMPLETED)
        self.assertEqual(result_response.stdout, "hello from typescript daemon\n")

    async def test_execute_stream_compile_error_typescript(self) -> None:
        self.runner.queue_result(
            RunnerResult("RUNTIME_ERROR", 2, 0, 1500, 1400, 215000, "", "Solution.ts(1,1): error TS1005: ';' expected.\n")
        )

        req = execution_pb2.ExecuteRequest(
            request_id="req-daemon-ts-err",
            language_id="typescript",
            source_code="const broken: string = ;\n",
        )
        call = self.stub.ExecuteStream(req)
        milestones = []
        result_response = None

        while True:
            resp = await call.read()
            if resp is grpc.aio.EOF:
                break
            if resp.HasField("milestone_event"):
                milestones.append(resp.milestone_event.milestone)
            if resp.HasField("result"):
                result_response = resp.result

        self.assertIn(execution_pb2.MILESTONE_ACCEPTED, milestones)
        self.assertIn(execution_pb2.MILESTONE_COMPILING, milestones)
        self.assertIn(execution_pb2.MILESTONE_COMPILE_ERROR, milestones)
        self.assertNotIn(execution_pb2.MILESTONE_RUNNING, milestones)
        self.assertIsNotNone(result_response)
        assert result_response is not None
        self.assertEqual(result_response.final_status, execution_pb2.MILESTONE_COMPILE_ERROR)
        self.assertEqual(result_response.exit_code, 2)
        self.assertIn("error TS1005", result_response.stderr)

    async def test_memory_admission_control(self) -> None:
        """Verifies that requests exceeding available daemon memory capacity are rejected with RESOURCE_EXHAUSTED."""
        server, port = create_server(
            host="127.0.0.1",
            port=0,
            max_workers=2,
            max_capacity=4,
            max_memory_kb=102400,  # 100 MiB limit
            pipeline=self.pipeline,
        )
        await server.start()
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        stub = execution_pb2_grpc.ExecutionWorkerServiceStub(channel)

        try:
            req = execution_pb2.ExecuteRequest(
                request_id="req-mem-exceeded",
                language_id="java",
                source_code="public class Solution {}",
            )
            with self.assertRaises(grpc.aio.AioRpcError) as ctx:
                call = stub.ExecuteStream(req)
                await call.read()
            self.assertEqual(ctx.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
            self.assertIn("memory capacity exceeded", ctx.exception.details())
        finally:
            await channel.close()
            await server.stop(grace=0.5)

    def test_derive_default_memory_budget_kb(self) -> None:
        """Verifies derivation of execution memory budget with operator override, safe host headroom,
        and strict headroom adherence without artificial floors on small hosts.
        """
        # Operator override is honored directly
        self.assertEqual(derive_default_memory_budget_kb(1024), 1048576)
        self.assertEqual(derive_default_memory_budget_kb(2048), 2097152)

        # Default host memory derivation on current machine
        derived = derive_default_memory_budget_kb()
        self.assertGreaterEqual(derived, 524288)
        self.assertLessEqual(derived, 4194304)

        # Large host simulation: 16 GiB (16777216 KB)
        # Headroom: max(1024 MiB, 25%) = 4 GiB -> available 12 GiB, capped at 4 GiB baseline
        self.assertEqual(
            derive_default_memory_budget_kb(total_physical_kb=16777216, cgroup_limit_kb=None),
            4194304,
        )

        # Medium host simulation: 2 GiB (2097152 KB)
        # Headroom: 25% = 512 MiB -> available 1536 MiB (1572864 KB)
        self.assertEqual(
            derive_default_memory_budget_kb(total_physical_kb=2097152, cgroup_limit_kb=None),
            1572864,
        )

        # Small host simulation: 512 MiB (524288 KB)
        # Headroom: 25% = 128 MiB -> available 384 MiB (393216 KB)
        # Must strictly preserve headroom and NOT force a 512 MiB floor that overcommits the host
        budget_512mb = derive_default_memory_budget_kb(total_physical_kb=524288, cgroup_limit_kb=None)
        self.assertEqual(budget_512mb, 393216)
        self.assertLess(budget_512mb, 524288)

        # Very small host simulation: 256 MiB (262144 KB)
        # Headroom: 25% (min 64 MiB) = 64 MiB -> available 192 MiB (196608 KB)
        budget_256mb = derive_default_memory_budget_kb(total_physical_kb=262144, cgroup_limit_kb=None)
        self.assertEqual(budget_256mb, 196608)
        self.assertLess(budget_256mb, 262144)

        # Insufficient host memory: 64 MiB (65536 KB)
        # Headroom requirement (64 MiB) exhausts available capacity; must raise ValueError
        with self.assertRaises(ValueError) as ctx:
            derive_default_memory_budget_kb(total_physical_kb=65536, cgroup_limit_kb=None)
        self.assertIn("Insufficient host or cgroup memory", str(ctx.exception))
        self.assertIn("--memory-mb", str(ctx.exception))

        # Explicit operator override works even on a constrained host
        self.assertEqual(
            derive_default_memory_budget_kb(operator_budget_mb=32, total_physical_kb=65536, cgroup_limit_kb=None),
            32768,
        )

    def test_derive_default_memory_budget_with_cgroup_limits(self) -> None:
        """Verifies that cgroup memory constraints are detected and bound execution memory budget."""
        import tempfile

        # Case 1: Host has 16 GiB physical RAM, but cgroup limit is 1 GiB (1048576 KB)
        # Effective memory = 1 GiB. Headroom = 25% (256 MiB) -> available 768 MiB (786432 KB)
        budget_cgroup = derive_default_memory_budget_kb(
            total_physical_kb=16777216,
            cgroup_limit_kb=1048576,
        )
        self.assertEqual(budget_cgroup, 786432)

        # Case 2: Host has 16 GiB physical RAM, but cgroup limit is 512 MiB (524288 KB)
        # Effective memory = 512 MiB. Headroom = 25% (128 MiB) -> available 384 MiB (393216 KB)
        budget_cgroup_small = derive_default_memory_budget_kb(
            total_physical_kb=16777216,
            cgroup_limit_kb=524288,
        )
        self.assertEqual(budget_cgroup_small, 393216)

        # Case 3: Extremely tight cgroup limit (64 MiB): requires explicit operator budget
        with self.assertRaises(ValueError) as ctx:
            derive_default_memory_budget_kb(
                total_physical_kb=16777216,
                cgroup_limit_kb=65536,
            )
        self.assertIn("Insufficient host or cgroup memory", str(ctx.exception))

        # Case 4: Hierarchy detection with cgroup v2 memory.max
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a mock cgroup hierarchy
            cgroup_sub = os.path.join(tmp_dir, "docker", "pes2")
            os.makedirs(cgroup_sub, exist_ok=True)
            mem_max_file = os.path.join(cgroup_sub, "memory.max")
            with open(mem_max_file, "w") as f:
                f.write("1073741824\n")  # 1 GiB

            proc_cgroup = os.path.join(tmp_dir, "proc_cgroup")
            with open(proc_cgroup, "w") as f:
                f.write("0::/docker/pes2\n")

            limit_detected = detect_cgroup_memory_limit_kb(
                proc_cgroup_path=proc_cgroup,
                cgroup_root=tmp_dir,
            )
            self.assertEqual(limit_detected, 1048576)

        # Case 5: Hierarchy detection with 'max' (unconstrained) returns None
        with tempfile.TemporaryDirectory() as tmp_dir:
            cgroup_sub = os.path.join(tmp_dir, "system.slice")
            os.makedirs(cgroup_sub, exist_ok=True)
            mem_max_file = os.path.join(cgroup_sub, "memory.max")
            with open(mem_max_file, "w") as f:
                f.write("max\n")

            proc_cgroup = os.path.join(tmp_dir, "proc_cgroup")
            with open(proc_cgroup, "w") as f:
                f.write("0::/system.slice\n")

            limit_detected = detect_cgroup_memory_limit_kb(
                proc_cgroup_path=proc_cgroup,
                cgroup_root=tmp_dir,
            )
            self.assertIsNone(limit_detected)

        # Case 6: Hierarchy detection with cgroup v1 memory.limit_in_bytes
        with tempfile.TemporaryDirectory() as tmp_dir:
            mem_v1_dir = os.path.join(tmp_dir, "memory")
            os.makedirs(mem_v1_dir, exist_ok=True)
            mem_limit_file = os.path.join(mem_v1_dir, "memory.limit_in_bytes")
            with open(mem_limit_file, "w") as f:
                f.write("536870912\n")  # 512 MiB

            limit_detected = detect_cgroup_memory_limit_kb(
                proc_cgroup_path=os.path.join(tmp_dir, "nonexistent"),
                cgroup_root=tmp_dir,
            )
            self.assertEqual(limit_detected, 524288)

    async def test_competing_request_rejected_during_cancellation_cleanup(self) -> None:
        """Verifies that an execution future holds its memory reservation during cancellation cleanup,
        rejecting competing requests that would exceed the configured budget until cleanup finishes.
        """
        import time

        class CancellationCleanupRunner(DaemonMockRunner):
            def __init__(self, cleanup_delay: float = 0.3) -> None:
                super().__init__()
                self.cleanup_delay = cleanup_delay
                self.in_cleanup = threading.Event()
                self.cleanup_done = threading.Event()

            def run(self, workspace, target_bin, target_argv, limits, stdin_content="", **kwargs):
                cancel_token = kwargs.get("cancel_token")
                while not (cancel_token and cancel_token.is_set()):
                    time.sleep(0.02)
                self.in_cleanup.set()
                time.sleep(self.cleanup_delay)
                self.cleanup_done.set()
                return RunnerResult(
                    status="CANCELLED",
                    exit_code=130,
                    signal=15,
                    wall_ms=int(self.cleanup_delay * 1000),
                    cpu_ms=0,
                    peak_memory_kb=0,
                    stdout="",
                    stderr="Cancelled",
                )

        cleanup_runner = CancellationCleanupRunner(cleanup_delay=0.3)
        pipeline = ExecutionPipeline(registry=self.registry, runner=cleanup_runner)
        server, port = create_server(
            host="127.0.0.1",
            port=0,
            max_workers=4,
            max_capacity=4,
            max_memory_kb=102400,  # 100 MiB limit
            pipeline=pipeline,
        )
        await server.start()
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        stub = execution_pb2_grpc.ExecutionWorkerServiceStub(channel)

        try:
            req1 = execution_pb2.ExecuteRequest(
                request_id="req-clean-1",
                language_id="python3",
                source_code="import time; time.sleep(10)",
                custom_limits=execution_pb2.ResourceLimits(memory_kb=71680),
            )
            call1 = stub.ExecuteStream(req1)
            while True:
                ev = await call1.read()
                if ev is grpc.aio.EOF:
                    break
                if ev.HasField("milestone_event") and ev.milestone_event.milestone == execution_pb2.MILESTONE_ACCEPTED:
                    break

            call1.cancel()

            while not cleanup_runner.in_cleanup.is_set():
                await asyncio.sleep(0.01)

            req2 = execution_pb2.ExecuteRequest(
                request_id="req-clean-2",
                language_id="python3",
                source_code="print('competing')",
                custom_limits=execution_pb2.ResourceLimits(memory_kb=71680),
            )
            with self.assertRaises(grpc.aio.AioRpcError) as ctx:
                call2 = stub.ExecuteStream(req2)
                await call2.read()
            self.assertEqual(ctx.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
            self.assertIn("memory capacity exceeded", ctx.exception.details())

            while not cleanup_runner.cleanup_done.is_set():
                await asyncio.sleep(0.01)

            await asyncio.sleep(0.05)

            class InstantRunner(DaemonMockRunner):
                def run(self, *args, **kwargs):
                    return RunnerResult("OK", 0, 0, 10, 5, 2048, "success\n", "")

            pipeline.runner = InstantRunner()

            call3 = stub.ExecuteStream(req2)
            milestones = []
            final_res = None
            while True:
                ev = await call3.read()
                if ev is grpc.aio.EOF:
                    break
                if ev.HasField("milestone_event"):
                    milestones.append(ev.milestone_event.milestone)
                if ev.HasField("result"):
                    final_res = ev.result

            self.assertIn(execution_pb2.MILESTONE_ACCEPTED, milestones)
            self.assertIsNotNone(final_res)
            assert final_res is not None
            self.assertEqual(final_res.final_status, execution_pb2.MILESTONE_COMPLETED)
        finally:
            await channel.close()
            await server.stop(grace=0.5)

    async def test_concurrent_requests_cannot_exceed_memory_budget_including_cancellation(self) -> None:
        """Verifies concurrent requests are admitted strictly up to the memory budget,
        and cannot exceed the budget even when in-flight requests are cancelled.
        """
        import time

        class DelayedRunner(DaemonMockRunner):
            def __init__(self) -> None:
                super().__init__()
                self.running_count = 0
                self.peak_running_count = 0
                self.lock = threading.Lock()

            def run(self, workspace, target_bin, target_argv, limits, stdin_content="", **kwargs):
                cancel_token = kwargs.get("cancel_token")
                with self.lock:
                    self.running_count += 1
                    if self.running_count > self.peak_running_count:
                        self.peak_running_count = self.running_count

                try:
                    for _ in range(30):
                        if cancel_token and cancel_token.is_set():
                            time.sleep(0.1)
                            return RunnerResult("CANCELLED", 130, 15, 50, 0, 0, "", "Cancelled")
                        time.sleep(0.02)
                    return RunnerResult("OK", 0, 0, 50, 10, 2048, "done\n", "")
                finally:
                    with self.lock:
                        self.running_count -= 1

        runner = DelayedRunner()
        pipeline = ExecutionPipeline(registry=self.registry, runner=runner)
        server, port = create_server(
            host="127.0.0.1",
            port=0,
            max_workers=4,
            max_capacity=4,
            max_memory_kb=153600,  # 150 MiB limit
            pipeline=pipeline,
        )
        await server.start()
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        stub = execution_pb2_grpc.ExecutionWorkerServiceStub(channel)

        try:
            req_60mb = execution_pb2.ExecuteRequest(
                request_id="req-concurrent",
                language_id="python3",
                source_code="import time; time.sleep(1)",
                custom_limits=execution_pb2.ResourceLimits(memory_kb=61440),  # 60 MiB
            )

            call1 = stub.ExecuteStream(req_60mb)
            ev1 = await call1.read()
            self.assertTrue(ev1.HasField("milestone_event"))

            call2 = stub.ExecuteStream(req_60mb)
            ev2 = await call2.read()
            self.assertTrue(ev2.HasField("milestone_event"))

            # Attempt request 3 (needs 60 MiB, but only 30 MiB left)
            with self.assertRaises(grpc.aio.AioRpcError) as ctx:
                call3 = stub.ExecuteStream(req_60mb)
                await call3.read()
            self.assertEqual(ctx.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
            self.assertIn("memory capacity exceeded", ctx.exception.details())

            call1.cancel()

            # Immediately during cancellation cleanup of request 1, attempt request 4
            with self.assertRaises(grpc.aio.AioRpcError) as ctx:
                call4 = stub.ExecuteStream(req_60mb)
                await call4.read()
            self.assertEqual(ctx.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)

            try:
                while True:
                    ev = await call1.read()
                    if ev is grpc.aio.EOF:
                        break
            except (grpc.aio.AioRpcError, asyncio.CancelledError):
                pass

            try:
                while True:
                    ev = await call2.read()
                    if ev is grpc.aio.EOF:
                        break
            except (grpc.aio.AioRpcError, asyncio.CancelledError):
                pass

            await asyncio.sleep(0.1)

            call5 = stub.ExecuteStream(req_60mb)
            milestones = []
            while True:
                ev = await call5.read()
                if ev is grpc.aio.EOF:
                    break
                if ev.HasField("milestone_event"):
                    milestones.append(ev.milestone_event.milestone)
            self.assertIn(execution_pb2.MILESTONE_ACCEPTED, milestones)
        finally:
            await channel.close()
            await server.stop(grace=0.5)

    async def test_grpc_cancellation_competing_request_real_native_runner(self) -> None:
        """Integration test verifying cancellation cleanup holds memory reservation with real NativeSandboxRunner,
        preventing competing requests from exceeding the configured budget during process termination and cgroup teardown.
        """
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
        server, port = create_server(
            host="127.0.0.1",
            port=0,
            max_workers=2,
            max_capacity=4,
            max_memory_kb=102400,
            pipeline=pipeline,
        )
        await server.start()
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        stub = execution_pb2_grpc.ExecutionWorkerServiceStub(channel)

        try:
            req1 = execution_pb2.ExecuteRequest(
                request_id="req-native-cancel-compete-1",
                language_id="bash",
                source_code="#!/bin/bash\nsleep 5\n",
                custom_limits=execution_pb2.ResourceLimits(memory_kb=71680),  # 70 MiB
            )
            call1 = stub.ExecuteStream(req1)
            while True:
                ev = await call1.read()
                if ev is grpc.aio.EOF:
                    break
                if ev.HasField("milestone_event") and ev.milestone_event.milestone == execution_pb2.MILESTONE_RUNNING:
                    break

            # Cancel in-flight request 1
            call1.cancel()

            # Immediately send competing request 2 needing 70 MiB (budget is 100 MiB)
            req2 = execution_pb2.ExecuteRequest(
                request_id="req-native-cancel-compete-2",
                language_id="bash",
                source_code="#!/bin/bash\necho hello\n",
                custom_limits=execution_pb2.ResourceLimits(memory_kb=71680),
            )
            with self.assertRaises(grpc.aio.AioRpcError) as ctx:
                call2 = stub.ExecuteStream(req2)
                await call2.read()
            self.assertEqual(ctx.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
            self.assertIn("memory capacity exceeded", ctx.exception.details())

            try:
                while True:
                    ev = await call1.read()
                    if ev is grpc.aio.EOF:
                        break
            except (grpc.aio.AioRpcError, asyncio.CancelledError):
                pass

            await asyncio.sleep(0.3)

            leftovers = [
                d for d in os.listdir(cgroup_root)
                if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
            ]
            self.assertEqual(len(leftovers), 0)

            req3 = execution_pb2.ExecuteRequest(
                request_id="req-native-cancel-compete-3",
                language_id="bash",
                source_code="#!/bin/bash\necho completed\n",
                custom_limits=execution_pb2.ResourceLimits(memory_kb=71680),
            )
            call3 = stub.ExecuteStream(req3)
            final_res = None
            while True:
                ev = await call3.read()
                if ev is grpc.aio.EOF:
                    break
                if ev.HasField("result"):
                    final_res = ev.result

            self.assertIsNotNone(final_res)
            assert final_res is not None
            self.assertEqual(final_res.final_status, execution_pb2.MILESTONE_COMPLETED)
            self.assertEqual(final_res.stdout.strip(), "completed")
        finally:
            await channel.close()
            await server.stop(grace=0.5)

    async def test_grpc_java_execution_with_real_native_runner(self) -> None:
        """Integration test verifying real gRPC Java ExecuteStream through NativeSandboxRunner."""
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
            java_src = (
                "public class Solution {\n"
                "    public static void main(String[] args) {\n"
                "        System.out.println(\"gRPC live Java execution OK\");\n"
                "    }\n"
                "}\n"
            )
            req = execution_pb2.ExecuteRequest(
                request_id="req-live-grpc-java",
                language_id="java",
                source_code=java_src,
            )
            call = stub.ExecuteStream(req)
            milestones = []
            final_res = None
            while True:
                ev = await call.read()
                if ev is grpc.aio.EOF:
                    break
                if ev.HasField("milestone_event"):
                    milestones.append(ev.milestone_event.milestone)
                if ev.HasField("result"):
                    final_res = ev.result

            self.assertIn(execution_pb2.MILESTONE_ACCEPTED, milestones)
            self.assertIn(execution_pb2.MILESTONE_COMPILING, milestones)
            self.assertIn(execution_pb2.MILESTONE_RUNNING, milestones)
            self.assertIn(execution_pb2.MILESTONE_COMPLETED, milestones)
            self.assertIsNotNone(final_res)
            assert final_res is not None
            self.assertEqual(final_res.final_status, execution_pb2.MILESTONE_COMPLETED)
            self.assertEqual(final_res.exit_code, 0)
            self.assertEqual(final_res.stdout.strip(), "gRPC live Java execution OK")

            # Verify cgroup cleanup
            leftovers = [
                d for d in os.listdir(cgroup_root)
                if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
            ]
            self.assertEqual(len(leftovers), 0)
        finally:
            await channel.close()
            await server.stop(grace=0.5)

    async def test_grpc_ruby_execution_with_real_native_runner(self) -> None:
        """Integration test verifying real gRPC Ruby ExecuteStream through NativeSandboxRunner."""
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
            ruby_src = "puts 'gRPC live Ruby execution OK'\n"
            req = execution_pb2.ExecuteRequest(
                request_id="req-live-grpc-ruby",
                language_id="ruby",
                source_code=ruby_src,
            )
            call = stub.ExecuteStream(req)
            milestones = []
            final_res = None
            while True:
                ev = await call.read()
                if ev is grpc.aio.EOF:
                    break
                if ev.HasField("milestone_event"):
                    milestones.append(ev.milestone_event.milestone)
                if ev.HasField("result"):
                    final_res = ev.result

            self.assertIn(execution_pb2.MILESTONE_ACCEPTED, milestones)
            self.assertIn(execution_pb2.MILESTONE_RUNNING, milestones)
            self.assertIn(execution_pb2.MILESTONE_COMPLETED, milestones)
            self.assertNotIn(execution_pb2.MILESTONE_COMPILING, milestones)
            self.assertIsNotNone(final_res)
            assert final_res is not None
            self.assertEqual(final_res.final_status, execution_pb2.MILESTONE_COMPLETED)
            self.assertEqual(final_res.exit_code, 0)
            self.assertEqual(final_res.stdout.strip(), "gRPC live Ruby execution OK")

            # Verify cgroup cleanup
            leftovers = [
                d for d in os.listdir(cgroup_root)
                if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
            ]
            self.assertEqual(len(leftovers), 0)
        finally:
            await channel.close()
            await server.stop(grace=0.5)

    async def test_grpc_javascript_execution_with_real_native_runner(self) -> None:
        """Integration test verifying real gRPC Node.js ExecuteStream through NativeSandboxRunner."""
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
            js_src = "console.log('gRPC live Node.js execution OK', 21 * 2);\n"
            req = execution_pb2.ExecuteRequest(
                request_id="req-live-grpc-js",
                language_id="javascript",
                source_code=js_src,
            )
            call = stub.ExecuteStream(req)
            milestones = []
            final_res = None
            while True:
                ev = await call.read()
                if ev is grpc.aio.EOF:
                    break
                if ev.HasField("milestone_event"):
                    milestones.append(ev.milestone_event.milestone)
                if ev.HasField("result"):
                    final_res = ev.result

            self.assertIn(execution_pb2.MILESTONE_ACCEPTED, milestones)
            self.assertIn(execution_pb2.MILESTONE_RUNNING, milestones)
            self.assertIn(execution_pb2.MILESTONE_COMPLETED, milestones)
            self.assertNotIn(execution_pb2.MILESTONE_COMPILING, milestones)
            self.assertIsNotNone(final_res)
            assert final_res is not None
            self.assertEqual(final_res.final_status, execution_pb2.MILESTONE_COMPLETED)
            self.assertEqual(final_res.exit_code, 0)
            self.assertEqual(final_res.stdout.strip(), "gRPC live Node.js execution OK 42")

            # Verify cgroup cleanup
            leftovers = [
                d for d in os.listdir(cgroup_root)
                if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
            ]
            self.assertEqual(len(leftovers), 0)
        finally:
            await channel.close()
            await server.stop(grace=0.5)

    async def test_grpc_php_execution_with_real_native_runner(self) -> None:
        """Integration test verifying real gRPC PHP ExecuteStream through NativeSandboxRunner."""
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
            php_src = "<?php\necho 'gRPC live PHP execution OK ' . (21 * 2) . \"\\n\";\n"
            req = execution_pb2.ExecuteRequest(
                request_id="req-live-grpc-php",
                language_id="php",
                source_code=php_src,
            )
            call = stub.ExecuteStream(req)
            milestones = []
            final_res = None
            while True:
                ev = await call.read()
                if ev is grpc.aio.EOF:
                    break
                if ev.HasField("milestone_event"):
                    milestones.append(ev.milestone_event.milestone)
                if ev.HasField("result"):
                    final_res = ev.result

            self.assertIn(execution_pb2.MILESTONE_ACCEPTED, milestones)
            self.assertIn(execution_pb2.MILESTONE_RUNNING, milestones)
            self.assertIn(execution_pb2.MILESTONE_COMPLETED, milestones)
            self.assertNotIn(execution_pb2.MILESTONE_COMPILING, milestones)
            self.assertIsNotNone(final_res)
            assert final_res is not None
            self.assertEqual(final_res.final_status, execution_pb2.MILESTONE_COMPLETED)
            self.assertEqual(final_res.exit_code, 0)
            self.assertEqual(final_res.stdout.strip(), "gRPC live PHP execution OK 42")

            # Verify cgroup cleanup
            leftovers = [
                d for d in os.listdir(cgroup_root)
                if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
            ]
            self.assertEqual(len(leftovers), 0)
        finally:
            await channel.close()
            await server.stop(grace=0.5)

    async def test_grpc_typescript_execution_with_real_native_runner(self) -> None:
        """Integration test verifying real gRPC TypeScript ExecuteStream through NativeSandboxRunner."""
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
            ts_src = 'const greeting: string = "gRPC live TypeScript execution OK"; console.log(greeting, 21 * 2);\n'
            req = execution_pb2.ExecuteRequest(
                request_id="req-live-grpc-ts",
                language_id="typescript",
                source_code=ts_src,
            )
            call = stub.ExecuteStream(req)
            milestones = []
            final_res = None
            while True:
                ev = await call.read()
                if ev is grpc.aio.EOF:
                    break
                if ev.HasField("milestone_event"):
                    milestones.append(ev.milestone_event.milestone)
                if ev.HasField("result"):
                    final_res = ev.result

            self.assertIn(execution_pb2.MILESTONE_ACCEPTED, milestones)
            self.assertIn(execution_pb2.MILESTONE_COMPILING, milestones)
            self.assertIn(execution_pb2.MILESTONE_RUNNING, milestones)
            self.assertIn(execution_pb2.MILESTONE_COMPLETED, milestones)
            self.assertIsNotNone(final_res)
            assert final_res is not None
            self.assertEqual(final_res.final_status, execution_pb2.MILESTONE_COMPLETED)
            self.assertEqual(final_res.exit_code, 0)
            self.assertEqual(final_res.stdout.strip(), "gRPC live TypeScript execution OK 42")

            # Verify cgroup cleanup
            leftovers = [
                d for d in os.listdir(cgroup_root)
                if d.startswith("pes2-") and os.path.isdir(os.path.join(cgroup_root, d))
            ]
            self.assertEqual(len(leftovers), 0)
        finally:
            await channel.close()
            await server.stop(grace=0.5)


if __name__ == "__main__":
    unittest.main()
