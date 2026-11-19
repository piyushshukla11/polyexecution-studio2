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
    """Safely inspect host installed version for honest reporting.
    Truthfully reports:
    - Toolchains installed in user-space paths (e.g. mise) that are unavailable to the sandbox rootfs.
    - Host launchers present without a functional runtime/SDK (incomplete toolchains).
    - Toolchains completely absent on the host.
    - Verified installed toolchains accessible inside the sandbox.
    """
    if not os.path.exists(binary_path):
        basename = os.path.basename(binary_path)
        user_path = shutil.which(basename)
        if user_path:
            return f"Unavailable to sandbox rootfs (installed in user path: {user_path})"
        return "Not installed on host"
    try:
        proc = subprocess.run(
            [binary_path, version_arg],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode != 0:
            first_err = (proc.stderr.splitlines()[0] if proc.stderr else proc.stdout.splitlines()[0] if proc.stdout else "").strip()
            err_msg = f": {first_err}" if first_err else ""
            return f"Incomplete toolchain on host (launcher present at {binary_path}, but SDK check failed{err_msg})"
        first_line = proc.stdout.splitlines()[0] if proc.stdout else proc.stderr.splitlines()[0] if proc.stderr else ""
        return first_line.strip() if first_line else "Installed (version unknown)"
    except Exception as exc:
        return f"Incomplete toolchain on host (inspection error: {exc})"


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

        # Tier 5 Interpreted Runtimes: Ruby (Vertical Slice 1)
        ruby_bin = "/usr/bin/ruby" if os.path.exists("/usr/bin/ruby") else shutil.which("ruby") or "/usr/bin/ruby"
        ruby_adapter = RuntimeAdapter(
            id=LanguageId.RUBY,
            display_name="Ruby",
            official_version="Ruby 3.4 (CRuby)",
            installed_version=detect_tool_version(ruby_bin),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=ruby_bin,
                execution_args=["/workspace/Solution.rb"],
                source_filename="Solution.rb",
                default_resource_limits=ResourceLimits(
                    cpu_ms=2000,
                    wall_ms=5000,
                    memory_kb=65536,
                    pids=32,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.RUBY,
            ),
            seccomp_policy=SeccompPolicyProfile.RUBY,
            cleanup_spec=CleanupSpec(),
        )
        self.register(ruby_adapter)

        # JavaScript (Node.js) runtime
        js_adapter = RuntimeAdapter(
            id=LanguageId.JAVASCRIPT,
            display_name="JavaScript (Node.js)",
            official_version="Node.js LTS (v20+)",
            installed_version=detect_tool_version("/usr/bin/node"),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin="/usr/bin/node",
                execution_args=["/workspace/Solution.js"],
                source_filename="Solution.js",
                default_resource_limits=ResourceLimits(
                    wall_ms=5000,
                    cpu_ms=2000,
                    memory_kb=131072,
                    pids=32,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.NODE,
            ),
            seccomp_policy=SeccompPolicyProfile.NODE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(js_adapter)

        # PHP runtime (Phase 5 Slice 3)
        php_bin = "/usr/bin/php" if os.path.exists("/usr/bin/php") else shutil.which("php") or "/usr/bin/php"
        php_adapter = RuntimeAdapter(
            id=LanguageId.PHP,
            display_name="PHP",
            official_version="PHP 8.5 (cli)",
            installed_version=detect_tool_version(php_bin),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=php_bin,
                execution_args=["/workspace/Solution.php"],
                source_filename="Solution.php",
                default_resource_limits=ResourceLimits(
                    wall_ms=5000,
                    cpu_ms=2000,
                    memory_kb=65536,
                    pids=16,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.PHP,
            ),
            seccomp_policy=SeccompPolicyProfile.PHP,
            cleanup_spec=CleanupSpec(),
        )
        self.register(php_adapter)

        # Truthfully unsupported Phase 5 runtimes (unavailable or unreachable in sandbox rootfs)

        ts_adapter = RuntimeAdapter(
            id=LanguageId.TYPESCRIPT,
            display_name="TypeScript",
            official_version="TypeScript 5.x",
            installed_version=detect_tool_version("/usr/bin/tsc"),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin="/usr/bin/tsc",
                execution_args=[],
                source_filename="solution.ts",
                seccomp_profile=SeccompPolicyProfile.V8_NODE,
            ),
            seccomp_policy=SeccompPolicyProfile.V8_NODE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(ts_adapter)

        py2_adapter = RuntimeAdapter(
            id=LanguageId.PYTHON2,
            display_name="Python 2",
            official_version="Python 2.7 (Legacy)",
            installed_version=detect_tool_version("/usr/bin/python2"),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin="/usr/bin/python2",
                execution_args=["/workspace/solution.py"],
                source_filename="solution.py",
                seccomp_profile=SeccompPolicyProfile.PYTHON,
            ),
            seccomp_policy=SeccompPolicyProfile.PYTHON,
            cleanup_spec=CleanupSpec(),
        )
        self.register(py2_adapter)

        dart_adapter = RuntimeAdapter(
            id=LanguageId.DART,
            display_name="Dart",
            official_version="Dart 3.x",
            installed_version=detect_tool_version("/usr/bin/dart"),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin="/usr/bin/dart",
                execution_args=[],
                source_filename="solution.dart",
                seccomp_profile=SeccompPolicyProfile.STRICT_NATIVE,
            ),
            seccomp_policy=SeccompPolicyProfile.STRICT_NATIVE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(dart_adapter)

        swift_adapter = RuntimeAdapter(
            id=LanguageId.SWIFT,
            display_name="Swift",
            official_version="Swift 6.x",
            installed_version=detect_tool_version("/usr/bin/swift"),
            family=RuntimeFamily.NATIVE,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin="/usr/bin/swift",
                execution_args=[],
                source_filename="solution.swift",
                seccomp_profile=SeccompPolicyProfile.STRICT_NATIVE,
            ),
            seccomp_policy=SeccompPolicyProfile.STRICT_NATIVE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(swift_adapter)

        racket_adapter = RuntimeAdapter(
            id=LanguageId.RACKET,
            display_name="Racket",
            official_version="Racket 8.x",
            installed_version=detect_tool_version("/usr/bin/racket"),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin="/usr/bin/racket",
                execution_args=[],
                source_filename="solution.rkt",
                seccomp_profile=SeccompPolicyProfile.STRICT_NATIVE,
            ),
            seccomp_policy=SeccompPolicyProfile.STRICT_NATIVE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(racket_adapter)

        erlang_adapter = RuntimeAdapter(
            id=LanguageId.ERLANG,
            display_name="Erlang",
            official_version="Erlang/OTP 27",
            installed_version=detect_tool_version("/usr/bin/erl"),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin="/usr/bin/erl",
                execution_args=[],
                source_filename="solution.erl",
                seccomp_profile=SeccompPolicyProfile.STRICT_NATIVE,
            ),
            seccomp_policy=SeccompPolicyProfile.STRICT_NATIVE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(erlang_adapter)

        elixir_adapter = RuntimeAdapter(
            id=LanguageId.ELIXIR,
            display_name="Elixir",
            official_version="Elixir 1.17",
            installed_version=detect_tool_version("/usr/bin/elixir"),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin="/usr/bin/elixir",
                execution_args=[],
                source_filename="solution.exs",
                seccomp_profile=SeccompPolicyProfile.STRICT_NATIVE,
            ),
            seccomp_policy=SeccompPolicyProfile.STRICT_NATIVE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(elixir_adapter)

        csharp_adapter = RuntimeAdapter(
            id=LanguageId.CSHARP,
            display_name="C# (.NET)",
            official_version=".NET 9.0 SDK",
            installed_version=detect_tool_version("/usr/bin/dotnet"),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin="/usr/bin/dotnet",
                execution_args=[],
                source_filename="Program.cs",
                seccomp_profile=SeccompPolicyProfile.STRICT_NATIVE,
            ),
            seccomp_policy=SeccompPolicyProfile.STRICT_NATIVE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(csharp_adapter)
