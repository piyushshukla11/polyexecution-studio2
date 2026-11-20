# Build Plan & Implementation Roadmap — PolyExecution Studio 2

## Phase Structure & Phased Implementation Strategy

PolyExecution Studio 2 is built in staged, verifiable phases. No phase is considered complete without passing automated test evidence across unit, integration, and live sandbox execution paths.

---

### Phase 0: Foundations, Governance & Toolchain Audit (Current)
- Establish repository structure, `.gitignore`, and Git hooks.
- Create `AI_RULES.md` and append-only `PROGRESS.md`.
- Document official LeetCode 27-environment inventory and perform host system toolchain audit.
- Define typed `RuntimeAdapter` and `RuntimeRegistry` architectural contracts.

### Phase 1: Core Low-Level Sandbox Engine (Native C)
- Implement `pes2-runner` in C with zero-allocation poll loop and high-resolution timer (`CLOCK_MONOTONIC`).
- Cgroups v2 integration (`pids.max`, `memory.max`, `cpu.max`) with verified cleanup.
- Linux namespaces setup (`CLONE_NEWUSER`, `CLONE_NEWNS`, `CLONE_NEWPID`, `CLONE_NEWNET`, `CLONE_NEWIPC`).
- Seccomp-BPF filters: strict base filter blocking network, raw sockets, namespace manipulation, and system control.
- Bidirectional stream capture with bounded buffers (64 KiB stdout/stderr caps) and lossless UTF-8 / binary handling.
- Evil test fixtures: fork bomb containment, file-snooping defense, network socket block, memory bomb, and wall-clock timeout.

### Phase 2: Runtime Adapter Engine & Tier 1 Vertical Slice
- Target runtimes: **C (`gcc`), C++ (`clang`/`g++`), Python 3 (`python3`)**.
- Implement Stage 1 compilation and Stage 2 execution pipelines for C/C++.
- Implement single-stage isolated execution pipeline for Python 3.
- Enforce truthful milestone emissions (`ACCEPTED` -> `COMPILING` -> `RUNNING` -> `COMPLETED` / `COMPILE_ERROR`).
- Automated tests verifying stdout, stderr, non-zero exits, compile errors, and resource violations.

### Phase 3: Systems Runtimes & gRPC Worker Daemon
- Target runtimes: **Go (`go`), Rust (`rustc`), Bash (`bash`)**.
- Shipped Python `grpc.aio` Worker Daemon architecture:
  - Asynchronous gRPC service (`ExecuteStream`) streaming real-time milestones and returning execution results with telemetry.
  - Off-loop non-blocking sandbox dispatch (`asyncio.to_thread` / thread pools) preventing event loop stalls.
  - Strict admission control (active execution counter and host memory tracking, failing fast with `RESOURCE_EXHAUSTED` when capacity is exceeded).
  - Default loopback binding (`127.0.0.1` / `[::1]`) enforcing host-local isolation against external network exposure.
  - Deterministic gRPC cancellation semantics: RPC cancellation propagates `SIGTERM` to the runner supervisor, cleanly terminating sandboxed processes, tearing down cgroups v2 hierarchies, and pruning ephemeral workspaces.
- Systems runtime adapters:
  - Bash (`bash`) single-stage isolated runtime adapter with strict process tree containment.
  - Truthful uninstalled handling for Go (`go`) and Rust (`rustc`) returning actionable error diagnostics when host toolchains are absent.

