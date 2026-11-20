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

### Interpreted & Managed Runtimes Architecture (Phase 5)
1. **Single-Stage Direct Interpreted Execution:**
   - Interpreted languages like Ruby execute directly via `ExecutionSpec` (`/usr/bin/ruby /workspace/Solution.rb`) without a compilation stage (`compile_spec=None`).
   - Milestones emitted reflect actual execution phases truthfully: `ACCEPTED` -> `RUNNING` -> `COMPLETED` / `RUNTIME_ERROR` without synthetic `COMPILING` milestones.
2. **Sandbox Rootfs Reachability & Honest Support Status:**
   - The native sandbox rootfs constructs an unprivileged user namespace bind-mounting `/usr`, `/bin`, `/lib`, and `/lib64`, completely excluding user home paths.
   - Runtimes installed solely via user-space managers (e.g. mise shims in `~/.local/share/mise` for Node.js and Kotlin) are unavailable inside the sandbox rootfs.
   - Toolchains present on the host without required runtimes or SDKs (such as `/usr/bin/dotnet` without an installed .NET SDK) are classified as incomplete toolchains.
   - The registry detects and reports these runtimes truthfully as unavailable to sandbox or incomplete, ensuring they remain unsupported and fail closed (`SANDBOX_ERROR`) rather than attempting execution.
3. **Ruby Runtime Seccomp Profile (`SECCOMP_PROFILE_RUBY`):**
   - Ruby 3 requires narrow startup syscalls: `sched_getaffinity` for core count detection, `eventfd2` and `epoll_create1`/polling for Fiber scheduling and I/O coordination.
   - Restricts `prctl` operations strictly to `PR_SET_NAME` (thread naming), `PR_SET_THP_DISABLE` (transparent huge page optimization), and `PR_SET_VMA` with `PR_SET_VMA_ANON_NAME` (GC page naming). Any attempt to invoke other `prctl` options (such as `PR_SET_DUMPABLE`) is default-denied and terminates the process with `SIGSYS`.
   - Strict network denial (`socket`, `connect`, etc.) and namespace isolation (`clone` namespace flags, `unshare`) remain fully enforced.
4. **Node.js Runtime Seccomp Profile (`SECCOMP_PROFILE_NODE`):**
   - V8 & libuv initialization requires `capget` (read-only process capability query), `sched_getaffinity` (CPU core count awareness for libuv threadpool), `eventfd2` (inter-thread event signaling), and `epoll_create1`/polling for asynchronous event loops.
   - Returns `ENOSYS` on `io_uring_setup` so libuv's kernel probe cleanly falls back to epoll without crashing the process. Related io_uring syscalls (`io_uring_enter`, `io_uring_register`) remain strictly denied (`SIGSYS`).
   - Restricts `prctl` operations strictly to `PR_SET_NAME` (thread naming) and `PR_SET_VMA` with `PR_SET_VMA_ANON_NAME` (V8 GC page naming). All other `prctl` options (such as `PR_SET_DUMPABLE`) and `capset` are default-denied and terminate the process with `SIGSYS`.
   - Bounded under a measured 128 MiB cgroup memory limit, properly distinguishing resident anonymous memory (~50 MiB baseline) from V8's ~1.28 GiB virtual address space pointer compression reservations.
   - Strict network denial (`socket`, `connect`, etc.) and namespace isolation (`clone` namespace flags, `unshare`) remain fully enforced.
5. **PHP Runtime Seccomp Profile (`SECCOMP_PROFILE_PHP`):**
   - Zend Engine initialization requires `sched_getaffinity` for core count detection.
   - Restricts `prctl` operations strictly to `PR_SET_NAME` (thread naming) and `PR_SET_VMA` with `PR_SET_VMA_ANON_NAME` (Zend memory manager `zend_alloc` anonymous page naming). Any attempt to invoke other `prctl` options (e.g. `PR_SET_DUMPABLE`) is default-denied and terminates the process with `SIGSYS`.
   - Default sandbox `clone` handling (no namespace flags) is preserved; `clone3` returns `ENOSYS`.
   - Memory bounded under a verified 64 MiB cgroup memory limit (`65536 KiB`), safely bounding the ~24.8 MiB baseline RSS without false OOM kills.
   - Strict network denial (`socket`, `connect`, etc.), scheduler denial (`sched_setparam`, `sched_setscheduler`, `setpriority`, `nice`), and namespace isolation (`clone` namespace flags, `unshare`) remain fully enforced.
6. **TypeScript 2-Stage Transpilation & Node Execution:**
   - 2-stage execution: compiles `/workspace/Solution.ts` to `/workspace/Solution.js` via `/usr/bin/env NODE_DISABLE_COMPILE_CACHE=1 /usr/bin/tsc --target ES2022 --module CommonJS /workspace/Solution.ts`, followed by execution via `/usr/bin/node /workspace/Solution.js`.
   - Reuses existing `SECCOMP_PROFILE_NODE` without profile widening or Tier A modifications.
   - Passing `NODE_DISABLE_COMPILE_CACHE=1` avoids Node.js compile cache directory creation (`mkdir` on `/tmp/node-compile-cache`), enabling clean compilation under default-deny seccomp policies while preserving strict directory creation protections.
   - Bounded under a measured 256 MiB cgroup limit for compilation (~214 MiB peak) and 128 MiB for execution.
