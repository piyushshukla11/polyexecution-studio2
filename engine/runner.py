"""Sandbox runner abstraction and native pes2-runner CLI interface."""

from abc import ABC, abstractmethod
import os
import re
import subprocess
import threading
import time
from typing import List, Optional

from engine.models import ResourceLimits, RunnerResult, SeccompPolicyProfile


PES2_RESULT_PATTERN = re.compile(
    r"PES2_RESULT\s+status=(\w+)\s+exit=(-?\d+)\s+signal=(-?\d+)\s+wall_ms=(\d+)\s+cpu_ms=(\d+)\s+peak_memory_kb=(\d+)\s+stdout_bytes=(\d+)\s+stderr_bytes=(\d+)"
)


class RunnerInterface(ABC):
    """Abstract interface for running bounded sandbox executions."""

    @abstractmethod
    def run(
        self,
        workspace: str,
        target_bin: str,
        target_argv: List[str],
        limits: ResourceLimits,
        stdin_content: str = "",
        seccomp_profile: SeccompPolicyProfile = SeccompPolicyProfile.STRICT_NATIVE,
        cancel_token: Optional[threading.Event] = None,
    ) -> RunnerResult:
        pass


class NativeSandboxRunner(RunnerInterface):
    """Executes target inside pes2-runner binary with Linux isolation."""

    def __init__(self, runner_path: str = "sandbox/pes2-runner", cgroup_root: Optional[str] = None) -> None:
        self.runner_path = os.path.abspath(runner_path)
        self.cgroup_root = cgroup_root or os.environ.get("PES2_CGROUP_ROOT")

    def run(
        self,
        workspace: str,
        target_bin: str,
        target_argv: List[str],
        limits: ResourceLimits,
        stdin_content: str = "",
        seccomp_profile: SeccompPolicyProfile = SeccompPolicyProfile.STRICT_NATIVE,
        cancel_token: Optional[threading.Event] = None,
    ) -> RunnerResult:
        if not os.path.exists(self.runner_path):
            return RunnerResult(
                status="SANDBOX_ERROR",
                exit_code=127,
                signal=0,
                wall_ms=0,
                cpu_ms=0,
                peak_memory_kb=0,
                stdout="",
                stderr=f"pes2-runner binary not found at {self.runner_path}",
            )

        cmd = [
            self.runner_path,
            "--workspace",
            os.path.abspath(workspace),
            "--wall-ms",
            str(limits.wall_ms),
            "--cpu-ms",
            str(limits.cpu_ms),
            "--memory-kb",
            str(limits.memory_kb),
            "--pids",
            str(limits.pids),
            "--seccomp-profile",
            seccomp_profile.value,
        ]
        if self.cgroup_root:
            cmd.extend(["--cgroup-root", self.cgroup_root])

        cmd.append("--")
        cmd.append(target_bin)
        cmd.extend(target_argv)

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if stdin_content else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as exc:
            return RunnerResult(
                status="SANDBOX_ERROR",
                exit_code=125,
                signal=0,
                wall_ms=0,
                cpu_ms=0,
                peak_memory_kb=0,
                stdout="",
                stderr=f"Supervisor invocation failure: {exc}",
            )

        stdout_data = ""
        stderr_data = ""

        def reader():
            nonlocal stdout_data, stderr_data
            stdout_data, stderr_data = proc.communicate(input=stdin_content if stdin_content else None)

        read_thread = threading.Thread(target=reader, daemon=True)
        read_thread.start()

        poll_interval = 0.05
        deadline = time.time() + ((limits.wall_ms + 5000) / 1000.0)
        was_cancelled = False
        was_timeout = False

        while read_thread.is_alive():
            if cancel_token and cancel_token.is_set():
                was_cancelled = True
                try:
                    proc.terminate()  # Sends SIGTERM to pes2-runner
                except ProcessLookupError:
                    pass
                break
            if time.time() > deadline:
                was_timeout = True
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                break
            time.sleep(poll_interval)

        read_thread.join(timeout=2.0)
        if read_thread.is_alive():
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            read_thread.join(timeout=1.0)

        # Parse PES2_RESULT metadata from stderr
        match = PES2_RESULT_PATTERN.search(stderr_data)
        if match:
            status = match.group(1)
            exit_code = int(match.group(2))
            signal = int(match.group(3))
            wall_ms = int(match.group(4))
            cpu_ms = int(match.group(5))
            peak_memory_kb = int(match.group(6))
            cleaned_stderr = PES2_RESULT_PATTERN.sub("", stderr_data).strip()
            return RunnerResult(
                status=status,
                exit_code=exit_code,
                signal=signal,
                wall_ms=wall_ms,
                cpu_ms=cpu_ms,
                peak_memory_kb=peak_memory_kb,
                stdout=stdout_data,
                stderr=cleaned_stderr,
            )

        if was_cancelled:
            return RunnerResult(
                status="CANCELLED",
                exit_code=130,
                signal=15,
                wall_ms=0,
                cpu_ms=0,
                peak_memory_kb=0,
                stdout=stdout_data,
                stderr=stderr_data or "Process cancelled by client signal",
            )

        if was_timeout:
            return RunnerResult(
                status="TIMEOUT",
                exit_code=124,
                signal=9,
                wall_ms=limits.wall_ms,
                cpu_ms=limits.cpu_ms,
                peak_memory_kb=0,
                stdout=stdout_data,
                stderr=stderr_data or "Host execution timeout waiting for pes2-runner",
            )

        return RunnerResult(
            status="SANDBOX_ERROR" if proc.returncode != 0 else "OK",
            exit_code=proc.returncode if proc.returncode is not None else 1,
            signal=0,
            wall_ms=0,
            cpu_ms=0,
            peak_memory_kb=0,
            stdout=stdout_data,
            stderr=stderr_data,
        )


