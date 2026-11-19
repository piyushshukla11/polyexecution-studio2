# Architecture & Technical Design — PolyExecution Studio 2

## 1. System Mission & Scope
PolyExecution Studio 2 (PES 2) is a multi-language remote execution studio engineered to safely evaluate and execute code across the 27 programming environments supported by LeetCode.

The system is designed around two non-negotiable principles:
1. **Zero-Trust Host Execution:** Untrusted user code and compilation steps execute strictly inside kernel-enforced sandboxes (cgroups v2, Linux namespaces, seccomp-bpf, rlimits).
2. **Explicit Runtime Adapters:** Heterogeneous language runtimes, SQL database engines, data science libraries, and web frontend scripts are managed via typed runtime adapters rather than arbitrary shell invocations or monolithic container images.

---

## 2. The 27-Environment Inventory & Execution Families

As of **2026-11-17**, LeetCode supports 27 distinct environments categorized into five execution families:

| Family | Environments | Execution Model | Security & Isolation Challenges |
| :--- | :--- | :--- | :--- |
| **Native Compiled** | C (`gcc 14`), C++ (`clang 19`), Rust (`rustc 1.99.1`), Go (`go 1.27`), Swift (`swift 6.4.1`) | 2-Stage: Sandbox compile -> Bounded binary handoff -> Sandbox execution | Out-of-memory during compilation, ASan memory overhead, binary bloat, static vs dynamic linkage |
| **JVM** | Java (`OpenJDK 25`), Kotlin (`kotlinc 2.4.21`), Scala (`scala 3.9.0`) | 2-Stage: Sandbox compile -> Bounded bytecode JAR handoff -> JVM execution | JIT compilation threads, VM memory footprint (>64 MiB base RSS), JVM signal interception (SIGSEGV/SIGQUIT) |
| **Interpreted Scripting** | Python 3 (`3.14`), Python 2 (`2.7.18`), JavaScript (`Node.js 26.12.1`), TypeScript (`tsc 7.0.2`), Ruby (`ruby 4.0`), PHP (`php 8.5`), Bash (`bash 5.2.21`), Dart (`dart 3.13`), Racket (`racket cs 9.3`), Erlang (`OTP 29`), Elixir (`elixir 1.20`), C# (`.NET 10`) | Direct execution or script pre-transpilation | V8 pointer compression cage (requires ~4-5 GiB virtual address space), subshell spawns (Bash), multi-threaded VM daemons |
| **Database Engines (SQL)** | MySQL (`9.7`), PostgreSQL (`18`), MS SQL Server (`2019`), Oracle SQL (`11.2`) | Ephemeral disposable schema per run -> Query evaluation -> Result table serialization | Persistent state pollution, concurrent schema collisions, network loopback security, database daemon startup latency |
| **Frameworks & UI** | Pandas (`Python 3.14 + pandas 3.1.0`), React (`React 18.2.0`), Vanilla JS (DOM) | Headless evaluation (Pandas) / Isolated browser sandbox iframe (React/DOM) | Heavy data allocations (NumPy/Pandas SIMD), DOM XSS in browser evaluation, exfiltration via DOM APIs |

---

## 3. Runtime Adapter Registry Contract

Rather than hardcoding language commands into monolithic handlers, each runtime implements a typed `RuntimeAdapter` registered in the central `RuntimeRegistry`:

```
+---------------------------------------------------------------------------------+
|                                 RuntimeAdapter                                  |
+---------------------------------------------------------------------------------+
| + id: LanguageId                                                                |
| + displayName: String                                                           |
| + officialVersion: String                                                       |
| + installedVersion: String                                                      |
| + family: RuntimeFamily (NATIVE, JVM, INTERPRETED, DATABASE, FRAMEWORK)        |
| + compileSpec: Optional<CompileSpec>                                            |
|   - compilerBin: Path                                                           |
|   - compileArgs: List<String>                                                   |
|   - sourceFileName: String                                                      |
|   - artifactOutputName: String                                                  |
|   - compileResourceLimits: ResourceLimits                                       |
| + executionSpec: ExecutionSpec                                                  |
|   - runnerBin: Path                                                             |
|   - executionArgs: List<String>                                                 |
|   - defaultResourceLimits: ResourceLimits (cpuMs, wallMs, memoryKb, pids, fsize)|
| + seccompPolicy: SeccompPolicyProfile (STRICT_NATIVE, JVM_PERMISSIVE, V8_NODE)  |
| + cleanupSpec: CleanupSpec (ephemeral workspace dirs, temp sqlite/postgres db)  |
+---------------------------------------------------------------------------------+
```

