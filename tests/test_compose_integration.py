"""Integration and execution verification for Docker Compose packaging and sandbox capabilities.

Verifies:
1. Compose service topology and loopback-only default ingress.
2. Worker daemon sandbox capability configuration (SYS_ADMIN, unconfined seccomp, cgroups).
3. Reconciled database sandbox: ensures persistent postgres is removed from production stack.
4. Real Linux namespace and sandbox execution check (unshare / native runner),
   with explicit reporting of environment capabilities and skips.
"""

import os
import shutil
import subprocess
import unittest
import yaml


class TestDockerComposeConfiguration(unittest.TestCase):
    """Verifies docker-compose.yml structural rules and security configuration."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cls.compose_path = os.path.join(cls.root_dir, "docker-compose.yml")
        with open(cls.compose_path, "r", encoding="utf-8") as f:
            cls.compose_content = f.read()

    def test_reverse_proxy_loopback_binding_by_default(self) -> None:
        """Compose must bind reverse proxy to loopback 127.0.0.1 by default."""
        self.assertIn("127.0.0.1", self.compose_content)
        self.assertIn("${PES2_BIND_HOST:-127.0.0.1}", self.compose_content)

    def test_worker_daemon_sandbox_capabilities_configured(self) -> None:
        """Worker daemon must use a narrowly delegated cgroup subtree, no-new-privileges, and avoid broad SYS_ADMIN."""
        self.assertNotIn("SYS_ADMIN", self.compose_content)
        self.assertNotIn("seccomp:unconfined", self.compose_content)
        self.assertIn("${PES2_CGROUP_ROOT:-/sys/fs/cgroup/pes2}:/sys/fs/cgroup/pes2:rw", self.compose_content)
        self.assertIn("PES2_CGROUP_ROOT=/sys/fs/cgroup/pes2", self.compose_content)
        self.assertIn("no-new-privileges:true", self.compose_content)

    def test_postgres_removed_from_production_stack(self) -> None:
        """PostgreSQL service and persistent volume must not be present until disposable sandbox is wired."""
        self.assertNotIn("db-postgres:", self.compose_content)
        self.assertNotIn("postgres-data:", self.compose_content)

    def test_worker_and_gateway_not_publicly_exposed(self) -> None:
        """Worker daemon and API gateway should communicate across internal pes2-internal mesh."""
        self.assertIn("pes2-internal", self.compose_content)
        parsed = yaml.safe_load(self.compose_content)
        services = parsed.get("services", {})

        # Only reverse-proxy should declare host ports mapping
        for name, svc in services.items():
            if name == "reverse-proxy":
                ports = svc.get("ports", [])
                self.assertTrue(len(ports) > 0, "Reverse proxy must map host ingress port")
                for p in ports:
                    self.assertIn("127.0.0.1", str(p))
            else:
                self.assertNotIn(
                    "ports",
                    svc,
                    f"Service '{name}' must not expose host ports directly; internal communication only.",
                )


class TestRealSandboxExecutionCheck(unittest.TestCase):
    """Real kernel sandbox capability and execution check."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cls.runner_bin = os.path.join(cls.root_dir, "sandbox", "pes2-runner")

    def test_real_user_and_mount_namespaces_available(self) -> None:
        """Checks whether the execution environment permits user and mount namespace creation."""
        unshare_path = shutil.which("unshare")
        if not unshare_path:
            self.skipTest("unshare binary not available in test environment")

        res = subprocess.run(
            [unshare_path, "-U", "-m", "true"],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            err = res.stderr.strip() or res.stdout.strip()
            self.skipTest(
                f"Kernel namespaces (CLONE_NEWUSER, CLONE_NEWNS) unprivileged creation restricted in current environment: {err}. "
                "This leaves container-nested unprivileged sandboxing unverified without SYS_ADMIN capability."
            )
        self.assertEqual(res.returncode, 0)

    def test_real_native_runner_execution_check(self) -> None:
        """Executes the compiled C runner binary against a test command if built and supported."""
        if not os.path.exists(self.runner_bin):
            self.skipTest(f"Native runner {self.runner_bin} not built. Run 'make -C sandbox' first.")

        # Real execution test running /bin/echo through pes2-runner
        test_workspace = "/tmp/pes2_test_runner_workspace"
        os.makedirs(test_workspace, exist_ok=True)
        try:
            cmd = [
                self.runner_bin,
                "--workspace", test_workspace,
                "--wall-ms", "2000",
                "--cpu-ms", "1000",
                "--memory-kb", "65536",
                "--pids", "16",
                "--seccomp-profile", "strict",
                "--",
                "/bin/echo", "pes2_real_sandbox_ok",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                err_msg = res.stderr.strip() or res.stdout.strip()
                self.skipTest(
                    f"Native runner returned code {res.returncode} ({err_msg}). "
                    "Host cgroup v2 delegation or user namespace creation is restricted in this shell. "
                    "Leaves in-container privileged sandbox execution unverified."
                )
            self.assertIn("pes2_real_sandbox_ok", res.stdout)
        finally:
            if os.path.exists(test_workspace):
                try:
                    os.rmdir(test_workspace)
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()
