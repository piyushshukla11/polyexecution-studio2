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
        self.assertIn(LanguageId.JAVA, adapters)
        self.assertIn(LanguageId.KOTLIN, adapters)
        self.assertIn(LanguageId.SCALA, adapters)
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

    def test_java_adapter_contract(self) -> None:
        adapter = self.registry.get(LanguageId.JAVA)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.display_name, "Java")
        self.assertEqual(adapter.family, RuntimeFamily.JVM)
        self.assertEqual(adapter.seccomp_policy, SeccompPolicyProfile.JVM)
        self.assertTrue(adapter.is_installed)

        # CompileSpec verification
        self.assertIsNotNone(adapter.compile_spec)
        assert adapter.compile_spec is not None
        self.assertTrue(os.path.isabs(adapter.compile_spec.compiler_bin))
        self.assertEqual(adapter.compile_spec.source_filename, "Solution.java")
        self.assertEqual(adapter.compile_spec.artifact_output_name, "Solution.class")
        self.assertEqual(adapter.compile_spec.seccomp_profile, SeccompPolicyProfile.JVM)
        self.assertGreaterEqual(adapter.compile_spec.compile_resource_limits.memory_kb, 262144)

        # ExecutionSpec verification
        self.assertTrue(os.path.isabs(adapter.execution_spec.runner_bin))
        self.assertEqual(adapter.execution_spec.source_filename, "Solution.java")
        self.assertIn("Solution", adapter.execution_spec.execution_args)
        self.assertEqual(adapter.execution_spec.seccomp_profile, SeccompPolicyProfile.JVM)
        self.assertGreaterEqual(adapter.execution_spec.default_resource_limits.memory_kb, 262144)

    def test_uninstalled_jvm_stubs(self) -> None:
        kotlin_adapter = self.registry.get(LanguageId.KOTLIN)
        self.assertIsNotNone(kotlin_adapter)
        assert kotlin_adapter is not None
        self.assertEqual(kotlin_adapter.display_name, "Kotlin")
        self.assertEqual(kotlin_adapter.family, RuntimeFamily.JVM)
        self.assertFalse(kotlin_adapter.is_installed)
        # Truthful audit: kotlinc exists in user mise path but is excluded from sandbox rootfs
        self.assertTrue(
            kotlin_adapter.installed_version.startswith("Unavailable to sandbox rootfs")
            or kotlin_adapter.installed_version == "Not installed on host"
        )

        scala_adapter = self.registry.get(LanguageId.SCALA)
        self.assertIsNotNone(scala_adapter)
        assert scala_adapter is not None
        self.assertEqual(scala_adapter.display_name, "Scala")
        self.assertEqual(scala_adapter.family, RuntimeFamily.JVM)
        self.assertFalse(scala_adapter.is_installed)
        self.assertEqual(scala_adapter.installed_version, "Not installed on host")

    def test_ruby_adapter_contract(self) -> None:
        adapter = self.registry.get(LanguageId.RUBY)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.display_name, "Ruby")
        self.assertEqual(adapter.family, RuntimeFamily.INTERPRETED)
        self.assertEqual(adapter.seccomp_policy, SeccompPolicyProfile.RUBY)
        self.assertTrue(adapter.is_installed)

        # Single-stage execution: compile_spec MUST be None to never emit COMPILING
        self.assertIsNone(adapter.compile_spec)

        # ExecutionSpec verification
        self.assertTrue(os.path.isabs(adapter.execution_spec.runner_bin))
        self.assertEqual(adapter.execution_spec.source_filename, "Solution.rb")
        self.assertIn("/workspace/Solution.rb", adapter.execution_spec.execution_args)
        self.assertEqual(adapter.execution_spec.seccomp_profile, SeccompPolicyProfile.RUBY)
        self.assertGreaterEqual(adapter.execution_spec.default_resource_limits.memory_kb, 65536)

    def test_javascript_adapter_contract(self) -> None:
        adapter = self.registry.get(LanguageId.JAVASCRIPT)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.display_name, "JavaScript (Node.js)")
        self.assertEqual(adapter.family, RuntimeFamily.INTERPRETED)
        self.assertEqual(adapter.seccomp_policy, SeccompPolicyProfile.NODE)
        self.assertTrue(adapter.is_installed)
        self.assertEqual(adapter.installed_version, "v26.12.0")

        # Single-stage execution: compile_spec MUST be None to never emit COMPILING
        self.assertIsNone(adapter.compile_spec)

        # ExecutionSpec verification
        self.assertTrue(os.path.isabs(adapter.execution_spec.runner_bin))
        self.assertEqual(adapter.execution_spec.source_filename, "Solution.js")
        self.assertIn("/workspace/Solution.js", adapter.execution_spec.execution_args)
        self.assertEqual(adapter.execution_spec.seccomp_profile, SeccompPolicyProfile.NODE)
        self.assertEqual(adapter.execution_spec.default_resource_limits.memory_kb, 131072)

    def test_php_adapter_contract(self) -> None:
        adapter = self.registry.get(LanguageId.PHP)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.display_name, "PHP")
        self.assertEqual(adapter.family, RuntimeFamily.INTERPRETED)
        self.assertEqual(adapter.seccomp_policy, SeccompPolicyProfile.PHP)
        self.assertTrue(adapter.is_installed)
        self.assertIn("PHP 8.5", adapter.installed_version)

        # Single-stage execution: compile_spec MUST be None to never emit COMPILING
        self.assertIsNone(adapter.compile_spec)

        # ExecutionSpec verification
        self.assertTrue(os.path.isabs(adapter.execution_spec.runner_bin))
        self.assertEqual(adapter.execution_spec.source_filename, "Solution.php")
        self.assertIn("/workspace/Solution.php", adapter.execution_spec.execution_args)
        self.assertEqual(adapter.execution_spec.seccomp_profile, SeccompPolicyProfile.PHP)
        self.assertEqual(adapter.execution_spec.default_resource_limits.memory_kb, 65536)

    def test_typescript_adapter_contract(self) -> None:
        adapter = self.registry.get(LanguageId.TYPESCRIPT)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.display_name, "TypeScript")
        self.assertEqual(adapter.family, RuntimeFamily.INTERPRETED)
        self.assertEqual(adapter.seccomp_policy, SeccompPolicyProfile.NODE)
        self.assertTrue(adapter.is_installed)
        self.assertIn("Version 6.0", adapter.installed_version)

        # 2-stage execution: compile_spec MUST be present to emit COMPILING and produce Solution.js
        self.assertIsNotNone(adapter.compile_spec)
        assert adapter.compile_spec is not None
        self.assertTrue(os.path.isabs(adapter.compile_spec.compiler_bin))
        self.assertEqual(adapter.compile_spec.source_filename, "Solution.ts")
        self.assertEqual(adapter.compile_spec.artifact_output_name, "Solution.js")
        self.assertIn("NODE_DISABLE_COMPILE_CACHE=1", adapter.compile_spec.compile_args)
        self.assertIn("/workspace/Solution.ts", adapter.compile_spec.compile_args)
        self.assertEqual(adapter.compile_spec.seccomp_profile, SeccompPolicyProfile.NODE)
        self.assertEqual(adapter.compile_spec.compile_resource_limits.memory_kb, 262144)

        # ExecutionSpec verification
        self.assertTrue(os.path.isabs(adapter.execution_spec.runner_bin))
        self.assertEqual(adapter.execution_spec.source_filename, "Solution.js")
        self.assertIn("/workspace/Solution.js", adapter.execution_spec.execution_args)
        self.assertEqual(adapter.execution_spec.seccomp_profile, SeccompPolicyProfile.NODE)
        self.assertEqual(adapter.execution_spec.default_resource_limits.memory_kb, 131072)

    def test_dart_adapter_contract(self) -> None:
        adapter = self.registry.get(LanguageId.DART)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.family, RuntimeFamily.INTERPRETED)
        self.assertIsNone(adapter.compile_spec)
        self.assertEqual(adapter.execution_spec.runner_bin, "/usr/bin/dart")
        self.assertEqual(
            adapter.execution_spec.execution_args,
            ["--suppress-analytics", "run", "/workspace/Solution.dart"],
        )
        self.assertEqual(adapter.execution_spec.source_filename, "Solution.dart")
        self.assertEqual(adapter.execution_spec.seccomp_profile, SeccompPolicyProfile.DART)
        self.assertEqual(adapter.seccomp_policy, SeccompPolicyProfile.DART)
        self.assertEqual(adapter.execution_spec.default_resource_limits.memory_kb, 196608)
        self.assertEqual(adapter.execution_spec.default_resource_limits.wall_ms, 10000)
        self.assertEqual(adapter.execution_spec.default_resource_limits.cpu_ms, 5000)
        self.assertEqual(adapter.execution_spec.default_resource_limits.pids, 32)
        self.assertEqual(adapter.execution_spec.default_resource_limits.max_output_bytes, 65536)

    def test_phase5_unsupported_runtimes_truthful(self) -> None:
        unsupported = [
            LanguageId.PYTHON2,
            LanguageId.SWIFT,
            LanguageId.RACKET,
            LanguageId.ERLANG,
            LanguageId.ELIXIR,
            LanguageId.CSHARP,
        ]
        for lang_id in unsupported:
            adapter = self.registry.get(lang_id)
            self.assertIsNotNone(adapter, f"{lang_id} should be registered in registry")
            assert adapter is not None
            self.assertFalse(
                adapter.is_installed,
                f"{lang_id} is unavailable or incomplete and must be reported uninstalled/unsupported",
            )
            self.assertIsNone(adapter.compile_spec)

        # Truthful classification checks
        csharp_adapter = self.registry.get(LanguageId.CSHARP)
        assert csharp_adapter is not None
        self.assertTrue(
            csharp_adapter.installed_version.startswith("Incomplete toolchain on host"),
            f"Expected dotnet without SDK to be reported incomplete: {csharp_adapter.installed_version}",
        )
        self.assertIn("launcher present", csharp_adapter.installed_version)

        for lang_id in [
            LanguageId.PYTHON2,
            LanguageId.SWIFT,
            LanguageId.RACKET,
            LanguageId.ERLANG,
            LanguageId.ELIXIR,
        ]:
            ad = self.registry.get(lang_id)
            assert ad is not None
            self.assertEqual(ad.installed_version, "Not installed on host")


if __name__ == "__main__":
    unittest.main()