### Phase 4: JVM Family & Bytecode Handoff
- Target runtimes: **Java (`javac`/`java`), Kotlin (`kotlinc`/`java`), Scala (`scalac`/`scala`)**.
- Java implemented as the first JVM vertical slice; uninstalled runtimes (Kotlin, Scala) truthfully reported as unsupported stubs.
- Dynamic JVM memory baseline admission:
  - Accommodates HotSpot VM metaspace, GC overhead, and thread stack baseline (~128 MiB) by lifting cgroup memory ceilings to a minimum of 192 MiB.
  - Dynamically computes and scales JVM heap limit (`-Xmx`) based on available cgroup memory budget minus metaspace reserves.
  - Worker daemon memory-aware admission control tracks allocated execution memory against an operator-configured execution budget (`--memory-mb`), defaulting to a safe budget derived from effective host and cgroup memory limits with safe headroom and no overcommit.
  - Guarantees memory reservations persist throughout cancellation cleanup until runner termination and cgroup teardown conclude, preventing competing requests from breaching memory limits during cleanup.
- Bytecode handoff architecture (Directory Classpath vs `target.jar`):
  - Initial planning proposed intermediate `target.jar` archiving; during Phase 4 implementation, direct ephemeral directory classpath handoff (`-cp /workspace Solution`) was selected and validated.
  - Rationale: Both compilation and execution execute against the identical isolated ephemeral `/workspace` mount in the sandbox. `javac -d /workspace` emits all primary, nested, inner, and lambda classes directly. Direct directory classpath execution eliminates the 500-1500 ms latency of a secondary `jar` packaging process, avoids entropy/device dependencies, and natively loads all nested classes and lambda call sites without recompilation.
- Restrictive seccomp profile (`SECCOMP_PROFILE_JVM`):
  - Narrow syscall allowances (`fchdir`, `ftruncate`, `flock`, `sched_getaffinity`, `getrusage`, `restart_syscall`).
  - Strict `prctl` scoping limited exclusively to `PR_SET_NAME` (preventing `PR_SET_DUMPABLE` overrides).
  - Strict network denial (`socket` and `setsockopt` return `EPERM` for internal dual-stack probes; active network operations `connect`/`bind`/`listen` killed by `SIGSYS`).
  - Strict namespace denial (`CLONE_NEW*`, `unshare` killed by `SIGSYS`).

### Phase 5: Interpreted & Managed Scripting Languages
- Target runtimes: **JavaScript (`Node.js`), TypeScript (`tsc`), Ruby, PHP, Python 2, Dart, Swift, Racket, Erlang, Elixir, C# (.NET)**.
- Slice 1: **Ruby (`ruby`) vertical slice**:
  - Implemented single-stage isolated interpreted execution (`/usr/bin/ruby /workspace/Solution.rb`).
  - Truthful milestone emissions: `ACCEPTED` -> `RUNNING` -> `COMPLETED` / `RUNTIME_ERROR` (never emits `COMPILING`).
  - Default resource bounds: 2,000 ms CPU timeout, 5,000 ms wall timeout, 64 MiB cgroup memory limit, 32 PIDs limit, 64 KiB output cap.
  - Native sandbox profile: dedicated `SECCOMP_PROFILE_RUBY` profile permitting narrow initialization syscalls (`sched_getaffinity`, `eventfd2`, `epoll_create1`/polling) and narrowly scoped `prctl` operations (`PR_SET_NAME`, `PR_SET_THP_DISABLE`, `PR_SET_VMA` with `PR_SET_VMA_ANON_NAME`) while maintaining default-deny for all network (`socket`, `connect`, etc.) and namespace operations (`clone` namespace flags, `unshare`).