7. **Dart Runtime Seccomp Profile (`SECCOMP_PROFILE_DART`) & Namespace-Isolated Procfs:**
   - Single-stage direct execution via `/usr/bin/dart --suppress-analytics run /workspace/Solution.dart`.
   - Dedicated `SECCOMP_PROFILE_DART` (profile 8) permitting narrow initialization syscalls: `sched_getaffinity` (CPU topology awareness for isolate thread pool), `eventfd2` (inter-thread event messaging), `epoll_create1`/`epoll_ctl`/`epoll_wait`/`epoll_pwait`/`epoll_pwait2` (event loop coordination), and `timerfd_create`/`timerfd_settime`/`timerfd_gettime` (timer threads).
   - Restricts `prctl` operations strictly to `PR_SET_NAME` (thread naming), `PR_GET_NAME` (thread name inspection), and `PR_SET_VMA` with `PR_SET_VMA_ANON_NAME` (Dart GC heap and isolate page naming). All other `prctl` operations (such as `PR_SET_DUMPABLE`) are denied.
   - Preserves default sandbox `clone` handling (no namespace flags) and returns `ENOSYS` on `clone3`. Strict default-deny remains enforced for all network calls (`socket`, `connect`, etc.), capabilities (`capset`), and scheduler modifications (`sched_setparam`, `sched_setscheduler`, `setpriority`, `nice`).
   - Narrowed SDK mount: bind-mounts host `/opt/dart-sdk` into `/opt/dart-sdk` read-only (`MS_RDONLY | MS_NOSUID | MS_NODEV`), with targets restricted under `/opt/dart-sdk/`.
   - Namespace-isolated procfs mount: glibc `pthread_getattr_np(pthread_self(), &attr)` in `os_thread.cc` reads `/proc/self/maps` to discover stack bounds, and canonicalizes `/proc/self/exe`. To support this without compromising host isolation, procfs is mounted at `<rootfs>/proc` strictly inside the child's `CLONE_NEWPID` namespace with `MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC` and `hidepid=2,subset=pid`. This exposes only the child's PID 1 entry, completely hides host and external task entries, prevents write operations (`EROFS`), and leaves no residual mounts or processes.
   - Bounded under an empirically measured 192 MiB cgroup limit (`196608 KiB`), accommodating the Dart VM baseline RSS of ~118–120 MiB while leaving ~73 MiB headroom for user code execution.
8. **Racket Runtime Seccomp Profile (`SECCOMP_PROFILE_RACKET`) & Read-Only /etc/racket Mount:**
   - Single-stage direct execution via `/usr/bin/racket /workspace/Solution.rkt`.
   - Dedicated `SECCOMP_PROFILE_RACKET` (profile 9) permitting narrow initialization syscalls: `sched_getaffinity` (core awareness for Chez Scheme runtime), `epoll_create`/`epoll_create1`/`epoll_ctl`/`epoll_wait`/`epoll_pwait`/`epoll_pwait2` (event loop coordination), and `poll` (descriptor and timer multiplexing).
   - Restricts `prctl` strictly to `PR_SET_NAME` (thread naming) and `PR_GET_NAME` (thread name inspection). Any other `prctl` options (such as `PR_SET_DUMPABLE`) are denied and terminate the process with `SIGSYS`.
   - Preserves default sandbox `clone` handling (no namespace flags) and returns `ENOSYS` on `clone3`. Strict default-deny remains enforced for all network calls (`socket`, `connect`, etc.), capabilities (`capset`), and scheduler modifications (`sched_setparam`, `sched_setscheduler`, `setpriority`, `nice`).
   - Narrowed configuration mount: host `/etc/racket` bind-mounted read-only (`MS_RDONLY | MS_NOSUID | MS_NODEV`), exposing `config.rktd` while keeping the sandbox isolated from the rest of `/etc`.
   - Bounded under an empirically measured 192 MiB cgroup limit (`196608 KiB`), accommodating Chez Scheme's ~135–140 MiB baseline heap while leaving ~55 MiB headroom for user code execution.
