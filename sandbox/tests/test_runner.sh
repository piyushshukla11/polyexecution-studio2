#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SANDBOX="$ROOT/sandbox"
TMP_DIR="$(mktemp -d /tmp/pes2-runner-tests-XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT
chmod 700 "$TMP_DIR"

pass() { echo "  [PASS] $1"; }
fail() { echo "  [FAIL] $1" >&2; exit 1; }

for source in "$SANDBOX"/tests/fixtures/*.c; do
    name="$(basename "$source" .c)"
    cc -Wall -Wextra -Werror -O2 "$source" -o "$TMP_DIR/$name"
    chmod 700 "$TMP_DIR/$name"
done

# The runner must refuse to launch a target when its required cgroup cannot be created.
set +e
FAIL_CLOSED_OUT=$("$SANDBOX/pes2-runner" --cgroup-root "$TMP_DIR/no-such-cgroup-root" \
    --workspace "$TMP_DIR" -- /workspace/marker 2>&1)
FAIL_CLOSED_STATUS=$?
set -e
if [ "$FAIL_CLOSED_STATUS" -eq 6 ] && [[ "$FAIL_CLOSED_OUT" == *"status=SANDBOX_ERROR"* ]] &&
   [ ! -e "$TMP_DIR/should-not-run" ]; then
    pass "Unavailable cgroup fails closed before target execution"
else
    fail "Runner did not fail closed (status=$FAIL_CLOSED_STATUS, output=$FAIL_CLOSED_OUT)"
fi

CGROUP_ROOT="${PES2_CGROUP_ROOT:-/sys/fs/cgroup}"
if [ ! -w "$CGROUP_ROOT/cgroup.subtree_control" ]; then
    echo "  [SKIP] Runtime isolation cases require a writable delegated cgroup v2 subtree ($CGROUP_ROOT)"
    echo "SUCCESS: fail-closed test passed; runtime cases skipped because cgroup delegation is unavailable."
    exit 0
fi

run_case() {
    local name="$1" expected_status="$2" target="$3" wall_ms="$4" cpu_ms="$5"
    shift 5
    local out="$TMP_DIR/$name.stdout" err="$TMP_DIR/$name.stderr" status
    set +e
    "$SANDBOX/pes2-runner" --cgroup-root "$CGROUP_ROOT" --workspace "$TMP_DIR" \
        --wall-ms "$wall_ms" --cpu-ms "$cpu_ms" "$@" -- "$target" >"$out" 2>"$err"
    status=$?
    set -e
    [ "$status" -eq "$expected_status" ] || fail "$name returned $status, expected $expected_status: $(cat "$err")"
    grep -q "PES2_RESULT status=$name" "$err" || fail "$name status event missing: $(cat "$err")"
    pass "$name"
}

run_case OK 0 /workspace/clean_exit 2000 1000
[ "$(cat "$TMP_DIR/OK.stdout")" = "sandbox-ok" ] || fail "clean stdout mismatch"
run_case RUNTIME_ERROR 1 /workspace/nonzero_exit 2000 1000
run_case TIMEOUT 2 /workspace/timeout 300 1000
run_case OUTPUT_LIMIT 3 /workspace/output_flood 3000 1000
[ "$(wc -c < "$TMP_DIR/OUTPUT_LIMIT.stdout" | tr -d ' ')" -eq 65536 ] || fail "output cap was not exactly 65536 bytes"
run_case MEMORY_LIMIT 4 /workspace/memory_bomb 3000 1000 --memory-kb 65536
run_case OK 0 /workspace/pids_limit 3000 1000 --pids 8
grep -q "PIDS_LIMIT_REACHED" "$TMP_DIR/OK.stdout" || fail "pids.max did not contain fork creation"
run_case TIMEOUT 2 /workspace/fork_bomb 1500 1000 --pids 8
run_case BLOCKED_BY_POLICY 5 /workspace/network_attempt 2000 1000
run_case OK 0 /workspace/filesystem_snoop 2000 1000

echo "Test cancellation cleanup..."
set +e
"$SANDBOX/pes2-runner" --cgroup-root "$CGROUP_ROOT" --workspace "$TMP_DIR" \
    --wall-ms 10000 --cpu-ms 9000 -- /workspace/timeout >"$TMP_DIR/cancel.stdout" 2>"$TMP_DIR/cancel.stderr" &
runner_pid=$!
sleep 0.1
kill -TERM "$runner_pid"
wait "$runner_pid"
cancel_status=$?
set -e
[ "$cancel_status" -eq 7 ] || fail "cancellation returned $cancel_status, expected 7: $(cat "$TMP_DIR/cancel.stderr")"
grep -q "PES2_RESULT status=CANCELLED" "$TMP_DIR/cancel.stderr" || fail "cancellation result status missing"
pass "SIGTERM cancels the run and cleans its cgroup"

leftovers=$(find "$CGROUP_ROOT" -maxdepth 1 -type d -name 'pes2-*' -print 2>/dev/null | wc -l | tr -d ' ')
[ "$leftovers" -eq 0 ] || fail "runner cgroups remain after tests: $leftovers"

 echo "SUCCESS: all runner runtime cases passed with delegated cgroups."
