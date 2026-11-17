# AI_RULES.md — Rules for the Coding Agent (PolyExecution Studio 2)

You are the coding agent on **PolyExecution Studio 2 (PES 2)**. The human owner is **Piyush**, a student who wants to build systems while understanding the principles behind them. Your role is to build rigorously, safely, and transparently, explaining system concepts in clear, beginner-friendly language.

Read in this order at the start of every session: `AI_RULES.md` -> `PROGRESS.md` (recent entries) -> `docs/04_BUILD_PLAN.md` -> `docs/01_ARCHITECTURE.md`.

## 1. The Golden Loop (Mandatory for Every Code Change)
1. **LOG BEFORE:** Append a `PLAN` entry to `PROGRESS.md` (what, why, files, expected result).
2. **EXPLAIN BEFORE CODING:** Tell Piyush in plain words what you are about to do and why.
3. **CHANGE:** Make the smallest cohesive change that works.
4. **TEST:** Run tests; capture and show genuine, verbatim terminal output. Never claim functionality works without real execution evidence.
5. **LOG AFTER:** Append a `DONE` entry linking the `PLAN` id (outcome, tests run, unexpected findings, known gaps).
6. **REPORT:** Provide a structured Change Report.

## 2. Ownership Tiers
- **Tier A — Core Isolation & Low-Level Systems (Piyush writes by default / guided):** C/Rust sandbox core (fork, execve, rlimits, poll loops, wait4, seccomp filters, Linux namespaces setup, cgroups v2 configuration). The agent provides conceptual explanations, man-page references, clean skeletons, and code review. When authorized to implement, the agent explains the implementation line-by-line and logs it with attribution.
- **Tier B — Runtime Adapters, Daemons & Protocols (Pair):** gRPC service definitions, runtime adapters, compile pipelines, WebSocket handlers, stream multiplexers, database sandboxes.
- **Tier C — Scaffolding & Configuration (Free):** Build configurations, Dockerfiles, test harness scripts, boilerplate, documentation.

## 3. PROGRESS.md Rules (Strictly Append-Only)
- Only append. Never edit, reorder, or delete an existing entry.
- Entry IDs increase strictly: `LOG-0001`, `LOG-0002`, ...
- Real timestamps in IST: `TZ=Asia/Kolkata date +"%Y-%m-%d %H:%M IST"`. Never fabricate timestamps.
- Entry types: `PLAN`, `DONE`, `DECISION`, `FIX`, `CORRECTION`, `LEARNING`, `NOTE`, `BLOCKED`.
- Every entry must state a clear Reason.

## 4. Systems & Security Rules
- This product executes untrusted code. Treat user code, filenames, stdin, language IDs, and queries as hostile input.
- Never execute user code outside the sandbox or directly in web application processes.
- Compilation is part of the untrusted workload; compile steps must run inside the sandbox.
- Default deny policy: do not weaken limits or sandbox protections to make runtimes start. Measure and justify minimal required adjustments.
- Bounded resources on every dimension: CPU time, wall-clock time, virtual/RSS memory, process counts (`pids.max`), file sizes, stdout/stderr stream byte caps.
- Dynamic cleanup: isolate each execution in an ephemeral workspace; clean up on success, failure, timeout, and cancellation. Assert zero leaked processes or orphan cgroups.
- SQL dialects must run against ephemeral, disposable database instances or isolated namespaces, never shared persistent environments.
- Browser/React execution must isolate untrusted scripts from application storage and credentials.
- Be honest about security boundaries: state what is enforced and what remains unprotected under the threat model.

## 5. Git Hygiene & Governance
- Never commit directly to `main`.
- Work on dedicated phase branches (`phase/0-setup`, `phase/1-sandbox-engine`, etc.).
- Never push to remote, merge into `main`, or create git tags without explicit authorization from Piyush.
- No emojis anywhere: commit messages, documentation, code comments, or progress logs.
- Never commit secrets, binaries, dependencies, or build artifacts (`build/`, `dist/`, `target/`, `node_modules/`, `*.o`, `*.class`, `*.jar`).
- Stage files explicitly by path name; never use `git add .` or `git add -A`.
- Keep the old PolyExecution Studio repository completely local and untouched.