class MockSandboxRunner(RunnerInterface):
    """Configurable mock runner for unit testing without kernel requirements."""

    def __init__(self) -> None:
        self.invocations: List[dict] = []
        self._next_result: Optional[RunnerResult] = None
        self._results_queue: List[RunnerResult] = []
        self.run_delay: float = 0.0

    def set_next_result(self, result: RunnerResult) -> None:
        self._next_result = result

    def queue_result(self, result: RunnerResult) -> None:
        self._results_queue.append(result)

    def run(
        self,
        workspace: str,
        target_bin: str,
        target_argv: List[str],
        limits: ResourceLimits,
        stdin_content: str = "",
        seccomp_profile: SeccompPolicyProfile = SeccompPolicyProfile.STRICT_NATIVE,
        cancel_token: Optional[threading.Event] = None,
    ) -> RunnerResult:
        self.invocations.append({
            "workspace": workspace,
            "target_bin": target_bin,
            "target_argv": list(target_argv),
            "limits": limits,
            "stdin_content": stdin_content,
            "seccomp_profile": seccomp_profile,
        })

        if cancel_token and cancel_token.is_set():
            return RunnerResult(
                status="CANCELLED",
                exit_code=130,
                signal=15,
                wall_ms=0,
                cpu_ms=0,
                peak_memory_kb=0,
                stdout="",
                stderr="Execution cancelled",
            )

        if self.run_delay > 0:
            step = 0.05
            elapsed = 0.0
            while elapsed < self.run_delay:
                if cancel_token and cancel_token.is_set():
                    return RunnerResult(
                        status="CANCELLED",
                        exit_code=130,
                        signal=15,
                        wall_ms=int(elapsed * 1000),
                        cpu_ms=0,
                        peak_memory_kb=0,
                        stdout="",
                        stderr="Execution cancelled while running",
                    )
                time.sleep(step)
                elapsed += step

        if self._results_queue:
            return self._results_queue.pop(0)
        if self._next_result is not None:
            return self._next_result
        return RunnerResult(
            status="OK",
            exit_code=0,
            signal=0,
            wall_ms=10,
            cpu_ms=5,
            peak_memory_kb=4096,
            stdout="",
            stderr="",
        )
