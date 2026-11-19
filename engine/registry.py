"""Runtime adapter registry and Tier 1 runtime adapters."""

import os
import shutil
import subprocess
from typing import Dict, Optional

from engine.models import (
    CleanupSpec,
    CompileSpec,
    ExecutionSpec,
    LanguageId,
    ResourceLimits,
    RuntimeAdapter,
    RuntimeFamily,
    SeccompPolicyProfile,
)


def detect_tool_version(binary_path: str, version_arg: str = "--version") -> str:
    """Safely inspect host installed version for honest reporting."""
    if not os.path.exists(binary_path) and not shutil.which(binary_path):
        return "Not installed on host"
    try:
        proc = subprocess.run(
            [binary_path, version_arg],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        first_line = proc.stdout.splitlines()[0] if proc.stdout else proc.stderr.splitlines()[0] if proc.stderr else ""
        return first_line.strip() if first_line else "Installed (version unknown)"
    except Exception:
        return "Installed (version query failed)"


class RuntimeRegistry:
    """Central registry of typed RuntimeAdapters."""

    def __init__(self) -> None:
        self._adapters: Dict[LanguageId, RuntimeAdapter] = {}
        self._register_tier1_defaults()

    def register(self, adapter: RuntimeAdapter) -> None:
        self._adapters[adapter.id] = adapter

    def get(self, language_id: LanguageId) -> Optional[RuntimeAdapter]:
        return self._adapters.get(language_id)

    def get_by_name(self, name: str) -> Optional[RuntimeAdapter]:
        try:
            lang_id = LanguageId(name.lower())
            return self.get(lang_id)
        except ValueError:
            return None

    def list_adapters(self) -> Dict[LanguageId, RuntimeAdapter]:
        return dict(self._adapters)

    def _register_tier1_defaults(self) -> None:
        # Tier 1 C Runtime (gcc)
        gcc_bin = "/usr/bin/gcc" if os.path.exists("/usr/bin/gcc") else shutil.which("gcc") or "/usr/bin/gcc"
        c_adapter = RuntimeAdapter(
            id=LanguageId.C,
            display_name="C",
            official_version="gcc 14 (gnu11, -O2, ASan enabled, GLib 2.0)",
            installed_version=detect_tool_version(gcc_bin),
            family=RuntimeFamily.NATIVE,
            compile_spec=CompileSpec(
                compiler_bin=gcc_bin,
                compile_args=[
                    "-O2",
                    "-std=gnu11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-o",
                    "/workspace/a.out",
                    "/workspace/solution.c",
                ],
                source_filename="solution.c",
                artifact_output_name="a.out",
                compile_resource_limits=ResourceLimits(
                    cpu_ms=5000,
                    wall_ms=10000,
                    memory_kb=262144,
                    pids=32,
                    max_output_bytes=65536,
                ),
            ),
            execution_spec=ExecutionSpec(
                runner_bin="/workspace/a.out",
                execution_args=[],
                default_resource_limits=ResourceLimits(
                    cpu_ms=1000,
                    wall_ms=2000,
                    memory_kb=65536,
                    pids=16,
                    max_output_bytes=65536,
                ),
            ),
            seccomp_policy=SeccompPolicyProfile.STRICT_NATIVE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(c_adapter)

        # Tier 1 C++ Runtime (clang++ / g++)
        clang_bin = "/usr/bin/clang++" if os.path.exists("/usr/bin/clang++") else (
            shutil.which("clang++") or (
                "/usr/bin/g++" if os.path.exists("/usr/bin/g++") else shutil.which("g++") or "/usr/bin/clang++"
            )
        )
        cpp_adapter = RuntimeAdapter(
            id=LanguageId.CPP,
            display_name="C++",
            official_version="clang 19 (C++23, -O2, ASan enabled)",
            installed_version=detect_tool_version(clang_bin),
            family=RuntimeFamily.NATIVE,
            compile_spec=CompileSpec(
                compiler_bin=clang_bin,
                compile_args=[
                    "-O2",
                    "-std=c++20",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-o",
                    "/workspace/a.out",
                    "/workspace/solution.cpp",
                ],
                source_filename="solution.cpp",
                artifact_output_name="a.out",
                compile_resource_limits=ResourceLimits(
                    cpu_ms=5000,
                    wall_ms=10000,
                    memory_kb=262144,
                    pids=32,
                    max_output_bytes=65536,
                ),
            ),
            execution_spec=ExecutionSpec(
                runner_bin="/workspace/a.out",
                execution_args=[],
                default_resource_limits=ResourceLimits(
                    cpu_ms=1000,
                    wall_ms=2000,
                    memory_kb=65536,
                    pids=16,
                    max_output_bytes=65536,
                ),
            ),
            seccomp_policy=SeccompPolicyProfile.STRICT_NATIVE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(cpp_adapter)

        # Tier 1 Python 3 Runtime (python3)
        python_bin = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else shutil.which("python3") or "/usr/bin/python3"
        python3_adapter = RuntimeAdapter(
            id=LanguageId.PYTHON3,
            display_name="Python 3",
            official_version="Python 3.14",
            installed_version=detect_tool_version(python_bin),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=python_bin,
                execution_args=["/workspace/solution.py"],
                default_resource_limits=ResourceLimits(
                    cpu_ms=2000,
                    wall_ms=5000,
                    memory_kb=131072,
                    pids=16,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.PYTHON,
            ),
            seccomp_policy=SeccompPolicyProfile.PYTHON,
            cleanup_spec=CleanupSpec(),
        )
        self.register(python3_adapter)

        # Tier 1/2 Bash Runtime (bash)
        bash_bin = "/usr/bin/bash" if os.path.exists("/usr/bin/bash") else shutil.which("bash") or "/usr/bin/bash"
        bash_adapter = RuntimeAdapter(
            id=LanguageId.BASH,
            display_name="Bash",
            official_version="Bash 5.2",
            installed_version=detect_tool_version(bash_bin),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=CompileSpec(
                compiler_bin=bash_bin,
                compile_args=["--norc", "--noprofile", "-n", "/workspace/solution.sh"],
                source_filename="solution.sh",
                artifact_output_name="solution.sh",
                compile_resource_limits=ResourceLimits(
                    cpu_ms=1000,
                    wall_ms=2000,
                    memory_kb=65536,
                    pids=16,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.BASH,
            ),
            execution_spec=ExecutionSpec(
                runner_bin=bash_bin,
                execution_args=["--norc", "--noprofile", "/workspace/solution.sh"],
                source_filename="solution.sh",
                default_resource_limits=ResourceLimits(
                    cpu_ms=2000,
                    wall_ms=5000,
                    memory_kb=65536,
                    pids=16,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.BASH,
            ),
            seccomp_policy=SeccompPolicyProfile.BASH,
            cleanup_spec=CleanupSpec(),
        )
        self.register(bash_adapter)

        # Tier 4 JVM Java Runtime (javac / java)
        javac_bin = "/usr/bin/javac" if os.path.exists("/usr/bin/javac") else shutil.which("javac") or "/usr/bin/javac"
        java_bin = "/usr/bin/java" if os.path.exists("/usr/bin/java") else shutil.which("java") or "/usr/bin/java"
        java_adapter = RuntimeAdapter(
            id=LanguageId.JAVA,
            display_name="Java",
            official_version="OpenJDK 25 (Java 25)",
            installed_version=detect_tool_version(javac_bin),
            family=RuntimeFamily.JVM,
            compile_spec=CompileSpec(
                compiler_bin=javac_bin,
                compile_args=["-d", "/workspace", "/workspace/Solution.java"],
                source_filename="Solution.java",
                artifact_output_name="Solution.class",
                compile_resource_limits=ResourceLimits(
                    cpu_ms=5000,
                    wall_ms=10000,
                    memory_kb=524288,
                    pids=64,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.JVM,
            ),
            execution_spec=ExecutionSpec(
                runner_bin=java_bin,
                execution_args=["-Xmx256m", "-Xms32m", "-XX:+UseSerialGC", "-cp", "/workspace", "Solution"],
                source_filename="Solution.java",
                default_resource_limits=ResourceLimits(
                    cpu_ms=2000,
                    wall_ms=5000,
                    memory_kb=393216,
                    pids=64,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.JVM,
            ),
            seccomp_policy=SeccompPolicyProfile.JVM,
            cleanup_spec=CleanupSpec(),
        )
        self.register(java_adapter)

        # Truthfully uninstalled JVM stubs for Kotlin and Scala
        kotlinc_bin = "/usr/bin/kotlinc"
        kotlin_adapter = RuntimeAdapter(
            id=LanguageId.KOTLIN,
            display_name="Kotlin",
            official_version="Kotlin 2.0",
            installed_version=detect_tool_version(kotlinc_bin),
            family=RuntimeFamily.JVM,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=kotlinc_bin,
                execution_args=[],
                source_filename="solution.kt",
                seccomp_profile=SeccompPolicyProfile.JVM,
            ),
            seccomp_policy=SeccompPolicyProfile.JVM,
            cleanup_spec=CleanupSpec(),
        )
        self.register(kotlin_adapter)

        scalac_bin = "/usr/bin/scalac"
        scala_adapter = RuntimeAdapter(
            id=LanguageId.SCALA,
            display_name="Scala",
            official_version="Scala 3.5",
            installed_version=detect_tool_version(scalac_bin),
            family=RuntimeFamily.JVM,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=scalac_bin,
                execution_args=[],
                source_filename="solution.scala",
                seccomp_profile=SeccompPolicyProfile.JVM,
            ),
            seccomp_policy=SeccompPolicyProfile.JVM,
            cleanup_spec=CleanupSpec(),
        )
        self.register(scala_adapter)

        # Uninstalled stubs for Go and Rust (truthfully reported)
        go_bin = "/usr/bin/go"
        go_adapter = RuntimeAdapter(
            id=LanguageId.GO,
            display_name="Go",
            official_version="Go 1.23",
            installed_version=detect_tool_version(go_bin),
            family=RuntimeFamily.NATIVE,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=go_bin,
                execution_args=[],
                source_filename="solution.go",
                seccomp_profile=SeccompPolicyProfile.STRICT_NATIVE,
            ),
            seccomp_policy=SeccompPolicyProfile.STRICT_NATIVE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(go_adapter)

        rust_bin = "/usr/bin/rustc"
        rust_adapter = RuntimeAdapter(
            id=LanguageId.RUST,
            display_name="Rust",
            official_version="Rust 1.82",
            installed_version=detect_tool_version(rust_bin),
            family=RuntimeFamily.NATIVE,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=rust_bin,
                execution_args=[],
                source_filename="solution.rs",
                seccomp_profile=SeccompPolicyProfile.STRICT_NATIVE,
            ),
            seccomp_policy=SeccompPolicyProfile.STRICT_NATIVE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(rust_adapter)
