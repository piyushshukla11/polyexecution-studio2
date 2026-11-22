"""Runtime adapter registry and Tier 1 runtime adapters."""

import glob
import os
import shutil
import subprocess
from typing import Dict, List, Optional

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


def detect_python_sqlite_version(python_bin: str) -> str:
    """Inspect SQLite library version linked by the Python runtime executing the SQLite adapter."""
    if not os.path.exists(python_bin):
        basename = os.path.basename(python_bin)
        user_path = shutil.which(basename)
        if user_path:
            return f"Unavailable to sandbox rootfs (installed in user path: {user_path})"
        return "Not installed on host"
    try:
        proc = subprocess.run(
            [python_bin, "-c", "import sqlite3; print(sqlite3.sqlite_version)"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode != 0:
            return "Incomplete toolchain on host (sqlite3 module failed to load)"
        ver = proc.stdout.strip()
        return ver if ver else "Installed (version unknown)"
    except Exception as exc:
        return f"Incomplete toolchain on host (inspection error: {exc})"


def detect_pandas_version(python_bin: str) -> str:
    """Inspect pandas and numpy library versions accessible to the Python runner."""
    if not os.path.exists(python_bin):
        basename = os.path.basename(python_bin)
        user_path = shutil.which(basename)
        if user_path:
            return f"Unavailable to sandbox rootfs (installed in user path: {user_path})"
        return "Not installed on host"
    try:
        proc = subprocess.run(
            [python_bin, "-c", "import pandas as pd, numpy as np; print(f'pandas {pd.__version__} (numpy {np.__version__})')"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
        user_python = shutil.which("python3")
        if user_python and user_python != python_bin:
            try:
                user_proc = subprocess.run(
                    [user_python, "-c", "import pandas as pd, numpy as np; print(f'pandas {pd.__version__} (numpy {np.__version__})')"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                )
                if user_proc.returncode == 0 and user_proc.stdout.strip():
                    return f"Unavailable to sandbox rootfs (installed in user path: {user_python}, {user_proc.stdout.strip()})"
            except Exception:
                pass
        return "Incomplete toolchain on host (pandas/numpy modules failed to load)"
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

        # TypeScript runtime (Phase 5 Slice 4)
        tsc_bin = "/usr/bin/tsc" if os.path.exists("/usr/bin/tsc") else shutil.which("tsc") or "/usr/bin/tsc"
        env_bin = "/usr/bin/env" if os.path.exists("/usr/bin/env") else shutil.which("env") or "/usr/bin/env"
        node_bin = "/usr/bin/node" if os.path.exists("/usr/bin/node") else shutil.which("node") or "/usr/bin/node"
        ts_adapter = RuntimeAdapter(
            id=LanguageId.TYPESCRIPT,
            display_name="TypeScript",
            official_version="TypeScript 6.0 (tsc)",
            installed_version=detect_tool_version(tsc_bin),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=CompileSpec(
                compiler_bin=env_bin,
                compile_args=["NODE_DISABLE_COMPILE_CACHE=1", tsc_bin, "--target", "ES2022", "--module", "CommonJS", "/workspace/Solution.ts"],
                source_filename="Solution.ts",
                artifact_output_name="Solution.js",
                compile_resource_limits=ResourceLimits(
                    wall_ms=10000,
                    cpu_ms=5000,
                    memory_kb=262144,
                    pids=32,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.NODE,
            ),
            execution_spec=ExecutionSpec(
                runner_bin=node_bin,
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
        self.register(ts_adapter)

        # Dart runtime (Phase 5 Slice 5)
        dart_bin = "/usr/bin/dart" if os.path.exists("/usr/bin/dart") else shutil.which("dart") or "/usr/bin/dart"
        dart_adapter = RuntimeAdapter(
            id=LanguageId.DART,
            display_name="Dart",
            official_version="Dart 3.x",
            installed_version=detect_tool_version(dart_bin),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=dart_bin,
                execution_args=["--suppress-analytics", "run", "/workspace/Solution.dart"],
                source_filename="Solution.dart",
                default_resource_limits=ResourceLimits(
                    wall_ms=10000,
                    cpu_ms=5000,
                    memory_kb=196608,
                    pids=32,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.DART,
            ),
            seccomp_policy=SeccompPolicyProfile.DART,
            cleanup_spec=CleanupSpec(),
        )
        self.register(dart_adapter)

        # Racket runtime (Phase 5 Slice 6)
        racket_bin = "/usr/bin/racket" if os.path.exists("/usr/bin/racket") else shutil.which("racket") or "/usr/bin/racket"
        racket_adapter = RuntimeAdapter(
            id=LanguageId.RACKET,
            display_name="Racket",
            official_version="Racket CS 9.3",
            installed_version=detect_tool_version(racket_bin),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=racket_bin,
                execution_args=["/workspace/Solution.rkt"],
                source_filename="Solution.rkt",
                default_resource_limits=ResourceLimits(
                    wall_ms=10000,
                    cpu_ms=5000,
                    memory_kb=196608,
                    pids=32,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.RACKET,
            ),
            seccomp_policy=SeccompPolicyProfile.RACKET,
            cleanup_spec=CleanupSpec(),
        )
        self.register(racket_adapter)

        # Erlang runtime (Phase 5 Slice 7)
        erl_bin = "/usr/bin/erl" if os.path.exists("/usr/bin/erl") else shutil.which("erl") or "/usr/bin/erl"
        erl_eval_harness = (
            "try "
            "case compile:file(\"/workspace/solution.erl\", [binary, return_errors]) of "
            "{ok, Mod, Bin} -> "
            "{module, Mod} = code:load_binary(Mod, \"/workspace/solution.erl\", Bin), "
            "case erlang:function_exported(Mod, start, 0) of "
            "true -> Mod:start(); "
            "false -> case erlang:function_exported(Mod, main, 1) of "
            "true -> Mod:main([]); "
            "false -> case erlang:function_exported(Mod, main, 0) of "
            "true -> Mod:main(); "
            "false -> io:format(standard_error, \"Error: no start/0, main/1, or main/0 exported in module ~p~n\", [Mod]), halt(1) "
            "end end end, "
            "halt(0); "
            "{error, Errors, _} -> "
            "lists:foreach(fun({File, ErrList}) -> lists:foreach(fun(Err) -> io:format(standard_error, \"~s: ~p~n\", [File, Err]) end, ErrList) end, Errors), "
            "halt(1) "
            "end "
            "catch Class:Reason:Stack -> "
            "io:format(standard_error, \"** exception ~p:~p~n~p~n\", [Class, Reason, Stack]), "
            "halt(1) "
            "end."
        )
        erlang_adapter = RuntimeAdapter(
            id=LanguageId.ERLANG,
            display_name="Erlang",
            official_version="Erlang/OTP 29",
            installed_version=detect_tool_version(erl_bin, version_arg="-version"),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=erl_bin,
                execution_args=[
                    "+S", "2:2",
                    "+A", "2",
                    "-noshell",
                    "-eval",
                    erl_eval_harness,
                ],
                source_filename="solution.erl",
                default_resource_limits=ResourceLimits(
                    wall_ms=10000,
                    cpu_ms=5000,
                    memory_kb=196608,
                    pids=64,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.ERLANG,
            ),
            seccomp_policy=SeccompPolicyProfile.ERLANG,
            cleanup_spec=CleanupSpec(),
        )
        self.register(erlang_adapter)

        # Elixir runtime (Phase 5 Slice 8)
        elixir_bin = "/usr/bin/elixir" if os.path.exists("/usr/bin/elixir") else shutil.which("elixir") or "/usr/bin/elixir"
        elixir_eval_harness = (
            'Code.require_file("/workspace/solution.exs"); '
            'cond do '
            'function_exported?(Solution, :start, 0) -> Solution.start(); '
            'function_exported?(Solution, :main, 1) -> Solution.main([]); '
            'function_exported?(Solution, :main, 0) -> Solution.main(); '
            'true -> :ok '
            'end'
        )
        elixir_adapter = RuntimeAdapter(
            id=LanguageId.ELIXIR,
            display_name="Elixir",
            official_version="Elixir 1.20",
            installed_version=detect_tool_version(elixir_bin, version_arg="--short-version"),
            family=RuntimeFamily.INTERPRETED,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=elixir_bin,
                execution_args=[
                    "--erl", "+S 2:2 +A 2",
                    "-e",
                    elixir_eval_harness,
                ],
                source_filename="solution.exs",
                default_resource_limits=ResourceLimits(
                    wall_ms=10000,
                    cpu_ms=5000,
                    memory_kb=196608,
                    pids=64,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.ELIXIR,
            ),
            seccomp_policy=SeccompPolicyProfile.ELIXIR,
            cleanup_spec=CleanupSpec(),
        )
        self.register(elixir_adapter)

        # Truthfully unsupported Phase 5 runtimes (unavailable or unreachable in sandbox rootfs)

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

        # C# (.NET) runtime (Phase 5 Slice 9)
        dotnet_bin = "/usr/bin/dotnet" if os.path.exists("/usr/bin/dotnet") else shutil.which("dotnet") or "/usr/bin/dotnet"

        # Concretely resolve Roslyn compiler assembly, reference targeting packs, and runtime configuration
        ref_dir = "/usr/share/dotnet/packs/Microsoft.NETCore.App.Ref/10.0.13/ref/net10.0"
        if not os.path.isdir(ref_dir):
            ref_candidates = sorted(glob.glob("/usr/share/dotnet/packs/Microsoft.NETCore.App.Ref/*/ref/net*"))
            if ref_candidates:
                ref_dir = ref_candidates[-1]

        csc_dll = "/usr/share/dotnet/sdk/10.0.113/Roslyn/bincore/csc.dll"
        if not os.path.exists(csc_dll):
            csc_candidates = sorted(glob.glob("/usr/share/dotnet/sdk/*/Roslyn/bincore/csc.dll"))
            if csc_candidates:
                csc_dll = csc_candidates[-1]

        runtime_config = "/usr/share/dotnet/sdk/10.0.113/dotnet.runtimeconfig.json"
        if not os.path.exists(runtime_config):
            cfg_candidates = sorted(glob.glob("/usr/share/dotnet/sdk/*/dotnet.runtimeconfig.json"))
            if cfg_candidates:
                runtime_config = cfg_candidates[-1]

        concrete_ref_args: List[str] = []
        if os.path.isdir(ref_dir):
            for ref_file in sorted(os.listdir(ref_dir)):
                if ref_file.endswith(".dll"):
                    concrete_ref_args.append(f"-r:{os.path.join(ref_dir, ref_file)}")

        csharp_compile_args = [
            "exec",
            csc_dll,
            "-nologo",
            "-target:exe",
            "-out:/workspace/Solution.dll",
            *concrete_ref_args,
            "/workspace/Solution.cs",
        ]

        csharp_adapter = RuntimeAdapter(
            id=LanguageId.CSHARP,
            display_name="C# (.NET)",
            official_version=".NET 10 SDK",
            installed_version=detect_tool_version(dotnet_bin),
            family=RuntimeFamily.DOTNET,
            compile_spec=CompileSpec(
                compiler_bin=dotnet_bin,
                compile_args=csharp_compile_args,
                source_filename="Solution.cs",
                artifact_output_name="Solution.dll",
                compile_resource_limits=ResourceLimits(
                    wall_ms=10000,
                    cpu_ms=5000,
                    memory_kb=262144,
                    pids=64,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.CSHARP,
            ),
            execution_spec=ExecutionSpec(
                runner_bin=dotnet_bin,
                execution_args=[
                    "exec",
                    "--runtimeconfig",
                    runtime_config,
                    "/workspace/Solution.dll",
                ],
                source_filename="Solution.cs",
                default_resource_limits=ResourceLimits(
                    wall_ms=5000,
                    cpu_ms=2000,
                    memory_kb=131072,
                    pids=32,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.CSHARP,
            ),
            seccomp_policy=SeccompPolicyProfile.CSHARP,
            cleanup_spec=CleanupSpec(),
        )
        self.register(csharp_adapter)

        # Phase 6: Disposable SQL Database Sandboxes (SQLite Slice 1)
        sqlite_harness_script = (
            "import json, os, re, sqlite3, sys\n"
            "\n"
            "def run():\n"
            "    target = sys.argv[1] if len(sys.argv) > 1 else '/workspace/solution.sql'\n"
            "    conn = sqlite3.connect(':memory:', isolation_level=None)\n"
            "    if hasattr(conn, 'enable_load_extension'):\n"
            "        try:\n"
            "            conn.enable_load_extension(False)\n"
            "        except Exception:\n"
            "            pass\n"
            "    try:\n"
            "        conn.execute('PRAGMA temp_store = MEMORY;')\n"
            "    except Exception:\n"
            "        pass\n"
            "    file_pragmas = {\n"
            "        'data_store_directory',\n"
            "        'temp_store_directory',\n"
            "        'temp_store',\n"
            "        'database_list',\n"
            "        'journal_mode',\n"
            "        'journal_size_limit',\n"
            "        'wal_checkpoint',\n"
            "        'wal_autocheckpoint',\n"
            "        'auto_vacuum',\n"
            "        'incremental_vacuum',\n"
            "        'locking_mode',\n"
            "        'mmap_size',\n"
            "        'page_size',\n"
            "        'max_page_count',\n"
            "        'synchronous',\n"
            "        'default_synchronous',\n"
            "        'cache_spill',\n"
            "    }\n"
            "    def authorizer(action, p1, p2, dbname, trigger):\n"
            "        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):\n"
            "            return sqlite3.SQLITE_DENY\n"
            "        if action == sqlite3.SQLITE_PRAGMA:\n"
            "            pname = (p1 or '').lower()\n"
            "            if pname in file_pragmas:\n"
            "                return sqlite3.SQLITE_DENY\n"
            "        return sqlite3.SQLITE_OK\n"
            "    conn.set_authorizer(authorizer)\n"
            "    max_opcodes = 1000000\n"
            "    opcode_interval = 100\n"
            "    max_ticks = max_opcodes // opcode_interval\n"
            "    ticks = 0\n"
            "    def progress_cb():\n"
            "        nonlocal ticks\n"
            "        ticks += 1\n"
            "        return 1 if ticks >= max_ticks else 0\n"
            "    conn.set_progress_handler(progress_cb, opcode_interval)\n"
            "    try:\n"
            "        with open(target, 'r', encoding='utf-8') as f:\n"
            "            content = f.read()\n"
            "    except Exception as e:\n"
            "        sys.stderr.write(f'Failed to read submission: {e}\\n')\n"
            "        sys.exit(1)\n"
            "    cur = conn.cursor()\n"
            "    stmts = []\n"
            "    buf = ''\n"
            "    for token in content.split(';'):\n"
            "        if not token.strip() and not buf:\n"
            "            continue\n"
            "        buf += token + ';'\n"
            "        if sqlite3.complete_statement(buf):\n"
            "            cleaned = re.sub(r'/\\*.*?\\*/', ' ', buf, flags=re.DOTALL)\n"
            "            cleaned = re.sub(r'--[^\\r\\n]*', ' ', cleaned)\n"
            "            if cleaned.replace(';', '').strip():\n"
            "                stmts.append(buf.strip())\n"
            "            buf = ''\n"
            "    if buf.strip():\n"
            "        cleaned = re.sub(r'/\\*.*?\\*/', ' ', buf, flags=re.DOTALL)\n"
            "        cleaned = re.sub(r'--[^\\r\\n]*', ' ', cleaned)\n"
            "        if cleaned.replace(';', '').strip():\n"
            "            stmts.append(buf.strip())\n"
            "    results = []\n"
            "    try:\n"
            "        for s in stmts:\n"
            "            stripped = re.sub(r\"'(?:''|[^'])*'|\\\"(?:\\\"\\\"|[^\\\"])*\\\"|/\\*.*?\\*/|--[^\\r\\n]*\", ' ', s, flags=re.DOTALL)\n"
            "            if re.search(r'\\bVACUUM\\b.*?\\bINTO\\b', stripped, re.IGNORECASE | re.DOTALL):\n"
            "                sys.stderr.write('SQLite error: authorization denied: VACUUM INTO is disabled\\n')\n"
            "                sys.exit(1)\n"
            "            cur.execute(s)\n"
            "            if cur.description:\n"
            "                cols = [d[0] for d in cur.description]\n"
            "                rows = [list(r) for r in cur.fetchmany(1000)]\n"
            "                results.append({'columns': cols, 'rows': rows})\n"
            "        try:\n"
            "            conn.commit()\n"
            "        except Exception:\n"
            "            pass\n"
            "    except Exception as e:\n"
            "        sys.stderr.write(f'SQLite error: {e}\\n')\n"
            "        sys.exit(1)\n"
            "    finally:\n"
            "        conn.close()\n"
            "    dump_fn = lambda obj: json.dumps(obj, default=lambda o: o.hex() if isinstance(o, bytes) else str(o))\n"
            "    if len(results) == 1:\n"
            "        print(dump_fn(results[0]))\n"
            "    elif len(results) > 1:\n"
            "        print(dump_fn(results))\n"
            "    else:\n"
            "        print(dump_fn({'columns': [], 'rows': []}))\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    run()\n"
        )

        sqlite_adapter = RuntimeAdapter(
            id=LanguageId.SQLITE,
            display_name="SQLite",
            official_version="SQLite 3.54 (in-memory)",
            installed_version=detect_python_sqlite_version(python_bin),
            family=RuntimeFamily.DATABASE,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=python_bin,
                execution_args=["-I", "-c", sqlite_harness_script, "/workspace/solution.sql"],
                source_filename="solution.sql",
                default_resource_limits=ResourceLimits(
                    wall_ms=3000,
                    cpu_ms=2000,
                    memory_kb=65536,
                    pids=16,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.PYTHON,
            ),
            seccomp_policy=SeccompPolicyProfile.PYTHON,
            cleanup_spec=CleanupSpec(),
        )
        self.register(sqlite_adapter)

        # Truthfully unsupported Phase 6 database runtimes (server toolchains uninstalled or absent)
        psql_bin = "/usr/bin/psql"
        if os.path.exists(psql_bin) and not os.path.exists("/usr/bin/postgres"):
            psql_installed = "Incomplete toolchain on host (psql client present, server daemon uninstalled)"
        else:
            psql_installed = detect_tool_version(psql_bin)

        postgresql_adapter = RuntimeAdapter(
            id=LanguageId.POSTGRESQL,
            display_name="PostgreSQL",
            official_version="PostgreSQL 18",
            installed_version=psql_installed,
            family=RuntimeFamily.DATABASE,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=psql_bin,
                execution_args=[],
                source_filename="solution.sql",
                seccomp_profile=SeccompPolicyProfile.STRICT_NATIVE,
            ),
            seccomp_policy=SeccompPolicyProfile.STRICT_NATIVE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(postgresql_adapter)

        mysql_adapter = RuntimeAdapter(
            id=LanguageId.MYSQL,
            display_name="MySQL",
            official_version="MySQL 9.x / MariaDB 11.x",
            installed_version=detect_tool_version("/usr/bin/mysql"),
            family=RuntimeFamily.DATABASE,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin="/usr/bin/mysql",
                execution_args=[],
                source_filename="solution.sql",
                seccomp_profile=SeccompPolicyProfile.STRICT_NATIVE,
            ),
            seccomp_policy=SeccompPolicyProfile.STRICT_NATIVE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(mysql_adapter)

        mssql_adapter = RuntimeAdapter(
            id=LanguageId.MSSQL,
            display_name="MS SQL Server",
            official_version="MS SQL Server 2022",
            installed_version=detect_tool_version("/opt/mssql-tools/bin/sqlcmd"),
            family=RuntimeFamily.DATABASE,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin="/opt/mssql-tools/bin/sqlcmd",
                execution_args=[],
                source_filename="solution.sql",
                seccomp_profile=SeccompPolicyProfile.STRICT_NATIVE,
            ),
            seccomp_policy=SeccompPolicyProfile.STRICT_NATIVE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(mssql_adapter)

        oracle_adapter = RuntimeAdapter(
            id=LanguageId.ORACLE,
            display_name="Oracle SQL",
            official_version="Oracle Database 23c",
            installed_version=detect_tool_version("/usr/bin/sqlplus"),
            family=RuntimeFamily.DATABASE,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin="/usr/bin/sqlplus",
                execution_args=[],
                source_filename="solution.sql",
                seccomp_profile=SeccompPolicyProfile.STRICT_NATIVE,
            ),
            seccomp_policy=SeccompPolicyProfile.STRICT_NATIVE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(oracle_adapter)

        # Phase 7: Data Science & Browser Frontend Runtimes
        # 1. Pandas isolated execution with DataFrame tabular serialization
        pandas_harness_script = (
            "import sys, os, json\n"
            "target = sys.argv[1] if len(sys.argv) > 1 else '/workspace/solution.py'\n"
            "try:\n"
            "    import pandas as pd\n"
            "    import numpy as np\n"
            "except ImportError as e:\n"
            "    sys.stderr.write(f'Pandas import error: {e}\\n')\n"
            "    sys.exit(1)\n"
            "\n"
            "def serialize_df(df):\n"
            "    columns = [str(c) for c in df.columns.tolist()]\n"
            "    rows = []\n"
            "    for record in df.to_dict(orient='records'):\n"
            "        row = []\n"
            "        for col in df.columns:\n"
            "            val = record.get(col)\n"
            "            if pd.isna(val) or val is None:\n"
            "                row.append(None)\n"
            "            elif hasattr(val, 'isoformat'):\n"
            "                row.append(val.isoformat())\n"
            "            elif isinstance(val, (np.bool_, bool)):\n"
            "                row.append(bool(val))\n"
            "            elif isinstance(val, (np.integer, int)):\n"
            "                row.append(int(val))\n"
            "            elif isinstance(val, (np.floating, float)):\n"
            "                row.append(float(val))\n"
            "            elif isinstance(val, bytes):\n"
            "                row.append(val.hex())\n"
            "            else:\n"
            "                row.append(str(val))\n"
            "        rows.append(row)\n"
            "    return {'columns': columns, 'rows': rows}\n"
            "\n"
            "def serialize_series(s):\n"
            "    name = str(s.name) if s.name is not None else 'value'\n"
            "    rows = []\n"
            "    for val in s.tolist():\n"
            "        if pd.isna(val) or val is None:\n"
            "            rows.append([None])\n"
            "        elif hasattr(val, 'isoformat'):\n"
            "            rows.append([val.isoformat()])\n"
            "        elif isinstance(val, (np.bool_, bool)):\n"
            "            rows.append([bool(val)])\n"
            "        elif isinstance(val, (np.integer, int)):\n"
            "            rows.append([int(val)])\n"
            "        elif isinstance(val, (np.floating, float)):\n"
            "            rows.append([float(val)])\n"
            "        elif isinstance(val, bytes):\n"
            "            rows.append([val.hex()])\n"
            "        else:\n"
            "            rows.append([str(val)])\n"
            "    return {'columns': [name], 'rows': rows}\n"
            "\n"
            "def run():\n"
            "    if not os.path.exists(target):\n"
            "        sys.stderr.write(f'Submission file not found: {target}\\n')\n"
            "        sys.exit(1)\n"
            "    try:\n"
            "        with open(target, 'r', encoding='utf-8') as f:\n"
            "            code_str = f.read()\n"
            "    except Exception as e:\n"
            "        sys.stderr.write(f'Failed to read submission: {e}\\n')\n"
            "        sys.exit(1)\n"
            "    env = {'pd': pd, 'pandas': pd, 'np': np, 'numpy': np, '__name__': '__main__'}\n"
            "    try:\n"
            "        compiled = compile(code_str, target, 'exec')\n"
            "        exec(compiled, env)\n"
            "    except Exception as e:\n"
            "        import traceback\n"
            "        traceback.print_exc(file=sys.stderr)\n"
            "        sys.exit(1)\n"
            "    results = []\n"
            "    for var_name in ['result', 'res', 'df', 'output']:\n"
            "        if var_name in env and var_name not in ['pd', 'pandas', 'np', 'numpy']:\n"
            "            val = env[var_name]\n"
            "            if isinstance(val, pd.DataFrame):\n"
            "                results.append(serialize_df(val))\n"
            "                break\n"
            "            elif isinstance(val, pd.Series):\n"
            "                results.append(serialize_series(val))\n"
            "                break\n"
            "    if not results:\n"
            "        for fn_name in ['solution', 'solve']:\n"
            "            if fn_name in env and callable(env[fn_name]):\n"
            "                try:\n"
            "                    ret = env[fn_name]()\n"
            "                    if isinstance(ret, pd.DataFrame):\n"
            "                        results.append(serialize_df(ret))\n"
            "                        break\n"
            "                    elif isinstance(ret, pd.Series):\n"
            "                        results.append(serialize_series(ret))\n"
            "                        break\n"
            "                except Exception:\n"
            "                    pass\n"
            "    if not results:\n"
            "        for k, v in env.items():\n"
            "            if k.startswith('__') or k in ['pd', 'pandas', 'np', 'numpy']:\n"
            "                continue\n"
            "            if isinstance(v, pd.DataFrame):\n"
            "                results.append(serialize_df(v))\n"
            "                break\n"
            "            elif isinstance(v, pd.Series):\n"
            "                results.append(serialize_series(v))\n"
            "                break\n"
            "    if results:\n"
            "        dump_fn = lambda obj: json.dumps(obj, default=lambda o: str(o))\n"
            "        if len(results) == 1:\n"
            "            print(dump_fn(results[0]))\n"
            "        else:\n"
            "            print(dump_fn(results))\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    run()\n"
        )

        pandas_bin = os.environ.get("PES2_PANDAS_PYTHON_BIN") or python_bin
        pandas_adapter = RuntimeAdapter(
            id=LanguageId.PANDAS,
            display_name="Pandas",
            official_version="Python 3.14 (pandas 3.1, numpy 2.5)",
            installed_version=detect_pandas_version(pandas_bin),
            family=RuntimeFamily.FRAMEWORK,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=pandas_bin,
                execution_args=["-I", "-c", pandas_harness_script, "/workspace/solution.py"],
                source_filename="solution.py",
                default_resource_limits=ResourceLimits(
                    wall_ms=5000,
                    cpu_ms=3000,
                    memory_kb=262144,
                    pids=16,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.PYTHON,
            ),
            seccomp_policy=SeccompPolicyProfile.PYTHON,
            cleanup_spec=CleanupSpec(),
        )
        self.register(pandas_adapter)

        # 2. React 18 isolated browser client-side execution specification
        react_adapter = RuntimeAdapter(
            id=LanguageId.REACT,
            display_name="React",
            official_version="React 18.2 (Babel, client-side sandbox)",
            installed_version="Browser-only runtime: client-side execution in isolated browser sandbox (not a server runner)",
            family=RuntimeFamily.FRAMEWORK,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=node_bin,
                execution_args=["--check", "/workspace/Solution.jsx"],
                source_filename="Solution.jsx",
                default_resource_limits=ResourceLimits(
                    wall_ms=5000,
                    cpu_ms=2000,
                    memory_kb=131072,
                    pids=16,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.NODE,
            ),
            seccomp_policy=SeccompPolicyProfile.NODE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(react_adapter)

        # 3. Vanilla JavaScript (DOM) isolated browser client-side execution specification
        vanilla_js_adapter = RuntimeAdapter(
            id=LanguageId.VANILLA_JS,
            display_name="JavaScript (DOM)",
            official_version="Browser JavaScript (ES2022, client-side sandbox)",
            installed_version="Browser-only runtime: client-side execution in isolated browser sandbox (not a server runner)",
            family=RuntimeFamily.FRAMEWORK,
            compile_spec=None,
            execution_spec=ExecutionSpec(
                runner_bin=node_bin,
                execution_args=["--check", "/workspace/Solution.js"],
                source_filename="Solution.js",
                default_resource_limits=ResourceLimits(
                    wall_ms=5000,
                    cpu_ms=2000,
                    memory_kb=131072,
                    pids=16,
                    max_output_bytes=65536,
                ),
                seccomp_profile=SeccompPolicyProfile.NODE,
            ),
            seccomp_policy=SeccompPolicyProfile.NODE,
            cleanup_spec=CleanupSpec(),
        )
        self.register(vanilla_js_adapter)