- Slice 2: **JavaScript (Node.js) (`javascript`) vertical slice**:
  - Implemented single-stage isolated interpreted execution (`/usr/bin/node /workspace/Solution.js`).
  - Truthful milestone emissions: `ACCEPTED` -> `RUNNING` -> `COMPLETED` / `RUNTIME_ERROR` (never emits `COMPILING`).
  - Measured resource bounds: 2,000 ms CPU timeout, 5,000 ms wall timeout, 128 MiB cgroup memory limit (accommodating ~50 MiB baseline resident set size without inflating for V8's ~1.28 GiB pointer compression virtual address reservation), 32 PIDs limit, 64 KiB output cap.
  - Native sandbox profile: dedicated `SECCOMP_PROFILE_NODE` profile permitting narrow initialization syscalls (`capget`, `sched_getaffinity`, `eventfd2`, `epoll_create1`, `epoll_ctl`, `epoll_wait`, `epoll_pwait`, `epoll_pwait2`), returning `ENOSYS` on `io_uring_setup` for safe libuv fallback, and narrowly scoped `prctl` operations (`PR_SET_NAME`, `PR_SET_VMA` with `PR_SET_VMA_ANON_NAME`), while maintaining default-deny for `capset`, all network (`socket`, `connect`, etc.), and namespace operations (`clone` namespace flags, `unshare`).
- Slice 3: **PHP (`php`) vertical slice**:
  - Implemented single-stage isolated interpreted execution (`/usr/bin/php /workspace/Solution.php`).
  - Truthful milestone emissions: `ACCEPTED` -> `RUNNING` -> `COMPLETED` / `RUNTIME_ERROR` (never emits `COMPILING`).
  - Measured resource bounds: 2,000 ms CPU timeout, 5,000 ms wall timeout, 64 MiB cgroup memory limit (accommodating ~24.8 MiB baseline resident set size and measured ~32.5 MiB under 500k-element array load), 16 PIDs limit, 64 KiB output cap.
  - Native sandbox profile: dedicated `SECCOMP_PROFILE_PHP` profile permitting narrow initialization syscalls (`sched_getaffinity` for core count detection) and narrowly scoped `prctl` operations (`PR_SET_NAME` for thread naming, `PR_SET_VMA` strictly with `PR_SET_VMA_ANON_NAME` for Zend memory manager anonymous mapping naming), while maintaining default-deny for `capset`, scheduler modifications (`sched_setparam`, `sched_setscheduler`, `setpriority`, `nice`), all network (`socket`, `connect`, etc.), and namespace operations (`clone` namespace flags, `unshare`). Preserves default sandbox `clone` handling (no namespace flags) and `clone3 -> ENOSYS`.
- Slice 4: **TypeScript (`typescript`) vertical slice**:
  - Implemented 2-stage transpiled execution: Stage 1 compilation via `/usr/bin/env NODE_DISABLE_COMPILE_CACHE=1 /usr/bin/tsc --target ES2022 --module CommonJS /workspace/Solution.ts` followed by Stage 2 execution via `/usr/bin/node /workspace/Solution.js`.
  - Truthful milestone emissions: `ACCEPTED` -> `COMPILING` -> `RUNNING` -> `COMPLETED` (syntax/type errors stop at `COMPILING` and produce `COMPILE_ERROR`).
  - Compile-time avoidance of directory creation: Passing `NODE_DISABLE_COMPILE_CACHE=1` via `/usr/bin/env` disables Node.js module compile cache initialization, completely avoiding `mkdir` syscalls and allowing clean compilation under existing `SECCOMP_PROFILE_NODE` with zero Tier A sandbox changes.
  - Measured resource bounds: 5,000 ms CPU timeout, 10,000 ms wall timeout, 256 MiB cgroup memory limit (`262144 KiB`) for compilation; 2,000 ms CPU timeout, 5,000 ms wall timeout, 128 MiB cgroup memory limit (`131072 KiB`) for execution.
  - Native sandbox profile: reuses existing `SECCOMP_PROFILE_NODE` for both compilation and execution stages.
- Slice 5: **Dart (`dart`) vertical slice**:
  - Implemented single-stage isolated interpreted execution (`/usr/bin/dart --suppress-analytics run /workspace/Solution.dart`).
  - Truthful milestone emissions: `ACCEPTED` -> `RUNNING` -> `COMPLETED` / `RUNTIME_ERROR` (never emits `COMPILING`).
  - Measured resource bounds: 5,000 ms CPU timeout, 10,000 ms wall timeout, 192 MiB cgroup memory limit (`196608 KiB`, accommodating Dart VM baseline RSS of ~118–120 MiB with ~73 MiB user heap headroom while preventing false OOM kills that occur under 64 MiB), 32 PIDs limit, 64 KiB output cap.
  - Native sandbox profile: dedicated `SECCOMP_PROFILE_DART` (profile 8) permitting narrow initialization syscalls (`sched_getaffinity`, `eventfd2`, `epoll_create1`, `epoll_ctl`, `epoll_wait`, `epoll_pwait`, `epoll_pwait2`, `timerfd_create`, `timerfd_settime`, `timerfd_gettime`), and narrowly scoped `prctl` operations (`PR_SET_NAME`, `PR_GET_NAME`, `PR_SET_VMA` with `PR_SET_VMA_ANON_NAME`), while maintaining default-deny for network, capabilities, and scheduler modifications. Preserves default sandbox `clone` handling and `clone3 -> ENOSYS`.
  - Narrowed SDK mount: bind-mounts host `/opt/dart-sdk` into `/opt/dart-sdk` read-only (`MS_RDONLY | MS_NOSUID | MS_NODEV`), allowing target execution only under that SDK path.
  - Namespace-isolated procfs mount: mounts procfs at `<rootfs>/proc` inside the child's `CLONE_NEWPID` namespace with `MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC` and mount options `hidepid=2,subset=pid`. Resolves glibc `pthread_getattr_np(pthread_self(), &attr)` reading `/proc/self/maps` and `/proc/self/exe` without exposing the host `/proc` or processes outside the child PID namespace, verified read-only and write-denied (`EROFS`).
- Slice 6: **Racket (`racket`) vertical slice**:
  - Implemented single-stage isolated interpreted execution (`/usr/bin/racket /workspace/Solution.rkt`).
  - Truthful milestone emissions: `ACCEPTED` -> `RUNNING` -> `COMPLETED` / `RUNTIME_ERROR` (never emits `COMPILING`).
  - Measured resource bounds: 5,000 ms CPU timeout, 10,000 ms wall timeout, 192 MiB cgroup memory limit (`196608 KiB`, accommodating Racket Chez Scheme baseline RSS of ~135–140 MiB with ~55 MiB user heap headroom while preventing immediate false OOM kills that occur under 64/128 MiB), 32 PIDs limit, 64 KiB output cap.
  - Native sandbox profile: dedicated `SECCOMP_PROFILE_RACKET` (profile 9) permitting narrow initialization syscalls (`sched_getaffinity`, `epoll_create`, `epoll_create1`, `epoll_ctl`, `epoll_wait`, `epoll_pwait`, `epoll_pwait2`, `poll`), and narrowly scoped `prctl` operations strictly limited to `PR_SET_NAME` and `PR_GET_NAME`, while maintaining default-deny for network, capabilities, dumpability, and scheduler modifications. Preserves default sandbox `clone` handling (no namespace flags) and `clone3 -> ENOSYS`.
  - Narrowed configuration mount: bind-mounts host `/etc/racket` into `/etc/racket` read-only (`MS_RDONLY | MS_NOSUID | MS_NODEV`), keeping rootfs isolated from the rest of `/etc`.
- Slice 7: **Erlang (`erlang`) vertical slice**:
  - Implemented single-stage isolated interpreted execution via `/usr/bin/erl` with `+S 2:2` (limiting BEAM CPU schedulers to 2) and `+A 2` (limiting the asynchronous thread pool to 2), `-noshell`, and an evaluation harness compiling and executing `/workspace/solution.erl`.
  - Module / Entry points: supports standard LeetCode Erlang conventions (`-module(solution).` with `start/0`, `main/1`, or `main/0`), returning exit code 0 on success and exit code 1 with clean stderr formatting on compilation errors or runtime exceptions.
  - Truthful milestone emissions: `ACCEPTED` -> `RUNNING` -> `COMPLETED` / `RUNTIME_ERROR` (never emits `COMPILING`).
  - Measured resource bounds: 5,000 ms CPU timeout, 10,000 ms wall timeout, 192 MiB cgroup memory limit (`196608 KiB`, accommodating BEAM baseline RSS of ~56.8 MiB and heavy 1,000,000-integer workloads reaching ~118.8 MiB with comfortable heap headroom), 64 PIDs limit (capping thread growth while guaranteeing unconstrained scheduler and async thread startup across core topologies), 64 KiB output cap.
  - Native sandbox profile: dedicated `SECCOMP_PROFILE_ERLANG` (profile 10) aligned with BEAM emulator requirements: permitting shell launcher operations (`chdir`, `getpgrp`, `getresuid`, `getresgid`, `wait4`), BEAM SMP CPU topology discovery (`sched_getaffinity`), async I/O polling (`epoll_create`, `epoll_create1`, `epoll_ctl`, `epoll_wait`, `epoll_pwait`, `epoll_pwait2`), file descriptor polling (`poll`, `ppoll`), kernel timerfd integration (`timerfd_create`, `timerfd_settime`, `timerfd_gettime`), JIT compilation buffer management (`memfd_create`, `ftruncate`), process group separation and FD hygiene in `erl_child_setup` (`setsid`, `pselect6`, `close_range`), internal stream communication across anonymous domain socketpair (`socketpair` strictly restricted to `AF_UNIX`, `sendmsg`, `recvmsg`, `getsockopt`, `setsockopt`), privilege synchronization (`setfsuid` and `setfsgid` strictly restricted to ID 0), offline resolver fallback without descriptor leakage (`socket` returning `EPERM`), and narrowly scoped `prctl` operations strictly limited to `PR_SET_NAME` and `PR_GET_NAME`. Preserves default-deny for network connections (`connect`, `bind`, etc.), capabilities, dumpability, and scheduler modifications. Preserves default sandbox `clone` handling (no namespace flags) and `clone3 -> ENOSYS`.
  - Zero additional mounts required: all Erlang binaries and runtime libraries reside entirely under `/usr/bin` and `/usr/lib/erlang`, which are already bind-mounted read-only into `/usr` in the sandbox rootfs.
- Slice 8: **Elixir (`elixir`) vertical slice**:
  - Implemented single-stage isolated interpreted execution via `/usr/bin/elixir` with `--erl "+S 2:2 +A 2"` (limiting BEAM schedulers and async threads) and an evaluation harness compiling and executing `/workspace/solution.exs`.
  - Module / Entry points: supports standard LeetCode and competitive programming conventions (`solution.exs` with `Solution.start/0`, `Solution.main/1`, `Solution.main/0`, or standalone script statements), returning exit code 0 on success and exit code 1 with clean stderr diagnostics on syntax or runtime errors.
  - Truthful milestone emissions: `ACCEPTED` -> `RUNNING` -> `COMPLETED` / `RUNTIME_ERROR` (never emits `COMPILING`).
  - Measured resource bounds: 5,000 ms CPU timeout, 10,000 ms wall timeout, 192 MiB cgroup memory limit (`196608 KiB`, accommodating BEAM baseline RSS of ~63.1 MiB and heavy 1,000,000-integer workloads reaching ~153.8 MiB with comfortable heap headroom), 64 PIDs limit (capping thread growth while accommodating 21 active threads under `+S 2:2 +A 2`), 64 KiB output cap.
  - Native sandbox profile: dedicated `SECCOMP_PROFILE_ELIXIR` (profile 11) permitting shell launcher script operations (`chdir`, `getpgrp`, `getresuid`, `getresgid`, `wait4`), BEAM SMP CPU topology discovery (`sched_getaffinity`), async I/O polling (`epoll_create`, `epoll_create1`, `epoll_ctl`, `epoll_wait`, `epoll_pwait`, `epoll_pwait2`), file descriptor polling (`poll`, `ppoll`), kernel timerfd integration (`timerfd_create`, `timerfd_settime`, `timerfd_gettime`), JIT compilation buffer management (`memfd_create`, `ftruncate`), process group separation and FD hygiene in `erl_child_setup` (`setsid`, `pselect6`, `close_range`), internal stream communication across anonymous domain socketpair (`socketpair` strictly restricted to `AF_UNIX`, `sendmsg`, `recvmsg`, `getsockopt`, `setsockopt`), privilege synchronization (`setfsuid` and `setfsgid` strictly restricted to ID 0), offline resolver fallback without descriptor leakage (`socket` returning `EPERM`), and narrowly scoped `prctl` operations strictly limited to `PR_SET_NAME` and `PR_GET_NAME`. Preserves default-deny for network connections (`connect`, `bind`, etc.), capabilities, dumpability, and scheduler modifications. Preserves default sandbox `clone` handling (no namespace flags) and `clone3 -> ENOSYS`.
  - Zero additional mounts required: all Elixir binaries and runtime libraries reside entirely under `/usr/bin` and `/usr/lib/elixir`, which are already bind-mounted read-only into `/usr` in the sandbox rootfs.
- Truthful unsupported runtime status:
  - C# (.NET) (`csharp`): launcher present at `/usr/bin/dotnet`, but no .NET SDK is installed on the host (`dotnet --version` fails); truthfully reported as `Incomplete toolchain on host (launcher present, but SDK check failed)` and marked unsupported.
  - Kotlin (`kotlin`): installed in user-space mise path, but excluded from sandbox rootfs; truthfully reported as `Unavailable to sandbox rootfs (installed in user path)` and marked unsupported.
  - Python 2 (`python2`), Swift (`swift`), Scala (`scala`), Go (`go`), Rust (`rust`): toolchains completely absent on host; truthfully reported as `Not installed on host`.
- Managed runtime memory notes:
  - V8 virtual address cages: V8 uses pointer compression virtual address reservations; cgroup accounting measures resident anonymous memory and page cache rather than virtual reservations. Physical cgroup limits should not be inflated for virtual address layouts without live resident memory evidence.

### Phase 6: Disposable SQL Database Sandboxes
- Target engines: **MySQL, PostgreSQL, MS SQL Server, Oracle SQL**.
- Implement disposable schema generation per execution request:
  - SQLite/PostgreSQL ephemeral instances with automated transaction rollbacks or container drop.
  - Query execution timeout ceiling (e.g. 3,000 ms) and row count ceilings (e.g. 1,000 rows).
  - Formatted tabular result serialization (JSON table columns and rows).
  - Strict SQL security: disallow file I/O (`LOAD DATA INFILE`, `pg_read_file`) and privilege escalation (`GRANT`, `CREATE USER`).

### Phase 7: Data Science & Browser Frontend Runtimes
- Target environments: **Pandas (Python 3 + Pandas/NumPy), React, Vanilla JavaScript (DOM)**.
- Pandas: isolated Python runner with pre-imported data structures, memory thresholds, and DataFrame tabular serialization.
- React / Web: client-side isolated worker or sandboxed iframe (`sandbox="allow-scripts"`) preventing host DOM access, cookie exfiltration, or cross-origin leakage.

### Phase 8: Web Studio Client & Collaborative Editor
- React 18 + TypeScript + Vite frontend.
- Monaco code editor supporting all 27 language modes and syntaxes.
- Multi-language dropdown with honest status badges (supported, in-development, uninstalled).
- Telemetry panel rendering real Linux kernel metrics (`wallMs`, `cpuMs`, `peakMemoryKb`, `exitCode`, `status`).

### Phase 9: End-to-End Verification & Production Packaging
- Multi-service Docker Compose configuration (Client reverse proxy, API Gateway, Worker daemon, Database sandboxes).
- Automated integration test suite validating all supported runtimes end-to-end.
- Load testing under concurrent execution stress.