---

## 4. Multi-Layer Security Architecture

1. **cgroups v2 Hierarchy (`/sys/fs/cgroup/pes2-...`)**:
   - `cpu.max`: Strict quota ceiling preventing CPU starvation.
   - `memory.max` & `memory.high`: Hard ceiling triggering kernel OOM killer before host instability occurs.
   - `pids.max`: Hard task ceiling (typically 32 to 64) containing fork bombs.
2. **Linux User & Mount Namespaces (`CLONE_NEWUSER`, `CLONE_NEWNS`, `CLONE_NEWPID`, `CLONE_NEWNET`, `CLONE_NEWIPC`)**:
   - `CLONE_NEWUSER`: Single unprivileged mapping (`0 -> host_uid`, `0 -> host_gid`). Namespace UID 0 provides initial namespace-local capabilities for `mount` and `chroot` setup without requiring host root privileges. Before target execution (`execve`), all capabilities are permanently cleared (`SYS_capset`), `PR_SET_DUMPABLE=0` is enforced, and `PR_SET_NO_NEW_PRIVS=1` is locked by seccomp. Target code executes as namespace UID 0 with zero capabilities, mapping strictly to the invoking unprivileged host user.
   - `CLONE_NEWNET`: Loopback only (`lo` down or unconfigured); outbound network sockets return `EPERM` or `ENETUNREACH`.
   - `CLONE_NEWNS`: Private mount namespace with `pivot_root` or `chroot` into a read-only rootfs; `/workspace` is mounted `noexec` or `nodev` depending on stage.
   - `CLONE_NEWPID`: Process tree virtualization; target process runs as PID 1 inside the sandbox.
3. **Seccomp-BPF Syscall Filter**:
   - Default deny architecture.
   - System calls allowing file creation outside `/workspace`, hardware configuration (`reboot`, `syslog`), kernel keyrings, or network sockets are blocked at the kernel syscall boundary.
4. **Disposable Ephemeral SQL Database Architecture**:
   - Each SQL run operates against a fresh, disposable in-memory or transient database instance/schema provisioned strictly per `requestId`.
   - Databases are destroyed and unlinked immediately upon query execution completion, timeout, or cancellation; no shared persistent state is retained across requests.
   - Security Boundary: Enforced at the engine and kernel level rather than through fragile keyword denylists. The execution role operates with minimal read-only or strictly bounded schema privileges (`NO SUPERUSER`, `NOCREATEDB`), with engine-level file import/export primitives completely disabled in daemon configurations (`COPY PROGRAM`, `LOAD DATA INFILE`, `pg_read_file`). Network access is denied via `CLONE_NEWNET`. Textual parser denylists are never relied upon as a primary defense boundary.

---

## 5. Worker Daemon Architecture & Durable Decisions

The remote execution worker daemon mediates between external orchestration/API clients and local kernel-sandboxed runners.

