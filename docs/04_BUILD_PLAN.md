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
- Implement Kotlin/Rust gRPC worker daemon managing job queues, timeouts, and execution workspaces.
- Handle Go compilation cache and runtime thread limits (`GOMAXPROCS`).
- Handle Rust compiler memory overhead and single-binary bytecode artifacts.
- Handle Bash subshell isolation, command restrictions, and fork limits.

### Phase 4: JVM Family & Bytecode Handoff
- Target runtimes: **Java (`javac`/`java`), Kotlin (`kotlinc`/`java`), Scala (`scalac`/`scala`)**.
- Dynamic JVM memory baseline admission (accommodating Java VM metaspace and heap overhead).
- Multi-class JAR bundling (`target.jar`) to support nested classes, lambdas, and companion objects without host recompilation.
- Restrictive seccomp profile accommodating JVM signal handlers (SIGSEGV/SIGQUIT/SIGUSR2).

### Phase 5: Interpreted & Managed Scripting Languages
- Target runtimes: **JavaScript (`Node.js`), TypeScript (`tsc`), Ruby, PHP, Python 2, Dart, Swift, Racket, Erlang, Elixir, C# (.NET)**.
- Implement specialized memory allocation rules (e.g. 5,120 MiB virtual address space for Node/V8 pointer compression cages).
- Single-pass TypeScript transpilation and execution.
- Erlang/Elixir BEAM VM lightweight process management.
- .NET runtime and JIT execution configuration.

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
