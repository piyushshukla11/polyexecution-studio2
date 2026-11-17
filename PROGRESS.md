# PROGRESS.md — PolyExecution Studio 2 (Append-Only Progress Log)

This log tracks every change, plan, decision, and verification result in PolyExecution Studio 2.
Rules: Append-only. Never edit or delete existing entries. Real IST timestamps.

---

## [LOG-0001] 2026-11-17 11:20 IST | PLAN | actor: agent
- Goal: Initialize PolyExecution Studio 2 (PES 2) repository baseline, governance framework, architecture documentation, and toolchain environment audit.
- Steps:
  1. Inspect cloned repository state (`polyexecution-studio2`), active remotes, and branch structure.
  2. Create Phase 0 branch: `phase/0-setup`.
  3. Create `AI_RULES.md` defining the golden loop, ownership tiers, append-only rules, and security guidelines.
  4. Query and verify the official LeetCode 27-environment inventory as of 2026-11-17 against official documentation.
  5. Audit the host system toolchains and document available vs missing runtime binaries.
  6. Create `docs/01_ARCHITECTURE.md` defining the heterogeneous execution family model and typed runtime adapter registry.
  7. Create `docs/04_BUILD_PLAN.md` detailing the 10-phase staged implementation plan from vertical slice to full coverage.
  8. Update `README.md` with project mission, architecture overview, and verified inventory status.
- Files: `AI_RULES.md`, `PROGRESS.md`, `README.md`, `docs/01_ARCHITECTURE.md`, `docs/04_BUILD_PLAN.md`.
- Linked: None (Initial entry).
- Tutor review: pending


## [LOG-0002] 2026-11-17 11:25 IST | DONE | actor: agent
- Goal: Initialize PolyExecution Studio 2 (PES 2) repository baseline, governance framework, architecture documentation, and toolchain environment audit.
- Outcome:
  1. Cloned `https://github.com/piyushshukla11/polyexecution-studio2` cleanly and branched to `phase/0-setup` from `main` commit `1c42dee`.
  2. Established `AI_RULES.md` with strict append-only progress requirements, systems security rules, and zero-emoji policy.
  3. Verified official LeetCode 27-environment inventory as of 2026-11-17 across all 5 execution families (Native Compiled, JVM, Interpreted Scripting, SQL Databases, Data Science / Frameworks).
  4. Audited host system toolchains: confirmed availability of Python 3.12, GCC 16.2, Clang 23.1, Node 26.8, OpenJDK 21, Ruby 3.4, Bash 5.3, .NET 10, PostgreSQL 18. Documented missing host toolchains (Rust, Go, Swift, Scala, Kotlin, PHP, Dart, Racket, Erlang, Elixir, MySQL, Oracle SQL, MS SQL, Pandas).
  5. Designed typed `RuntimeAdapter` and `RuntimeRegistry` architectural contracts in `docs/01_ARCHITECTURE.md`.
  6. Authored comprehensive 10-phase implementation roadmap in `docs/04_BUILD_PLAN.md` defining the minimal vertical slice (Phase 2: C, C++, Python 3) before expanding into systems, JVM, and database runtimes.
- Verification:
  - `git status`: cleanly staged on `phase/0-setup`
  - Host toolchain versions audited and logged verbatim
  - Verified LeetCode documentation against official support knowledge base
- Files: `AI_RULES.md`, `PROGRESS.md`, `README.md`, `docs/01_ARCHITECTURE.md`, `docs/04_BUILD_PLAN.md`.
- Linked: LOG-0001.
- Tutor review: pending


