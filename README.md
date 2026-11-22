# PolyExecution Studio 2 (PES 2)

PolyExecution Studio 2 is a high-performance, multi-language remote execution engine and browser studio engineered to safely evaluate the 27 programming environments offered in LeetCode's code editor.

---

## 1. Project Mission & Core Principles

1. **Zero-Trust Host Execution:** Untrusted user code and compilation steps execute strictly inside kernel-enforced sandboxes using Linux cgroups v2, unprivileged user/mount/pid/net namespaces, and strict seccomp-bpf syscall filters.
2. **Explicit Runtime Adapters:** Instead of relying on brittle shell scripts or monolithic Docker images, each programming environment is represented by a typed `RuntimeAdapter` defining its compilation pipeline, runtime binary, seccomp profile, and resource limits.
3. **Honest Capabilities:** Languages and runtimes are never marked "supported" in the studio until they pass real, verified end-to-end execution tests through the production sandbox pipeline.

---

## 2. Official LeetCode Environment Inventory (As of 2026-11-17)

| # | Language / Environment | Official LeetCode Version & Specs | Host System Status | Execution Family |
| :-: | :--- | :--- | :--- | :--- |
| 1 | **C++** | `clang 19` (C++23, `-O2`, ASan enabled) | `clang 23.1.3` installed | Native Compiled |
| 2 | **Java** | `OpenJDK 25` | `OpenJDK 21.0.13 LTS` installed | JVM |
| 3 | **Python 3** | `Python 3.14` | `Python 3.12.15` installed | Interpreted |
| 4 | **Python 2** | `Python 2.7.18` (Legacy) | Not installed on host | Interpreted |
| 5 | **MySQL** | `MySQL 9.7` | Not installed on host | SQL Database |
| 6 | **MS SQL Server** | `MS SQL Server 2019` | Not installed on host | SQL Database |
| 7 | **Oracle SQL** | `Oracle SQL 11.2` | Not installed on host | SQL Database |
| 8 | **C** | `gcc 14` (gnu11, `-O2`, ASan enabled, GLib 2.0) | `gcc 16.2.1` installed | Native Compiled |
| 9 | **C#** | `C# 14 (.NET 10)` | `.NET 10.0.403` installed | Managed / JIT |
| 10 | **JavaScript** | `Node.js 26.12.1` (`lodash`, `datastructures-js`) | `Node.js 26.8.2` installed | Interpreted / V8 |
| 11 | **Ruby** | `Ruby 4.0` (`Algorithms` module) | `Ruby 3.4.10` installed | Interpreted |
| 12 | **Bash** | `Bash 5.2.21` | `Bash 5.3.20` installed | Interpreted Shell |
| 13 | **Swift** | `Swift 6.4.1` (Swift 5 mode) | Not installed on host | Native Compiled |
| 14 | **Go** | `Go 1.27` (`gods` library) | Not installed on host | Native Compiled |
| 15 | **Scala** | `Scala 3.9.0` (JDK 25) | Not installed on host | JVM |
| 16 | **Kotlin** | `Kotlin 2.4.21` (JDK 25) | Not installed on host | JVM |
| 17 | **Rust** | `Rust 1.99.1` (Edition 2024, `-O2`, `itertools`, `regex`) | Not installed on host | Native Compiled |
| 18 | **PHP** | `PHP 8.5` (`bcmath` enabled) | Not installed on host | Interpreted |
| 19 | **TypeScript** | `TypeScript 7.0.2` on Node.js 26.12.1 | `Node.js` installed; `tsc` missing | Interpreted / V8 |
| 20 | **Racket** | `Racket CS 9.3` (`#lang racket`) | Not installed on host | Functional / Scheme |
| 21 | **Erlang** | `Erlang/OTP 29` | Not installed on host | BEAM VM |
| 22 | **Elixir** | `Elixir 1.20` (OTP 29) | Not installed on host | BEAM VM |
| 23 | **Dart** | `Dart 3.13` | Not installed on host | Managed / JIT |
| 24 | **PostgreSQL** | `PostgreSQL 18` | `psql 18.6` client installed | SQL Database |
| 25 | **Pandas** | `Python 3.14 + Pandas 3.1.0 + NumPy 2.5.4` | Single-stage isolated harness; 256 MiB memory; tabular JSON | Data Science |
| 26 | **React** | `React 18.2.0` | Sandboxed iframe (`sandbox="allow-scripts"`, opaque origin, strict CSP) | Web Framework |
| 27 | **Vanilla JS** | `Browser JavaScript (DOM, ES2022)` | Sandboxed iframe (`sandbox="allow-scripts"`, opaque origin, strict CSP) | Interpreted / DOM |
| 28 | **SQLite** | `SQLite 3.54 (in-memory)` | `SQLite 3.54.0` installed | SQL Database |

---

## 3. Project Documentation

- [AI_RULES.md](AI_RULES.md): Mandatory engineering, security, and governance standards.
- [PROGRESS.md](PROGRESS.md): Append-only engineering log.
- [docs/01_ARCHITECTURE.md](docs/01_ARCHITECTURE.md): Multi-layer security model, runtime adapter contract, and database sandbox design.
- [docs/04_BUILD_PLAN.md](docs/04_BUILD_PLAN.md): 10-phase staged implementation roadmap.