### Architecture & Design
- **Asynchronous gRPC Service (`grpc.aio`):** Implemented in Python using `grpc.aio` to seamlessly integrate with the typed `RuntimeRegistry` and `RuntimeAdapter` models without foreign-function interface (FFI) overhead or IPC complexity.
- **Streaming Telemetry (`ExecuteStream`):** Delivers real-time lifecycle milestone events (`ACCEPTED`, `COMPILING`, `RUNNING`, etc.) as execution progresses; stdout, stderr, and Linux kernel telemetry (`wallMs`, `cpuMs`, `peakMemoryKb`, `exitCode`, `signal`) are delivered in the final completed execution result.
- **Off-Loop Asynchronous Dispatch:** To prevent long-running compilation or user code execution from blocking the asynchronous event loop, runner subprocesses are dispatched via `asyncio.to_thread` / worker thread pools.
- **Strict Concurrency & Memory Admission Control:** Managed via an active execution tracking counter (`max_capacity`) and dynamic host memory capacity tracking (`max_memory_kb`).
  - **Operator-Configured Execution Budget with Host Headroom:** The daemon enforces an operator-configured execution memory budget (via `--memory-mb`). When omitted, it safely derives the budget from effective memory (the minimum of host physical RAM and any active cgroup quota via `memory.max` or `memory.limit_in_bytes`), reserving at least 25% (and a minimum of 1024 MiB on large hosts or 64 MiB for daemon RSS on smaller hosts) headroom. The derived budget strictly respects headroom without artificial floors, and requires an explicit `--memory-mb` budget if effective memory cannot satisfy the headroom requirement.
  - **Cancellation Cleanup Race Prevention:** When an RPC is cancelled, the handler retains its memory reservation and worker slot until the underlying execution future completes. While the executor terminates `pes2-runner` (`SIGTERM`) and tears down the cgroup hierarchy, the memory remains allocated in daemon accounting. Competing requests arriving during this cleanup window that would exceed the budget are rejected with `grpc.StatusCode.RESOURCE_EXHAUSTED` rather than exceeding physical capacity.
- **Loopback-Only Interface Security:** The daemon binds exclusively to loopback addresses (`127.0.0.1` / `[::1]`) by default, preventing unauthenticated host exposure or external network penetration.
- **Deterministic Cancellation & Cleanup:** Client disconnects or gRPC call cancellations signal the execution thread via cancellation tokens, sending `SIGTERM` to the supervisor runner process (`pes2-runner`). The supervisor terminates sandboxed child processes, cleanly tears down cgroups v2 hierarchies, and deletes ephemeral workspace directories.

### JVM Bytecode Handoff & Memory Admission Architecture
1. **Directory Classpath Handoff:**
   - Both compilation and execution execute against the identical isolated ephemeral `/workspace` mount in the sandbox.
   - `javac -d /workspace` emits all primary, nested, inner, and lambda classes directly into `/workspace`.
   - Direct directory classpath execution (`-cp /workspace Solution`) is zero-copy, eliminates the 500-1500 ms latency of a secondary `jar` packaging process, avoids entropy/device dependencies, and natively loads all nested classes and lambda call sites without recompilation.
2. **Dynamic JVM Memory Baseline Admission:**
   - HotSpot JVM requires approximately 128 MiB baseline reserve for native runtime structures (metaspace, thread stacks, JIT compiler threads, GC tables).
   - Dynamic memory baseline admission ensures cgroup memory limits are accommodated to a minimum of 192 MiB to prevent immediate VM aborts during initialization.
   - The heap ceiling (`-Xmx`) is dynamically calculated and injected based on the admitted cgroup memory quota (`cgroup_memory_kb - 128 MiB`), ensuring heap allocations never breach kernel cgroup limits.

### Decision Rationale: Python `grpc.aio` Daemon vs Kotlin/Rust
During Phase 3 architectural refinement, the worker daemon was implemented using Python `grpc.aio` rather than Kotlin or Rust:
1. **Unified Memory Footprint:** A Kotlin daemon requires a persistent JVM runtime on the host consuming 150-300 MiB baseline RSS solely for RPC dispatch, competing with sandboxed compilations for host RAM.
2. **Direct Engine Synergy:** The typed adapter layer (`engine/registry.py`) is natively Python. A Python daemon shares typed data structures directly without serialization boundaries or foreign-runtime subprocess handoffs.
3. **Deterministic Subprocess and Asynchronous Cancellation:** Python's `asyncio` cancellation primitives pair directly with native OS subprocess signal forwarding and cgroup cleanup handlers, ensuring zero orphaned processes or leaked workspaces.

---

## 6. Architectural Non-Goals & Honest Limitations
- **No Emulation of LeetCode Services:** PES 2 executes code; it does not clone LeetCode's frontend UI, question text, subscription models, or proprietary test cases.
- **Single-Host vs Multi-Host Clustering:** The initial architecture targets a clean, vertically sliced single-host engine with transparent resource admission before distributed clustering is considered.
- **Docker Socket Independence:** Core sandbox capabilities rely directly on Linux kernel primitives (cgroups v2, namespaces, seccomp) to avoid requiring privileged Docker-in-Docker daemons.