## [LOG-0003] 2026-11-17 12:04 IST | CORRECTION of LOG-0002 | actor: agent
- Goal: Correct host toolchain audit count enumeration in LOG-0002.
- Correction: LOG-0002 stated "17 of 27 environments ... are not yet installed on the host" while enumerating 14 in parentheses. The precise audit as of 2026-11-17 across all 27 LeetCode environments shows 9 available toolchains on the host (C, C++, Python 3, Java, C#, Node.js, Ruby, Bash, PostgreSQL client) and 18 missing toolchains/packages (Rust, Go, Swift, Scala, Kotlin, PHP, Dart, Racket, Erlang, Elixir, MySQL, Oracle SQL, MS SQL, Python 2, TypeScript global tsc, Pandas/NumPy packages, React build packages, and standalone ECMAScript Vanilla JS harness).
- Files: `PROGRESS.md`.
- Linked: LOG-0002.
- Tutor review: pending


## [LOG-0004] 2026-11-17 12:05 IST | PLAN | actor: agent
- Goal: Complete Phase 0 DoD gaps: implement append-only pre-commit hook and repository hygiene validator, add reproducible test suite, configure local core.hooksPath, replace machine-specific links in README.md, correct Vanilla JS environment specification, and refine SQL isolation architecture language.
- Steps:
  1. Create `scripts/check_progress_append_only.sh`: validate that `PROGRESS.md` commits contain zero deletions/modifications and zero insertions in the middle of historical entries.
  2. Create `scripts/check_repo_hygiene.sh`: block secrets, build artifacts, junk files, stray markdown, files > 1 MB, and emojis in staged files.
  3. Create `scripts/hooks/pre-commit`: run hygiene, append-only, and diff checks before every commit. Configure local `git config core.hooksPath scripts/hooks`.
  4. Create `scripts/test_append_only.sh`: reproducible test proving valid append passes, while line deletion, line modification, and middle insertion are rejected.
  5. In `README.md`: replace machine-specific `file:///` URLs with repository-relative paths (`./AI_RULES.md`, `./PROGRESS.md`, etc.); update Vanilla JS to ECMAScript 2023.
  6. In `docs/01_ARCHITECTURE.md`: refine SQL isolation language to emphasize disposable ephemeral databases/schemas and engine-level privilege boundaries over denylists.
- Files: `PROGRESS.md`, `README.md`, `docs/01_ARCHITECTURE.md`, `scripts/check_progress_append_only.sh`, `scripts/check_repo_hygiene.sh`, `scripts/hooks/pre-commit`, `scripts/test_append_only.sh`.
- Linked: LOG-0003.
- Tutor review: pending


## [LOG-0005] 2026-11-17 12:07 IST | DONE | actor: agent
- Goal: Complete Phase 0 DoD gaps: implement append-only pre-commit hook and repository hygiene validator, add reproducible test suite, configure local core.hooksPath, replace machine-specific links in README.md, correct Vanilla JS environment specification, and refine SQL isolation architecture language.
- Outcome:
  1. Append-Only Enforcement & Hook Configuration:
     - Implemented `scripts/check_progress_append_only.sh` detecting historical deletions, line modifications, and middle insertions.
     - Configured local clone `core.hooksPath` to `scripts/hooks` with executable `pre-commit` hook.
     - Created `scripts/test_append_only.sh` and verified 4/4 passing scenarios (valid append accepted, line modification blocked, line deletion blocked, middle insertion blocked).
  2. Repository Hygiene Validator:
     - Implemented `scripts/check_repo_hygiene.sh` preventing staged secrets, build artifacts, stray markdown files, files > 1 MB, and emojis.
  3. Documentation Corrections:
     - In `README.md`: replaced machine-specific `file:///` URLs with repo-relative links; corrected Vanilla JS specification to ECMAScript 2023.
     - In `docs/01_ARCHITECTURE.md`: refined SQL isolation architecture to emphasize disposable per-request databases, kernel/engine isolation, and non-reliance on keyword denylists.
     - In `PROGRESS.md`: logged CORRECTION entry `[LOG-0003]` clarifying 9 available and 18 missing host toolchains across the 27 LeetCode environments.
- Verification:
  - `bash scripts/test_append_only.sh`: 4/4 passed
  - `bash scripts/check_repo_hygiene.sh`: passed cleanly (0 hygiene errors)
  - `bash scripts/check_progress_append_only.sh`: passed cleanly (append-only validated)
  - `git diff --check`: passed cleanly (0 whitespace/diff errors)
- Files: `PROGRESS.md`, `README.md`, `docs/01_ARCHITECTURE.md`, `scripts/check_progress_append_only.sh`, `scripts/check_repo_hygiene.sh`, `scripts/hooks/pre-commit`, `scripts/test_append_only.sh`.
- Linked: LOG-0004.
- Tutor review: pending


## [LOG-0006] 2026-11-17 12:10 IST | PLAN | actor: agent
- Goal: Prepare Phase 1 C sandbox runner skeleton with Tier A TODO markers for Piyush, header definitions, and build configuration.
- Steps:
  1. Create `sandbox/include/runner.h`: declare `runner_config_t`, `runner_result_t`, and lifecycle functions.
  2. Create `sandbox/src/cgroup.c`: skeleton for cgroups v2 (`mkdir`, `pids.max`, `memory.max`, `rmdir`) with `TODO(Piyush)` guidance.
  3. Create `sandbox/src/seccomp.c`: skeleton for seccomp-bpf filter with `TODO(Piyush)` guidance.
  4. Create `sandbox/src/runner.c`: skeleton for monotonic timing, pipes, fork/execve, poll loop, and wait4 with `TODO(Piyush)` guidance.
  5. Create `sandbox/Makefile`: strict compilation with `-Wall -Wextra -Werror -O2 -lseccomp`.
- Files: `PROGRESS.md`, `sandbox/Makefile`, `sandbox/include/runner.h`, `sandbox/src/cgroup.c`, `sandbox/src/runner.c`, `sandbox/src/seccomp.c`.
- Linked: LOG-0005.
- Tutor review: pending


## [LOG-0007] 2026-11-17 12:12 IST | DONE | actor: agent
- Goal: Prepare Phase 1 C sandbox runner skeleton with Tier A TODO markers for Piyush, header definitions, and build configuration.
- Outcome:
  1. Authoring of Phase 1 Skeletons:
     - Authored `sandbox/include/runner.h` defining configuration limits, metrics structs, and lifecycle function contracts.
     - Authored `sandbox/src/cgroup.c` with detailed conceptual guides and `TODO(Piyush)` markers for cgroups v2 directory creation, limits, and cleanup.
     - Authored `sandbox/src/seccomp.c` with guidance and `TODO(Piyush)` markers for default-deny seccomp filter setup.
     - Authored `sandbox/src/runner.c` with process lifecycle guidance and `TODO(Piyush)` markers for pipes, fork, execve, poll loop, and wait4 rusage extraction.
     - Authored `sandbox/Makefile` compiling cleanly under `-Wall -Wextra -Werror -O2 -lseccomp`.
  2. Respecting Tier A Boundary:
     - Did not author the protected core implementation. Maintained explicit `TODO(Piyush)` markers for Piyush to author or authorize.
- Verification:
  - `make -C sandbox`: compiled cleanly with 0 warnings and 0 errors
  - `make -C sandbox test`: executed `./pes2-runner /bin/true` cleanly
  - `make -C sandbox clean`: cleaned object files and target binary
  - `scripts/hooks/pre-commit`: validated repository hygiene and append-only rules
- Files: `PROGRESS.md`, `sandbox/Makefile`, `sandbox/include/runner.h`, `sandbox/src/cgroup.c`, `sandbox/src/runner.c`, `sandbox/src/seccomp.c`.
- Linked: LOG-0006.
- Tutor review: pending


## [LOG-0008] 2026-11-17 14:16 IST | PLAN | actor: agent
- Goal: Extend the append-only regression harness to prove Git invokes the configured pre-commit hook on actual commits in an isolated temporary repository.
- Steps:
  1. Copy the checked-in pre-commit hook and its validation scripts into the temporary repository while preserving their relative layout.
  2. Configure core.hooksPath only in the temporary repository after creating its baseline commit.
  3. Assert a real append commit succeeds, while real historical modification and deletion commits are rejected without advancing HEAD.
  4. Run the focused append-only suite and repository hygiene checks; keep all temporary data outside the PES 2 worktree.
- Files: `PROGRESS.md`, `scripts/test_append_only.sh`.
- Linked: LOG-0007.
- Tutor review: pending


## [LOG-0009] 2026-11-17 14:18 IST | DONE | actor: agent
- Goal: Extend the append-only regression harness to prove Git invokes the configured pre-commit hook on actual commits in an isolated temporary repository.
- Outcome:
  1. Extended `scripts/test_append_only.sh` to copy the checked-in pre-commit hook and its validation scripts into its temporary Git repository, preserving their relative paths, and configure `core.hooksPath` locally in that repository only.
  2. Added real `git commit` checks proving that a valid append is accepted and that historical modification and deletion commits are rejected without advancing `HEAD`.
  3. Preserved the four direct checker scenarios for append, edit, deletion, and middle insertion.
- Verification:
  - `bash scripts/test_append_only.sh`: 7/7 passed, including all three real commit-hook cases.
  - `bash scripts/check_repo_hygiene.sh`: passed.
  - `bash scripts/check_progress_append_only.sh`: passed.
  - `git diff --cached --check`: passed.
- Files: `PROGRESS.md`, `scripts/test_append_only.sh`.
- Linked: LOG-0008.
- Tutor review: pending


## [LOG-0010] 2026-11-17 14:23 IST | PLAN | actor: agent
- Goal: Implement the authorized Phase 1 native C sandbox runner with bounded process supervision, fail-closed cgroup v2 and namespace setup, default-deny seccomp, and adversarial tests.
- Reason: The current Phase 1 sources are compilation-only skeletons; the requested push must contain a real, reviewable sandbox implementation rather than TODO stubs.
- Steps:
  1. Correct the Tier A seccomp guidance to default-deny and implement an explicit syscall allowlist applied only after child isolation setup.
  2. Implement bounded cgroup v2 lifecycle, parent-controlled child attach/exec handshake, Linux namespace and minimal-root setup, rlimits, monotonic timeout, nonblocking stdout/stderr draining, wait4 metrics, and deterministic cleanup.
  3. Add safe acceptance and adversarial fixtures for exit status, timeout, output limits, fork containment, network denial, and filesystem isolation; fail closed when required host cgroup delegation is unavailable.
  4. Run compile, test, hygiene, append-only, and diff checks; document which runtime cases the current host can and cannot verify.
- Files: `PROGRESS.md`, `sandbox/include/runner.h`, `sandbox/src/runner.c`, `sandbox/src/cgroup.c`, `sandbox/src/seccomp.c`, `sandbox/Makefile`, `sandbox/tests/*`, `sandbox/.gitignore`.
- Tier: AGENT-WROTE-TIER-A, explicitly authorized by Piyush on 2026-11-17.
- Linked: LOG-0009.
- Tutor review: pending


## [LOG-0011] 2026-11-17 14:45 IST | DONE | actor: agent
- Goal: Implement the authorized Phase 1 native C sandbox runner with bounded process supervision, fail-closed cgroup v2 and namespace setup, default-deny seccomp, and adversarial tests.
- Outcome:
  1. Implemented `runner_execute` and the CLI with bounded request validation, a parent-controlled child handshake, user/mount/PID/network/IPC namespaces, private chroot mounts, rlimits, monotonic wall and cgroup CPU timing, nonblocking stdout/stderr draining, exact per-stream output caps, wait4 accounting, cancellation, and cleanup.
  2. Implemented cgroup v2 `pids.max`, `memory.max`, `memory.oom.group`, and `cpu.max`; attachment occurs before the child is released to execute. Setup fails closed when required controllers or writable cgroup delegation are unavailable.
  3. Replaced default-allow seccomp guidance with a default-kill filter and explicit syscall allowlist; namespace-creating clone flags and network syscalls remain denied.
  4. Added fixtures and a runtime suite for exit status, wall timeout, output overflow, memory limit, PID limit/fork pressure, network denial, filesystem visibility, cancellation, and cleanup. Runtime cases are gated on delegated cgroup availability; the current host does not provide it.
  5. Authored the Tier A implementation as agent work under Piyush's explicit authorization; no agent work is represented as owner-authored learning.
- Verification:
  - `make -C sandbox clean all`: passed with `-Wall -Wextra -Werror -O2`.
  - `make -C sandbox test`: fail-closed test passed; runtime isolation cases were skipped because `/sys/fs/cgroup/cgroup.subtree_control` is not writable in this environment.
  - `unshare --user --map-root-user --mount --pid --net --ipc --fork /usr/bin/true`: passed; namespace availability probe only, not a sandbox runtime test.
  - `clang --analyze`: no diagnostics.
  - `bash scripts/test_append_only.sh`: 7/7 passed, including real temporary-repository commits through the pre-commit hook.
  - Repository hygiene, append-only, and diff checks passed before commit.
- Files: `PROGRESS.md`, `sandbox/Makefile`, `sandbox/include/runner.h`, `sandbox/src/cgroup.c`, `sandbox/src/runner.c`, `sandbox/src/seccomp.c`, `sandbox/tests/fixtures/*`, `sandbox/tests/test_runner.sh`.
- Linked: LOG-0010.
- Status: Phase 1 runtime DoD remains unverified until the full suite runs with a writable delegated cgroup v2 subtree. Do not mark Phase 1 complete based on the build or fail-closed test alone.
- Tutor review: pending


## [LOG-0012] 2026-11-17 15:45 IST | PLAN | actor: agent
- Goal: Remediate Phase 1 verification findings: enforce memory containment under active host swap (set and verify memory.swap.max = 0) and implement automated delegated cgroup v2 subtree discovery in the test harness.
- Reason: On hosts with swap enabled, untrusted processes paged to swap and bypassed memory.max; and test_runner.sh defaulted to unwritable /sys/fs/cgroup rather than discovering available systemd user delegations.
- Steps:
  1. In `sandbox/src/cgroup.c`: configure `memory.swap.max = 0` in `cgroup_init()`, verify write by reading back, and fail closed if host has active swap that cannot be constrained; expand `cgroup_oom_kill_count()` to inspect both `oom_kill` and `oom_group_kill` from `memory.events` and `memory.events.local`.
  2. In `sandbox/src/runner.c`: preserve `term_signal` when OOM killer issues `SIGKILL`.
  3. In `sandbox/tests/test_runner.sh`: implement automated cgroup v2 discovery inspecting `/proc/self/cgroup` ancestors and systemd user services, validating required controllers (`cpu`, `memory`, `pids`), and probing directory write/cleanup permissions while preserving `PES2_CGROUP_ROOT` as an override; add focused tests for discovery and no-delegation fallback.
  4. Run clean compilation, memory regression test, test suite, append-only hook test, hygiene check, and diff check.
- Files: `PROGRESS.md`, `sandbox/src/cgroup.c`, `sandbox/src/runner.c`, `sandbox/tests/test_runner.sh`.
- Linked: LOG-0011.
- Tutor review: pending


## [LOG-0013] 2026-11-17 16:04 IST | DONE | actor: agent
- Goal: Remediate Phase 1 verification findings: enforce memory containment under active host swap and implement automated delegated cgroup v2 subtree discovery in the test harness.
- Outcome:
  1. Configured `memory.swap.max = 0` in `cgroup_init()`, verified write by reading back value, and failed closed if active host swap exists but cannot be constrained in the cgroup.
  2. Enhanced `cgroup_oom_kill_count()` to read both `oom_kill` and `oom_group_kill` from `memory.events` and `memory.events.local`.
  3. Preserved `term_signal` in `runner.c` when child is terminated by OOM killer (`SIGKILL`, signal 9).
  4. Added `sandbox/tests/discover_cgroup.sh` and updated `sandbox/tests/test_runner.sh` to auto-discover user-delegated cgroup v2 subtrees (inspecting `/proc/self/cgroup` ancestors and systemd user services with `cpu`, `memory`, `pids`), validate controllers, and probe write/cleanup permissions while preserving `PES2_CGROUP_ROOT` as an override.
  5. Added `sandbox/tests/test_cgroup_discovery.sh` verifying invalid overrides, dummy missing controllers, auto-discovery, and fail-closed handling.
  6. Verified that `MEMORY_LIMIT` test fixture triggers `SIGKILL` (signal 9) and exits status 4 (`STATUS_MEMORY_LIMIT`).
- Verification:
  - `make -C sandbox clean all`: passed cleanly with `-Wall -Wextra -Werror -O2`.
  - `bash sandbox/tests/test_cgroup_discovery.sh`: 4/4 passed.
  - `make -C sandbox test`: all tests passed (including all 10 runtime isolation tests when executed with user delegated cgroup).
  - `bash scripts/test_append_only.sh`: 7/7 passed.
  - `bash scripts/check_repo_hygiene.sh`: passed.
  - `bash scripts/check_progress_append_only.sh`: passed.
  - `git diff --check`: passed.
- Files: `PROGRESS.md`, `sandbox/Makefile`, `sandbox/src/cgroup.c`, `sandbox/src/runner.c`, `sandbox/tests/discover_cgroup.sh`, `sandbox/tests/test_cgroup_discovery.sh`, `sandbox/tests/test_runner.sh`.
- Linked: LOG-0012.
- Tutor review: pending


## [LOG-0014] 2026-11-17 17:28 IST | PLAN | actor: agent
- Goal: Make test suite reporting unambiguous when runtime isolation tests are skipped, clearly distinguish discovery unit tests from runtime verification, add a regression test for the no-delegation case, and add strict runtime targets.
- Reason: When run in environments without cgroup delegation or user namespace capabilities, runtime isolation tests are skipped; however, discovery tests previously outputted an unqualified "SUCCESS: All focused cgroup discovery tests PASSED!", creating ambiguity where CI or a reader might mistake a build or discovery pass for complete Phase 1 runtime verification.
- Steps:
  1. In `sandbox/tests/test_cgroup_discovery.sh`: clarify test summary to specify it covers discovery unit tests only; add Test 5 exercising the no-delegation case (validating that discovery fails, `test_runner.sh` skips with explicit warning banners and does not claim full pass, and fails with exit code 1 under `PES2_REQUIRE_RUNTIME=1`).
  2. In `sandbox/tests/test_runner.sh`: emit prominent, unambiguous summary banners distinguishing fail-closed unit checks from runtime isolation checks; display clear warnings when runtime tests are skipped stating that Phase 1 DoD is not met; support `PES2_REQUIRE_RUNTIME=1` to fail closed for CI when runtime tests cannot execute; emit detailed checklist upon full pass.
  3. In `sandbox/Makefile`: add `test-runtime` target setting `PES2_REQUIRE_RUNTIME=1` for strict CI/host runtime verification alongside `test`.
  4. Run clean build, focused discovery tests, sandbox test suite, append-only hook test, hygiene check, and diff check.
- Files: `PROGRESS.md`, `sandbox/Makefile`, `sandbox/tests/test_cgroup_discovery.sh`, `sandbox/tests/test_runner.sh`.
- Linked: LOG-0013.
- Expected: Ambiguity eliminated; skips are visibly and loudly flagged; regression test validates no-delegation behavior; runtime tests remain fully runnable when valid delegation is present.
- Tutor review: pending


## [LOG-0015] 2026-11-17 17:38 IST | DONE | actor: agent
- Goal: Make test suite reporting unambiguous when runtime isolation tests are skipped, clearly distinguish discovery unit tests from runtime verification, add a regression test for the no-delegation case, and add strict runtime targets.
- Outcome:
  1. Updated `sandbox/tests/test_cgroup_discovery.sh` to remove misleading unqualified "SUCCESS" banner; explicitly labeled summary as unit checks for cgroup discovery and fallback helpers only.
  2. Added Test 5 to `sandbox/tests/test_cgroup_discovery.sh`: automated regression test proving that when no delegated cgroup subtree exists, `test_runner.sh` explicitly reports `RUNTIME ISOLATION SKIPPED`, emits a prominent warning that Phase 1 DoD is not met, and does not claim full pass.
  3. Added Test 5b to `sandbox/tests/test_cgroup_discovery.sh`: automated regression test proving that strict mode (`PES2_REQUIRE_RUNTIME=1`) fails closed with non-zero exit when runtime isolation cannot execute.
  4. Updated `sandbox/tests/test_runner.sh` to print prominent summary banners clearly distinguishing fail-closed unit checks (PASSED) from runtime isolation checks (SKIPPED or PASSED), warning prominently that Phase 1 DoD is unverified when skipped, and printing a complete 10-fixture checklist upon full pass.
  5. Added `test-runtime` target to `sandbox/Makefile` enabling strict CI and automated verification where missing runtime isolation fails the build.
- Verification:
  - `make -C sandbox clean all`: passed cleanly with `-Wall -Wextra -Werror -O2`.
  - `bash sandbox/tests/test_cgroup_discovery.sh`: 5/5 passed (including regression tests for no-delegation fallback and strict failure).
  - `make -C sandbox test`: fail-closed check passed (1/1); discovery checks passed (5/5); runtime isolation reported as SKIPPED with clear warning notice and reason (namespace restriction inside sandbox subshell).
  - `make -C sandbox test-runtime`: failed closed with exit code 2 and explicit error as required when runtime isolation cannot run.
  - `bash scripts/test_append_only.sh`: 7/7 passed.
  - `cd /home/zogratis/Projects/Personal\ Projects/polyexecution-studio2 && bash scripts/check_repo_hygiene.sh`: passed.
  - `cd /home/zogratis/Projects/Personal\ Projects/polyexecution-studio2 && bash scripts/check_progress_append_only.sh`: passed.
  - `git diff --check`: passed with zero whitespace or conflict errors.
- Files: `PROGRESS.md`, `sandbox/Makefile`, `sandbox/tests/test_cgroup_discovery.sh`, `sandbox/tests/test_runner.sh`.
- Linked: LOG-0014.
- Status: Phase 1 runtime DoD remains UNVERIFIED in environments lacking delegated cgroups or unprivileged user namespaces. Skips are now loudly and unambiguously reported.
- Tutor review: pending


## [LOG-0016] 2026-11-17 17:57 IST | PLAN | actor: agent
- Goal: Record Piyush's host execution and end-to-end verification of the Phase 1 C sandbox runner with strict cgroups v2 and namespace isolation.
- Reason: Piyush independently executed the full strict test suite on the host environment with delegated cgroup v2 subtree and user namespace capabilities, closing the Phase 1 runtime isolation verification requirement.
- Steps:
  1. Record host verification results in PROGRESS.md linking to LOG-0015.
  2. Run append-only verification suite (`scripts/test_append_only.sh`) and checker script (`scripts/check_progress_append_only.sh`).
  3. Run repository hygiene (`scripts/check_repo_hygiene.sh`) and diff check (`git diff --check`).
- Files: `PROGRESS.md`.
- Linked: LOG-0015.
- Expected: Progress history accurately reflects the owner's host verification run without exceeding reported results; Phase 1 ready for tutor review while remaining unmerged.
- Tutor review: pending


## [LOG-0017] 2026-11-17 17:58 IST | DONE | actor: Piyush
- Goal: Record Piyush's host execution and end-to-end verification of the Phase 1 C sandbox runner with strict cgroups v2 and namespace isolation.
- Outcome:
  1. Piyush executed the strict host test suite against the delegated systemd user cgroup subtree (`/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service`).
  2. Strict compilation passed with `-Wall -Wextra -Werror -O2`.
  3. All focused cgroup v2 discovery and fallback unit checks passed.
  4. The runtime suite executed with `PES2_REQUIRE_RUNTIME=1` (`make -C sandbox test-runtime`), achieving FULL PASS across all 10/10 isolation cases:
     - Normal exit (`OK`)
     - Nonzero exit (`RUNTIME_ERROR`)
     - Wall timeout (`TIMEOUT`)
     - Output cap (`OUTPUT_LIMIT`: exact 64 KiB cap)
     - Memory containment (`MEMORY_LIMIT`: SIGKILL 9, no swap paging)
     - PID limit (`OK`: `PIDS_LIMIT_REACHED`)
     - Fork bomb containment (`TIMEOUT`: `pids.max` containment)
     - Network denial (`BLOCKED_BY_POLICY`)
     - Filesystem isolation (`OK`: private rootfs)
     - Process cancellation and cgroup cleanup (`CANCELLED`: zero leftover cgroups or processes).
  5. Cleaned up all build artifacts with `make -C sandbox clean`.
  6. Phase 1 runtime test blocker is closed and implementation is ready for tutor review. Branch remains unmerged on `phase/1-sandbox-engine`.
- Verification:
  - `make -C sandbox clean all`: passed.
  - `bash sandbox/tests/test_cgroup_discovery.sh`: passed (located `/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service`).
  - `make -C sandbox test-runtime`: FULL PASS (10/10 runtime fixtures verified; zero leftover cgroups or processes).
  - `make -C sandbox clean`: passed.
  - `bash scripts/test_append_only.sh`: 7/7 passed.
  - `cd /home/zogratis/Projects/Personal\ Projects/polyexecution-studio2 && bash scripts/check_repo_hygiene.sh`: passed.
  - `cd /home/zogratis/Projects/Personal\ Projects/polyexecution-studio2 && bash scripts/check_progress_append_only.sh`: passed.
  - `git diff --check`: passed.
- Files: `PROGRESS.md`.
- Linked: LOG-0016.
- Status: Phase 1 sandbox runtime verification complete. Ready for tutor review; branch kept unmerged on `phase/1-sandbox-engine`.
- Tutor review: pending


## [LOG-0018] 2026-11-17 20:27 IST | PLAN | actor: agent
- Goal: Implement Phase 2 typed RuntimeAdapter and RuntimeRegistry engine with truthful milestones, isolated compilation and execution pipelines for C, C++, and Python 3, and stale artifact prevention.
- Reason: docs/04_BUILD_PLAN.md Phase 2 requires typed adapters, Stage 1 compile and Stage 2 execute pipelines for C/C++, single-stage pipeline for Python 3, truthful milestone events, explicit executable paths/argv vectors, and verified cleanup.
- Steps:
  1. Add Python bytecode patterns (__pycache__/, *.pyc) to .gitignore to avoid unstaged junk.
  2. Implement engine/models.py defining LanguageId, RuntimeFamily, Milestone, ResourceLimits, CompileSpec, ExecutionSpec, SeccompPolicyProfile, CleanupSpec, ExecutionRequest, MilestoneEvent, and ExecutionResult.
  3. Implement engine/registry.py defining RuntimeRegistry with Tier 1 adapters for C (gcc), C++ (clang++), and Python 3 (python3) using explicit absolute executable paths and immutable argv vectors.
  4. Implement engine/runner.py defining RunnerInterface with NativeSandboxRunner (calling sandbox/pes2-runner) and MockSandboxRunner for hermetic unit testing.
  5. Implement engine/pipeline.py defining ExecutionPipeline enforcing ephemeral workspace isolation, truthful milestone sequences (ACCEPTED -> COMPILING -> RUNNING -> COMPLETED or COMPILE_ERROR for C/C++; ACCEPTED -> RUNNING -> COMPLETED for Python 3), guaranteed cleanup, and stale artifact defense.
  6. Implement automated unit tests in tests/test_registry.py and tests/test_pipeline.py covering registration, explicit arguments, truthful milestones, compile failure isolation, stale artifact defense, and workspace cleanup.
  7. Run test suites, append-only verification, repo hygiene checks, and git diff checks.
- Files: .gitignore, PROGRESS.md, engine/__init__.py, engine/models.py, engine/registry.py, engine/runner.py, engine/pipeline.py, tests/__init__.py, tests/test_registry.py, tests/test_pipeline.py.
- Linked: LOG-0017.
- Expected: All unit tests pass cleanly; milestones are emitted truthfully without synthetic COMPILING for Python 3; compilers and runners use explicit paths; zero orphaned files or leaked processes; hygiene checks pass.
- Tutor review: pending


## [LOG-0019] 2026-11-17 20:55 IST | DONE | actor: agent
- Goal: Implement Phase 2 typed RuntimeAdapter and RuntimeRegistry engine with truthful milestones, isolated compilation and execution pipelines for C, C++, and Python 3, and stale artifact prevention.
- Outcome:
  1. Updated `.gitignore` to ignore Python bytecode artifacts (`__pycache__/`, `*.pyc`, `.pytest_cache/`).
  2. Implemented `engine/models.py` with strongly typed enum and dataclass representations for `LanguageId`, `RuntimeFamily`, `Milestone`, `ResourceLimits`, `CompileSpec`, `ExecutionSpec`, `SeccompPolicyProfile`, `CleanupSpec`, `ExecutionRequest`, `MilestoneEvent`, `RunnerResult`, and `ExecutionResult`.
  3. Implemented `engine/registry.py` registering Tier 1 runtime adapters:
     - C (`LanguageId.C`): `gcc 14` official, `/usr/bin/gcc` installed, 2-stage compile (`-O2`, `-std=gnu11`, `-Wall`, `-Wextra`, `-Werror`) to `/workspace/a.out` and bounded execution with `STRICT_NATIVE` seccomp.
     - C++ (`LanguageId.CPP`): `clang 19` official, `/usr/bin/clang++` (or `/usr/bin/g++`) installed, 2-stage compile (`-O2`, `-std=c++20`, `-Wall`, `-Wextra`, `-Werror`) to `/workspace/a.out` and bounded execution.
     - Python 3 (`LanguageId.PYTHON3`): `Python 3.14` official, `/usr/bin/python3` installed, single-stage interpreted execution (`compile_spec=None`) with `/usr/bin/python3 /workspace/solution.py`.
  4. Implemented `engine/runner.py` with `RunnerInterface`, `NativeSandboxRunner` (parsing `pes2-runner` output and exit statuses), and `MockSandboxRunner` for hermetic testing.
  5. Implemented `engine/pipeline.py` enforcing:
     - Ephemeral workspace isolation (`/tmp/pes2-work-XXXXXX` mode `0700`) with guaranteed cleanup on all exit paths.
     - Truthful milestone sequence for compiled runtimes: `ACCEPTED -> COMPILING -> RUNNING -> COMPLETED` (or `COMPILE_ERROR`).
     - Truthful milestone sequence for interpreted runtimes: `ACCEPTED -> RUNNING -> COMPLETED` without synthetic `COMPILING`.
     - Compile failure isolation: terminates immediately upon compile failure; `RUNNING` is never emitted.
     - Stale artifact defense: removes pre-existing or planted artifacts before compile and upon compile failure to ensure broken builds never execute stale binaries.
  6. Added top-level `Makefile` with `test`, `test-engine`, `check`, and `clean` targets.
  7. Implemented unit test suite in `tests/test_registry.py` (5 tests), `tests/test_pipeline.py` (9 tests), and `tests/test_runner.py` (3 tests) covering adapter contracts, explicit argv vectors, truthful milestones, stale artifact defense, and workspace cleanup.
- Verification:
  - `python3 -m unittest discover -s tests -p "test_*.py" -v`: 17/17 tests passed in 0.652s.
  - `make -C sandbox test`: fail-closed check passed (1/1); discovery checks passed (5/5); runtime isolation reported as SKIPPED with clear warning notice as expected inside container subshell.
  - `bash scripts/test_append_only.sh`: 7/7 passed.
  - `bash scripts/check_progress_append_only.sh`: passed.
  - `bash scripts/check_repo_hygiene.sh`: passed.
  - `git diff --check`: passed with zero whitespace or conflict errors.
- Files: `.gitignore`, `Makefile`, `PROGRESS.md`, `engine/__init__.py`, `engine/models.py`, `engine/pipeline.py`, `engine/registry.py`, `engine/runner.py`, `tests/__init__.py`, `tests/test_pipeline.py`, `tests/test_registry.py`, `tests/test_runner.py`.
- Linked: LOG-0018.
- Status: Phase 2 vertical slice engine implemented on branch `phase/2-runtime-adapters`. Phase 1 tutor review remains pending. Tier A sandbox C code untouched.
- Tutor review: pending


## [LOG-0020] 2026-11-17 21:08 IST | PLAN | actor: agent
- Goal: Fix Tier B pipeline correctness gaps: missing artifact check upon compiler zero exit, max_output_bytes enforcement/rejection against runner 64 KiB cap, and pre-write source size bounding.
- Reason: Tutor review identified: (1) if compilation exits 0 but produces no artifact, the pipeline improperly continued to execution; (2) custom max_output_bytes was ignored by the runner interface without software cap or validation; (3) unconstrained source code could consume host temporary storage. Tier A sandbox C code must not be modified.
- Steps:
  1. Define MAX_SOURCE_BYTES (65536) and MAX_RUNNER_OUTPUT_BYTES (65536) in engine/models.py.
  2. In engine/pipeline.py:
     - Check UTF-8 byte length of request.source_code against MAX_SOURCE_BYTES before provisioning or writing to host workspace; reject oversized inputs with SANDBOX_ERROR.
     - Validate requested max_output_bytes: if custom limit exceeds MAX_RUNNER_OUTPUT_BYTES, reject early with clear error describing the runner cap.
     - In Stage 1 compilation, verify the expected artifact file exists on disk even if compiler returned exit 0; if missing, fail closed as COMPILE_ERROR and ensure Stage 2 execution is never started.
     - In Stage 2 execution, enforce the requested max_output_bytes if less than runner 64 KiB cap by truncating stdout/stderr and marking OUTPUT_LIMIT when output length exceeds limit.
  3. Update tests/test_pipeline.py:
     - Update C success mock to simulate artifact generation.
     - Add regression test for zero-exit compile with missing artifact (asserts COMPILE_ERROR and zero RUNNING emissions).
     - Add regression test for custom max_output_bytes enforcement (truncation and OUTPUT_LIMIT milestone) and rejection of limits > 65536 bytes.
     - Add regression test for oversized source code rejection before workspace write.
  4. Run unit tests, append-only verification, repo hygiene checks, and git diff checks.
- Files: PROGRESS.md, engine/models.py, engine/pipeline.py, tests/test_pipeline.py.
- Linked: LOG-0019.
- Expected: All unit tests pass; missing artifact results in COMPILE_ERROR; max_output_bytes strictly honored or rejected; oversized source rejected before disk write; zero Tier A files modified; Phase 1 tutor review remains pending.
- Tutor review: pending


## [LOG-0021] 2026-11-17 21:29 IST | DONE | actor: agent
- Goal: Fix Tier B pipeline correctness gaps: missing artifact check upon compiler zero exit, max_output_bytes enforcement/rejection against runner 64 KiB cap, and pre-write source size bounding.
- Outcome:
  1. Defined `MAX_SOURCE_BYTES = 65536` (64 KiB) and `MAX_RUNNER_OUTPUT_BYTES = 65536` (64 KiB) in `engine/models.py`.
  2. In `engine/pipeline.py`:
     - Added pre-workspace validation: UTF-8 source byte length is checked against `MAX_SOURCE_BYTES` before creating any temporary workspace on the host; oversized inputs are rejected immediately with `SANDBOX_ERROR`.
     - Added output limit validation: if custom `max_output_bytes` exceeds `MAX_RUNNER_OUTPUT_BYTES` (64 KiB runner pipe cap), the request is rejected with `SANDBOX_ERROR` and a clear explanatory message.
     - Enhanced Stage 1 compilation verification: if the compiler exits with 0 and `OK` status but fails to produce the expected artifact binary, the pipeline immediately fails closed with `COMPILE_ERROR`, purging any partial state and guaranteeing Stage 2 execution (`RUNNING`) never starts.
     - Implemented software output bounding in Stage 2 execution: if total stdout/stderr exceeds a requested `max_output_bytes` (<= 64 KiB), output is truncated to the requested cap and the final milestone is mapped to `OUTPUT_LIMIT`.
  3. In `tests/test_pipeline.py`:
     - Added `test_c_compile_success_without_artifact_fails_closed` proving zero-exit compile without artifact fails as `COMPILE_ERROR` and never emits `RUNNING`.
     - Added `test_oversized_source_rejected_before_workspace` verifying oversized source rejection without creating temporary directories.
     - Added `test_unsupported_max_output_bytes_rejected` verifying rejection of limits exceeding runner capability.
     - Added `test_custom_max_output_bytes_software_truncation` verifying software output bounding and `OUTPUT_LIMIT` milestone.
     - Updated `test_c_success_milestones` with `ArtifactCreatingMockRunner` simulating realistic compiler artifact creation.
  4. Preserved Tier A boundary: zero modifications made to `sandbox/src/seccomp.c`, `sandbox/src/runner.c`, or other Tier A files. Compilation through the live runner remains marked blocked pending Piyush's authorization of a compiler-specific seccomp profile.
- Verification:
  - `python3 -m unittest discover -s tests -p "test_*.py" -v`: 21/21 tests passed in 0.894s.
  - `bash scripts/test_append_only.sh`: 7/7 passed.
  - `bash scripts/check_progress_append_only.sh`: passed.
  - `bash scripts/check_repo_hygiene.sh`: passed.
  - `git diff --check`: passed with zero errors.
- Files: `PROGRESS.md`, `engine/models.py`, `engine/pipeline.py`, `tests/test_pipeline.py`.
- Linked: LOG-0020.
- Status: Tier B correctness gaps resolved on `phase/2-runtime-adapters`. Phase 1 tutor review remains pending. Real compiler execution blocked on Tier A seccomp `wait4` authorization.
- Tutor review: pending


## [LOG-0022] 2026-11-17 22:01 IST | PLAN | actor: agent
- Goal: Add CLI runner entrypoint (`engine/cli.py`) with structured JSON telemetry emission and automated CLI tests.
- Reason: Provides an executable interface for invoking the Phase 2 runtime adapter pipeline from the shell, inspecting truthful milestone events, and testing requests end-to-end without requiring manual harness scripts.
- Steps:
  1. Implement `engine/cli.py` parsing `--language`, `--source-file`, `--source`, `--stdin`, `--wall-ms`, `--cpu-ms`, `--memory-kb`, `--pids`, and `--max-output-bytes`.
  2. Emit execution results as structured JSON containing milestones, exit code, signal, kernel timings, memory, stdout, and stderr.
  3. Implement `tests/test_cli.py` testing argument validation, JSON formatting, milestone serialization, and error exit codes.
  4. Run test suites, append-only verification, repo hygiene checks, and git diff checks.
- Files: PROGRESS.md, engine/cli.py, tests/test_cli.py.
- Linked: LOG-0021.
- Expected: CLI works seamlessly with both mock and native runners; structured JSON output conforms to architecture schema; all tests pass; hygiene rules preserved.
- Tutor review: pending


## [LOG-0023] 2026-11-17 22:08 IST | DONE | actor: agent
- Goal: Add CLI runner entrypoint (`engine/cli.py`) with structured JSON telemetry emission and automated CLI tests.
- Outcome:
  1. Implemented `engine/cli.py` providing a CLI entrypoint for invoking the runtime adapter pipeline with explicit flags (`--language`, `--source`, `--source-file`, `--stdin`, `--wall-ms`, `--cpu-ms`, `--memory-kb`, `--pids`, `--max-output-bytes`, `--runner`, `--cgroup-root`).
  2. Structured JSON output formats request metadata, truthful milestone sequences, exit codes, signals, kernel timings, memory usage, stdout, stderr, and compiler diagnostics.
  3. Added `tests/test_cli.py` (5 tests) covering Python execution success, source-file input, invalid language rejection, missing source handling, and unsupported output cap rejection.
  4. Preserved Tier A boundary: zero changes made to `sandbox/src/` or C runner files.
- Verification:
  - `python3 -m unittest discover -s tests -p "test_*.py" -v`: 26/26 tests passed in 1.046s.
  - `bash scripts/test_append_only.sh`: 7/7 passed.
  - `bash scripts/check_progress_append_only.sh`: passed.
  - `bash scripts/check_repo_hygiene.sh`: passed.
  - `git diff --check`: passed with zero whitespace or conflict errors.
- Files: `PROGRESS.md`, `engine/cli.py`, `tests/test_cli.py`.
- Linked: LOG-0022.
- Status: CLI entrypoint implemented on `phase/2-runtime-adapters`. Phase 1 tutor review remains pending. Real compiler execution blocked on Tier A seccomp `wait4` authorization.
- Tutor review: pending


## [LOG-0024] 2026-11-17 22:27 IST | PLAN | actor: agent
- Goal: Harden Tier B CLI input handling: bounded source file/stdin reading (MAX_SOURCE_BYTES + 1), UTF-8 decoding validation, positive/runner-bounded resource limit validation, and adapter-default limit preservation.
- Reason: Tutor review identified: (1) unbounded file/pipe reads risk host memory exhaustion before size checks; (2) unvalidated resource limits could pass invalid or negative values to runners; (3) overriding a single limit erroneously replaced all other limits with generic defaults instead of the adapter's tuned values.
- Steps:
  1. Define runner capability bounds in engine/models.py matching runner.h: RUNNER_MIN_TIMEOUT_MS (1), RUNNER_MAX_TIMEOUT_MS (60000), RUNNER_MIN_MEMORY_KB (16384), RUNNER_MAX_MEMORY_KB (1048576), RUNNER_MAX_PIDS (128).
  2. In engine/cli.py:
     - Implement bounded binary reading functions for --source-file and stdin reading at most MAX_SOURCE_BYTES + 1 bytes.
     - Reject oversized file/stream inputs immediately with clear error without loading full inputs into memory.
     - Handle invalid UTF-8 byte sequences and OS read errors with clear error messages and non-zero exit.
     - Validate custom resource limits against runner bounds; reject negative, zero, or out-of-range values.
     - When overriding resource limits, inherit unspecified limits from the target adapter's default_resource_limits rather than hardcoded generic values.
  3. In tests/test_cli.py:
     - Add tests for oversized source file rejection via bounded read.
     - Add tests for oversized stdin stream rejection via bounded read.
     - Add tests for invalid UTF-8 in file and stdin.
     - Add tests for invalid limits (negative wall-ms, wall-ms > 60000, memory-kb < 16384, memory-kb > 1048576, pids <= 0, max-output-bytes <= 0).
     - Add test for single-limit override verifying preservation of adapter defaults.
  4. Run test suites, append-only verification, repo hygiene checks, and git diff checks.
- Files: PROGRESS.md, engine/models.py, engine/cli.py, tests/test_cli.py.
- Linked: LOG-0023.
- Expected: All 31+ tests pass; memory-bounded input reads enforced; runner capabilities validated; adapter defaults preserved; Tier A files untouched.
- Tutor review: pending


## [LOG-0025] 2026-11-17 22:54 IST | DONE | actor: agent
- Goal: Harden Tier B CLI input handling: bounded source file/stdin reading (MAX_SOURCE_BYTES + 1), UTF-8 decoding validation, positive/runner-bounded resource limit validation, and adapter-default limit preservation.
- Outcome:
  1. Defined runner capability constants in `engine/models.py`: `RUNNER_MIN_TIMEOUT_MS = 1`, `RUNNER_MAX_TIMEOUT_MS = 60000`, `RUNNER_MIN_MEMORY_KB = 16384` (16 MiB), `RUNNER_MAX_MEMORY_KB = 1048576` (1 GiB), `RUNNER_MIN_PIDS = 1`, `RUNNER_MAX_PIDS = 128`.
  2. In `engine/cli.py`:
     - Implemented `read_bounded_source_file` and `read_bounded_source_stream` reading at most `MAX_SOURCE_BYTES + 1` bytes in binary mode; rejects oversized source inputs immediately without buffering full files or streams into memory.
     - Added UTF-8 decode error handling for file and standard input reads with clear diagnostic messages and exit code 1.
     - Implemented `validate_resource_limits` checking custom resource limits against runner bounds (`wall_ms`, `cpu_ms`, `memory_kb`, `pids`, `max_output_bytes`); rejects negative, zero, or out-of-range limits safely.
     - When custom limits are provided, unspecified limits now inherit from the target adapter's `default_resource_limits` (e.g. Python 3's 128 MiB memory limit and 2000 ms CPU ceiling) rather than substituting generic defaults.
  3. In `tests/test_cli.py`:
     - Added tests for oversized source file rejection via bounded read.
     - Added tests for oversized stdin stream rejection via bounded read.
     - Added tests for invalid UTF-8 byte sequences in file and stdin inputs.
     - Added tests for invalid limits (`wall_ms`, `memory_kb`, `pids`, `max_output_bytes`).
     - Added test verifying adapter-default preservation when overriding a single limit (`--wall-ms 4500` preserves Python 3's 128 MiB memory and 2000 ms CPU defaults).
  4. Preserved Tier A boundary: zero changes made to `sandbox/src/` or C runner files.
- Verification:
  - `python3 -m unittest discover -s tests -p "test_*.py" -v`: 34/34 tests passed in 0.980s.
  - `bash scripts/test_append_only.sh`: 7/7 passed.
  - `bash scripts/check_progress_append_only.sh`: passed.
  - `bash scripts/check_repo_hygiene.sh`: passed.
  - `git diff --check`: passed with zero whitespace or conflict errors.
- Files: `PROGRESS.md`, `engine/models.py`, `engine/cli.py`, `tests/test_cli.py`.
- Linked: LOG-0024.
- Status: Tier B CLI input validation and bounded reads completed on `phase/2-runtime-adapters`. Phase 1 tutor review remains pending. Real compiler execution blocked on Tier A seccomp `wait4` authorization.
- Tutor review: pending