---

## 4. Web Studio Client & Collaborative Editor

PolyExecution Studio 2 features a standalone browser-based developer studio built with React 18, TypeScript, Vite, and Monaco Editor.

### Features
- **Monaco Code Editor:** Syntax modes and highlighters for all 27 LeetCode languages + Bash (28 environments) with standard starter templates.
- **Truthful Language Picker:** Categorized by execution family with accurate status indicators (`Supported`, `In Development`, `Uninstalled`).
- **Kernel Telemetry Panel:** Direct visualization of Linux cgroups v2 resource metrics (`wallMs`, `cpuMs`, `peakMemoryKb`, `exitCode`, `signal`, `status`, and milestone lifecycle events).
- **Multi-Stream Output Terminal:** View standard output, standard error, tabular results for SQL and Pandas DataFrames, and live isolated iframe previews for React and Vanilla JavaScript.
- **Collaborative Architecture:** Honest standalone editor mode documenting the exact backend requirements (persistent WebSocket CRDT gateway) for multi-user synchronization without fake cursors or simulated bots.

### Running the Web Studio Client
```bash
# Navigate to the client directory
cd client

# Install dependencies
npm install

# Start development server
npm run dev
```

### Running Frontend Tests & Production Build
```bash
# Run Vitest test suite
cd client
npm test

# Run TypeScript type check and production bundle build
npm run build
```

---

## 5. Production Packaging & Docker Compose Deployment

PolyExecution Studio 2 includes a production-ready, multi-service Docker Compose deployment that packages the entire system with defense-in-depth container defaults.

### Service Architecture
- **`reverse-proxy` (Nginx 1.27):** Sole public-facing ingress bound strictly to loopback (`${PES2_BIND_HOST:-127.0.0.1}:${PES2_PORT:-8088}:80`) by default. Fails closed with exit code 2 at startup if `PES2_BIND_HOST` is configured on a public interface without `PES2_API_KEY`. Routes `/api/` to the API gateway and `/` to the client, forwarding `Authorization` and `X-API-Key` headers. Hardened with `read_only: true`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, and tmpfs mounts.
- **`client` (Web Studio):** Internal static asset server serving production-compiled React 18 bundles, completely off public interfaces.
- **`api-gateway` (Python HTTP):** Translates incoming HTTP REST and SSE requests to internal gRPC calls against the worker daemon. Enforces 256 KiB request payload ceilings, connection socket timeouts (15s), bounded connection concurrency ceilings (HTTP 503 on saturation), strict CORS origin whitelisting (HTTP 403), JSON schema validation, custom resource limits bounds, and mandatory token verification whenever public ingress is configured.
- **`worker-daemon` (Python gRPC):** Core execution supervisor managing isolated ephemeral cgroup sandboxes and admission control. Hardened with `no-new-privileges:true`, no `SYS_ADMIN` capability, restricted container seccomp, and a narrowly delegated cgroup subtree (`${PES2_CGROUP_ROOT:-/sys/fs/cgroup/pes2}`) for isolated sandbox execution.
- **`Database Sandboxes`:** SQLite is supported via in-memory isolation. Persistent database containers (`db-postgres`) have been removed from the default production Compose stack until wired to disposable sandbox paths.

### Cgroup Delegation Requirements for Containerized Worker
The containerized worker daemon mounts `${PES2_CGROUP_ROOT:-/sys/fs/cgroup/pes2}:/sys/fs/cgroup/pes2:rw`. On systemd-managed Linux hosts, the delegated subtree must be provisioned for the container user before launching nested sandboxes:
```bash
sudo mkdir -p /sys/fs/cgroup/pes2
sudo chown -R $(id -u):$(id -g) /sys/fs/cgroup/pes2
echo "+cpu +memory +pids" | sudo tee /sys/fs/cgroup/cgroup.subtree_control
```
Without host-level cgroup delegation, container-nested sandbox execution safely fails closed with `SANDBOX_ERROR`. Bare-metal Linux host execution remains the primary deployment target for kernel-enforced sandboxing.

### Quickstart Deployment
```bash
# 1. Create your local environment configuration (bound to loopback 127.0.0.1 by default)
cp .env.example .env

# 2. Build and launch all services in detached mode
docker compose up -d

# 3. Verify deployment health
curl -s http://127.0.0.1:8088/api/health

# 4. View running services and health status
docker compose ps
```

### Running Automated Test Suites
```bash
# Run full pytest suite (including Gateway, Ingress, Compose, E2E runtimes, and Load concurrency tests)
pytest

# Run focused hardening, gateway, and Compose integration suites
pytest tests/test_ingress_hardening.py tests/test_compose_integration.py tests/test_gateway.py

# Run frontend Vitest test suite and production build
cd client && npm test && npm run build && cd ..

# Run sandbox seccomp profile unit test harness
cd sandbox && make test && cd ..
```

### Clean Teardown
```bash
docker compose down -v
```
