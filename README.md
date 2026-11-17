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
| 25 | **Pandas** | `Python 3.14 + Pandas 3.1.0 + NumPy 2.5.4` | `python3` installed; libs missing | Data Science |
| 26 | **React** | `Node.js 18 + React 18.2.0` | `Node.js` installed | Web Framework |
| 27 | **Vanilla JS** | Browser DOM JavaScript sandbox | Browser environment | Web Frontend |

---

## 3. Project Documentation

- [`AI_RULES.md`](file:///home/zogratis/Projects/Personal%20Projects/polyexecution-studio2/AI_RULES.md): Mandatory engineering, security, and governance standards.
- [`PROGRESS.md`](file:///home/zogratis/Projects/Personal%20Projects/polyexecution-studio2/PROGRESS.md): Append-only engineering log.
- [`docs/01_ARCHITECTURE.md`](file:///home/zogratis/Projects/Personal%20Projects/polyexecution-studio2/docs/01_ARCHITECTURE.md): Multi-layer security model, runtime adapter contract, and database sandbox design.
- [`docs/04_BUILD_PLAN.md`](file:///home/zogratis/Projects/Personal%20Projects/polyexecution-studio2/docs/04_BUILD_PLAN.md): 10-phase staged implementation roadmap.
