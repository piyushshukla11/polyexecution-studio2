"""Typed models and contracts for PES 2 Runtime Adapter Engine."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


class LanguageId(str, Enum):
    C = "c"
    CPP = "cpp"
    PYTHON3 = "python3"
    PYTHON2 = "python2"
    JAVA = "java"
    KOTLIN = "kotlin"
    SCALA = "scala"
    RUST = "rust"
    GO = "go"
    SWIFT = "swift"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    RUBY = "ruby"
    PHP = "php"
    BASH = "bash"
    CSHARP = "csharp"
    DART = "dart"
    RACKET = "racket"
    ERLANG = "erlang"
    ELIXIR = "elixir"
    MYSQL = "mysql"
    POSTGRESQL = "postgresql"
    MSSQL = "mssql"
    ORACLE = "oracle"
    PANDAS = "pandas"
    REACT = "react"
    VANILLA_JS = "vanilla_js"


class RuntimeFamily(str, Enum):
    NATIVE = "NATIVE"
    JVM = "JVM"
    INTERPRETED = "INTERPRETED"
    DATABASE = "DATABASE"
    FRAMEWORK = "FRAMEWORK"


class Milestone(str, Enum):
    ACCEPTED = "ACCEPTED"
    COMPILING = "COMPILING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPILE_ERROR = "COMPILE_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    TIMEOUT = "TIMEOUT"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    MEMORY_LIMIT = "MEMORY_LIMIT"
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"
    SANDBOX_ERROR = "SANDBOX_ERROR"
    CANCELLED = "CANCELLED"


class SeccompPolicyProfile(str, Enum):
    STRICT_NATIVE = "strict"
    COMPILER = "compiler"
    PYTHON = "python"
    BASH = "bash"
    JVM = "jvm"
    JVM_PERMISSIVE = "jvm_permissive"
    V8_NODE = "node"
    NODE = "node"
    RUBY = "ruby"
    PHP = "php"


MAX_SOURCE_BYTES: int = 65536
MAX_RUNNER_OUTPUT_BYTES: int = 65536
RUNNER_MIN_TIMEOUT_MS: int = 1
RUNNER_MAX_TIMEOUT_MS: int = 60000
RUNNER_MIN_MEMORY_KB: int = 16384
RUNNER_MAX_MEMORY_KB: int = 1048576
RUNNER_MIN_PIDS: int = 1
RUNNER_MAX_PIDS: int = 128


@dataclass(frozen=True)
class ResourceLimits:
    cpu_ms: int = 1000
    wall_ms: int = 2000
    memory_kb: int = 65536
    pids: int = 32
    max_output_bytes: int = 65536


@dataclass(frozen=True)
class CompileSpec:
    compiler_bin: str
    compile_args: List[str]
    source_filename: str
    artifact_output_name: str
    compile_resource_limits: ResourceLimits = field(
        default_factory=lambda: ResourceLimits(
            cpu_ms=5000,
            wall_ms=10000,
            memory_kb=262144,
            pids=32,
            max_output_bytes=65536,
        )
    )
    seccomp_profile: SeccompPolicyProfile = SeccompPolicyProfile.COMPILER


@dataclass(frozen=True)
class ExecutionSpec:
    runner_bin: str
    execution_args: List[str] = field(default_factory=list)
    source_filename: Optional[str] = None
    default_resource_limits: ResourceLimits = field(default_factory=ResourceLimits)
    seccomp_profile: SeccompPolicyProfile = SeccompPolicyProfile.STRICT_NATIVE


@dataclass(frozen=True)
class CleanupSpec:
    ephemeral_workspace: bool = True
    remove_source: bool = True
    remove_artifact: bool = True


@dataclass(frozen=True)
class RuntimeAdapter:
    id: LanguageId
    display_name: str
    official_version: str
    installed_version: str
    family: RuntimeFamily
    compile_spec: Optional[CompileSpec]
    execution_spec: ExecutionSpec
    seccomp_policy: SeccompPolicyProfile = SeccompPolicyProfile.STRICT_NATIVE
    cleanup_spec: CleanupSpec = field(default_factory=CleanupSpec)

    @property
    def is_installed(self) -> bool:
        if not self.installed_version:
            return False
        unsupported_markers = ("Not installed", "Unavailable", "Incomplete")
        return not any(self.installed_version.startswith(m) for m in unsupported_markers)

    def resolve_execution_limits(
        self, custom_limits: Optional[ResourceLimits] = None
    ) -> Tuple[List[str], ResourceLimits]:
        """Resolves target execution arguments and resource limits.
        For JVM runtimes, implements dynamic memory baseline admission:
        accommodates ~128 MiB metaspace/native reserve (minimum 192 MiB cgroup limit)
        and dynamically computes the JVM heap budget (-Xmx).
        """
        limits = custom_limits or self.execution_spec.default_resource_limits
        args = list(self.execution_spec.execution_args)
        if self.family == RuntimeFamily.JVM:
            min_cgroup_kb = 196608
            metaspace_reserve_kb = 131072
            effective_cgroup_kb = max(limits.memory_kb, min_cgroup_kb)
            heap_mb = max(32, (effective_cgroup_kb - metaspace_reserve_kb) // 1024)
            filtered_args = [a for a in args if not a.startswith("-Xmx") and not a.startswith("-Xms")]
            args = [f"-Xmx{heap_mb}m", "-Xms32m"] + filtered_args
            limits = ResourceLimits(
                cpu_ms=limits.cpu_ms,
                wall_ms=limits.wall_ms,
                memory_kb=effective_cgroup_kb,
                pids=limits.pids,
                max_output_bytes=limits.max_output_bytes,
            )
        return args, limits


@dataclass(frozen=True)
class ExecutionRequest:
    request_id: str
    language_id: LanguageId
    source_code: str
    stdin: str = ""
    custom_limits: Optional[ResourceLimits] = None


@dataclass(frozen=True)
class MilestoneEvent:
    milestone: Milestone
    timestamp_ms: int
    details: Optional[str] = None


@dataclass(frozen=True)
class RunnerResult:
    status: str
    exit_code: int
    signal: int
    wall_ms: int
    cpu_ms: int
    peak_memory_kb: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ExecutionResult:
    request_id: str
    language_id: LanguageId
    final_status: Milestone
    milestones: List[MilestoneEvent]
    exit_code: int
    signal: int
    wall_ms: int
    cpu_ms: int
    peak_memory_kb: int
    stdout: str
    stderr: str
    compile_output: Optional[str] = None
