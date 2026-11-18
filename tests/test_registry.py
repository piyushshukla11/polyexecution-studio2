"""Tests for RuntimeRegistry and Tier 1 typed RuntimeAdapters."""

import os
import unittest

from engine.models import (
    LanguageId,
    RuntimeFamily,
    SeccompPolicyProfile,
)
from engine.registry import RuntimeRegistry


class TestRuntimeRegistry(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = RuntimeRegistry()

    def test_tier1_runtimes_present(self) -> None:
        adapters = self.registry.list_adapters()
        self.assertIn(LanguageId.C, adapters)
        self.assertIn(LanguageId.CPP, adapters)
        self.assertIn(LanguageId.PYTHON3, adapters)
        self.assertIn(LanguageId.BASH, adapters)
        self.assertIn(LanguageId.GO, adapters)
        self.assertIn(LanguageId.RUST, adapters)

    def test_c_adapter_contract(self) -> None:
        adapter = self.registry.get(LanguageId.C)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.display_name, "C")
        self.assertEqual(adapter.family, RuntimeFamily.NATIVE)
        self.assertEqual(adapter.seccomp_policy, SeccompPolicyProfile.STRICT_NATIVE)

        # CompileSpec verification
        self.assertIsNotNone(adapter.compile_spec)
        assert adapter.compile_spec is not None
        self.assertTrue(os.path.isabs(adapter.compile_spec.compiler_bin))
        self.assertIn("-O2", adapter.compile_spec.compile_args)
        self.assertEqual(adapter.compile_spec.source_filename, "solution.c")
        self.assertEqual(adapter.compile_spec.artifact_output_name, "a.out")
        self.assertGreater(adapter.compile_spec.compile_resource_limits.cpu_ms, 0)
        self.assertGreater(adapter.compile_spec.compile_resource_limits.memory_kb, 0)

        # ExecutionSpec verification
        self.assertEqual(adapter.execution_spec.runner_bin, "/workspace/a.out")
        self.assertGreater(adapter.execution_spec.default_resource_limits.cpu_ms, 0)
        self.assertGreater(adapter.execution_spec.default_resource_limits.memory_kb, 0)

    def test_cpp_adapter_contract(self) -> None:
        adapter = self.registry.get(LanguageId.CPP)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.display_name, "C++")
        self.assertEqual(adapter.family, RuntimeFamily.NATIVE)

        self.assertIsNotNone(adapter.compile_spec)
        assert adapter.compile_spec is not None
        self.assertTrue(os.path.isabs(adapter.compile_spec.compiler_bin))
        self.assertEqual(adapter.compile_spec.source_filename, "solution.cpp")
        self.assertEqual(adapter.compile_spec.artifact_output_name, "a.out")
        self.assertEqual(adapter.execution_spec.runner_bin, "/workspace/a.out")

    def test_python3_adapter_contract(self) -> None:
        adapter = self.registry.get(LanguageId.PYTHON3)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.display_name, "Python 3")
        self.assertEqual(adapter.family, RuntimeFamily.INTERPRETED)

        # Truthful single-stage: compile_spec MUST be None
        self.assertIsNone(adapter.compile_spec)

        # ExecutionSpec verification
        self.assertTrue(os.path.isabs(adapter.execution_spec.runner_bin))
        self.assertIn("/workspace/solution.py", adapter.execution_spec.execution_args)
        self.assertGreater(adapter.execution_spec.default_resource_limits.cpu_ms, 0)
        self.assertGreater(adapter.execution_spec.default_resource_limits.memory_kb, 0)

    def test_lookup_by_name(self) -> None:
        self.assertEqual(self.registry.get_by_name("c"), self.registry.get(LanguageId.C))
        self.assertEqual(self.registry.get_by_name("cpp"), self.registry.get(LanguageId.CPP))
        self.assertEqual(self.registry.get_by_name("python3"), self.registry.get(LanguageId.PYTHON3))
        self.assertEqual(self.registry.get_by_name("bash"), self.registry.get(LanguageId.BASH))
        self.assertEqual(self.registry.get_by_name("go"), self.registry.get(LanguageId.GO))
        self.assertEqual(self.registry.get_by_name("rust"), self.registry.get(LanguageId.RUST))
        self.assertIsNone(self.registry.get_by_name("unknown_lang"))

    def test_bash_adapter_contract(self) -> None:
        adapter = self.registry.get(LanguageId.BASH)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.display_name, "Bash")
        self.assertEqual(adapter.family, RuntimeFamily.INTERPRETED)
        self.assertEqual(adapter.seccomp_policy, SeccompPolicyProfile.BASH)
        self.assertTrue(adapter.is_installed)

        # CompileSpec verification (bash -n syntax validation)
        self.assertIsNotNone(adapter.compile_spec)
        assert adapter.compile_spec is not None
        self.assertTrue(os.path.isabs(adapter.compile_spec.compiler_bin))
        self.assertIn("-n", adapter.compile_spec.compile_args)
        self.assertEqual(adapter.compile_spec.source_filename, "solution.sh")
        self.assertEqual(adapter.compile_spec.artifact_output_name, "solution.sh")
        self.assertEqual(adapter.compile_spec.seccomp_profile, SeccompPolicyProfile.BASH)

        # ExecutionSpec verification
        self.assertTrue(os.path.isabs(adapter.execution_spec.runner_bin))
        self.assertIn("/workspace/solution.sh", adapter.execution_spec.execution_args)
        self.assertEqual(adapter.execution_spec.seccomp_profile, SeccompPolicyProfile.BASH)
        self.assertEqual(adapter.execution_spec.source_filename, "solution.sh")

    def test_uninstalled_go_rust_stubs(self) -> None:
        go_adapter = self.registry.get(LanguageId.GO)
        self.assertIsNotNone(go_adapter)
        assert go_adapter is not None
        self.assertEqual(go_adapter.display_name, "Go")
        self.assertFalse(go_adapter.is_installed)
        self.assertEqual(go_adapter.installed_version, "Not installed on host")

        rust_adapter = self.registry.get(LanguageId.RUST)
        self.assertIsNotNone(rust_adapter)
        assert rust_adapter is not None
        self.assertEqual(rust_adapter.display_name, "Rust")
        self.assertFalse(rust_adapter.is_installed)
        self.assertEqual(rust_adapter.installed_version, "Not installed on host")


if __name__ == "__main__":
    unittest.main()