9. **Erlang Runtime Seccomp Profile (`SECCOMP_PROFILE_ERLANG`) & Zero-Mount Isolation:**
   - Single-stage direct execution via `/usr/bin/erl` with `+S 2:2` (limiting BEAM CPU schedulers to 2) and `+A 2` (limiting the asynchronous thread pool to 2), `-noshell`, and an in-memory evaluation harness compiling and executing `/workspace/solution.erl`.
   - Dedicated `SECCOMP_PROFILE_ERLANG` (profile 10) aligned with BEAM emulator and launcher requirements: shell launcher operations (`chdir`, `getpgrp`, `getresuid`, `getresgid`, `wait4`), BEAM SMP CPU topology discovery (`sched_getaffinity`), async I/O driver event loop coordination (`epoll_create`, `epoll_create1`, `epoll_ctl`, `epoll_wait`, `epoll_pwait`, `epoll_pwait2`), file descriptor polling (`poll`, `ppoll`), kernel timerfd integration (`timerfd_create`, `timerfd_settime`, `timerfd_gettime`), JIT compilation buffer management (`memfd_create`, `ftruncate`), process group separation and FD hygiene in `erl_child_setup` (`setsid`, `pselect6`, `close_range`), and stream messaging across anonymous domain socketpairs (`sendmsg`, `recvmsg`, `getsockopt`, `setsockopt`).
   - Domain isolation: restricts `socketpair` strictly to `AF_UNIX` (all other domains terminate with `SIGSYS`). Returns `EPERM` for `socket()` allowing BEAM's internal `inet_db` resolver initialization to cleanly fall back to offline mode without creating descriptors or crashing, while maintaining strict default-deny for active network operations (`connect`, `bind`, etc. killed by `SIGSYS`).
   - Identity containment: restricts `setfsuid` and `setfsgid` strictly to ID 0 (the sandbox's sole mapped UID/GID); attempts to set non-zero IDs terminate with `SIGSYS`.
   - Restricts `prctl` strictly to `PR_SET_NAME` (thread naming) and `PR_GET_NAME`. Any other `prctl` options (such as `PR_SET_DUMPABLE`) are denied and terminate the process with `SIGSYS`.
   - Preserves default sandbox `clone` handling (no namespace flags) and returns `ENOSYS` on `clone3`.
   - Zero additional mounts required: all Erlang runtime executables and OTP libraries reside entirely within `/usr/bin` and `/usr/lib/erlang`, which are already bind-mounted read-only into `/usr` in the rootfs.
   - Bounded under an empirically measured 192 MiB cgroup limit (`196608 KiB`), accommodating the BEAM VM baseline RSS of ~56.8 MiB and heavy 1,000,000-integer workloads peaking at ~118.8 MiB with comfortable headroom, paired with a 64-PID limit.
10. **Elixir Runtime Seccomp Profile (`SECCOMP_PROFILE_ELIXIR`) & Zero-Mount Isolation:**
   - Single-stage direct execution via `/usr/bin/elixir` with `--erl "+S 2:2 +A 2"` (limiting BEAM schedulers to 2 and async thread pool to 2) and an evaluation harness compiling and executing `/workspace/solution.exs`.
   - Dedicated `SECCOMP_PROFILE_ELIXIR` (profile 11) permitting narrow initialization syscalls: shell launcher operations (`chdir`, `getpgrp`, `getresuid`, `getresgid`, `wait4`), BEAM SMP CPU topology discovery (`sched_getaffinity`), async I/O driver event loop coordination (`epoll_create`, `epoll_create1`, `epoll_ctl`, `epoll_wait`, `epoll_pwait`, `epoll_pwait2`), file descriptor polling (`poll`, `ppoll`), kernel timerfd integration (`timerfd_create`, `timerfd_settime`, `timerfd_gettime`), JIT compilation buffer management (`memfd_create`, `ftruncate`), process group separation and FD hygiene in `erl_child_setup` (`setsid`, `pselect6`, `close_range`), and stream messaging across anonymous domain socketpairs (`sendmsg`, `recvmsg`, `getsockopt`, `setsockopt`).
   - Domain isolation: restricts `socketpair` strictly to `AF_UNIX` (all other domains terminate with `SIGSYS`). Returns `EPERM` for `socket()` allowing BEAM's internal `inet_db` resolver initialization to cleanly fall back to offline mode without creating descriptors or crashing, while maintaining strict default-deny for active network operations (`connect`, `bind`, etc. killed by `SIGSYS`).
   - Identity containment: restricts `setfsuid` and `setfsgid` strictly to ID 0 (the sandbox's sole mapped UID/GID); attempts to set non-zero IDs terminate with `SIGSYS`.
   - Restricts `prctl` strictly to `PR_SET_NAME` (thread naming) and `PR_GET_NAME`. Any other `prctl` options (such as `PR_SET_DUMPABLE`) are denied and terminate the process with `SIGSYS`.
   - Preserves default sandbox `clone` handling (no namespace flags) and returns `ENOSYS` on `clone3`.
   - Zero additional mounts required: all Elixir runtime binaries and compiled libraries reside entirely within `/usr/bin` and `/usr/lib/elixir`, which are already bind-mounted read-only into `/usr` in the rootfs.
   - Bounded under an empirically measured 192 MiB cgroup limit (`196608 KiB`), accommodating the BEAM VM baseline RSS of ~63.1 MiB and heavy 1,000,000-integer workloads peaking at ~153.8 MiB with comfortable headroom, paired with a 64-PID limit.

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
