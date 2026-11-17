#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SANDBOX="$ROOT/sandbox"

pass() { echo "  [PASS] $1"; }
fail() { echo "  [FAIL] $1" >&2; exit 1; }

echo "========================================================================"
echo "Running Focused Cgroup v2 Discovery & Fallback Tests"
echo "========================================================================"

# Source discovery function from test_runner.sh
# We extract discover_cgroup_root logic into a reusable helper
DISCOVERY_SCRIPT="$SANDBOX/tests/discover_cgroup.sh"

# Test 1: Explicit invalid override must fail discovery
echo "Test 1: Explicit invalid cgroup root override fails validation..."
set +e
OUT=$(PES2_CGROUP_ROOT="/tmp/nonexistent-cgroup-root-$$" bash "$DISCOVERY_SCRIPT" 2>&1)
STATUS=$?
set -e
if [ "$STATUS" -ne 0 ] || [ -z "$OUT" ]; then
    pass "Invalid PES2_CGROUP_ROOT override was correctly rejected"
else
    fail "Invalid override was unexpectedly accepted as: $OUT"
fi

# Test 2: Unwritable dummy directory override must fail validation
echo "Test 2: Dummy directory without controllers fails validation..."
DUMMY_DIR=$(mktemp -d /tmp/pes2-dummy-cgroup-XXXXXX)
trap 'rm -rf "$DUMMY_DIR"' EXIT
set +e
OUT=$(PES2_CGROUP_ROOT="$DUMMY_DIR" bash "$DISCOVERY_SCRIPT" 2>&1)
STATUS=$?
set -e
if [ "$STATUS" -ne 0 ] || [ -z "$OUT" ]; then
    pass "Directory missing cgroup controllers was correctly rejected"
else
    fail "Dummy directory was unexpectedly accepted as: $OUT"
fi

# Test 3: Auto-discovery when PES2_CGROUP_ROOT is unset
echo "Test 3: Auto-discovery discovers delegated user slice if available..."
set +e
DISCOVERED=$(unset PES2_CGROUP_ROOT && bash "$DISCOVERY_SCRIPT" 2>/dev/null)
STATUS=$?
set -e
if [ "$STATUS" -eq 0 ] && [ -n "$DISCOVERED" ] && [ -d "$DISCOVERED" ]; then
    pass "Auto-discovery located writable subtree: $DISCOVERED"
else
    echo "  [INFO] Auto-discovery returned no writable delegation in this subshell (status=$STATUS)"
fi

# Test 4: Verify runner fails closed on invalid cgroup root
echo "Test 4: Runner fails closed on invalid cgroup root..."
set +e
FAIL_CLOSED_OUT=$("$SANDBOX/pes2-runner" --cgroup-root "$DUMMY_DIR/no-such-cgroup" \
    --workspace "$DUMMY_DIR" -- /bin/true 2>&1)
FAIL_CLOSED_STATUS=$?
set -e
if [ "$FAIL_CLOSED_STATUS" -eq 6 ] && echo "$FAIL_CLOSED_OUT" | grep -q "status=SANDBOX_ERROR"; then
    pass "Runner fails closed with SANDBOX_ERROR when cgroup setup fails"
else
    fail "Runner did not fail closed on missing cgroup (status=$FAIL_CLOSED_STATUS, out=$FAIL_CLOSED_OUT)"
fi

# Test 5: Regression test - no-delegation environment reports SKIPPED unambiguously
echo "Test 5: Regression test: test_runner.sh reports SKIPPED unambiguously when cgroup delegation is absent..."
set +e
NO_DELEG_OUT=$(PES2_REQUIRE_RUNTIME=0 PES2_CGROUP_ROOT="$DUMMY_DIR/nonexistent" bash "$SANDBOX/tests/test_runner.sh" 2>&1)
NO_DELEG_STATUS=$?
set -e
if [ "$NO_DELEG_STATUS" -eq 0 ] && \
   echo "$NO_DELEG_OUT" | grep -q "RUNTIME ISOLATION SKIPPED" && \
   echo "$NO_DELEG_OUT" | grep -q "Do NOT treat this output or build success as meeting Phase 1 DoD" && \
   ! echo "$NO_DELEG_OUT" | grep -q "STATUS: ALL_PASSED"; then
    pass "Runner reports SKIPPED with clear warning and never claims ALL_PASSED without delegation"
else
    fail "Runner did not report skip unambiguously when cgroup delegation was absent (status=$NO_DELEG_STATUS, out=$NO_DELEG_OUT)"
fi

# Test 5b: Regression test - strict runtime mode fails closed when delegation is missing
echo "Test 5b: Strict mode (PES2_REQUIRE_RUNTIME=1) fails closed when delegation is absent..."
set +e
STRICT_NO_DELEG_OUT=$(PES2_REQUIRE_RUNTIME=1 PES2_CGROUP_ROOT="$DUMMY_DIR/nonexistent" bash "$SANDBOX/tests/test_runner.sh" 2>&1)
STRICT_NO_DELEG_STATUS=$?
set -e
if [ "$STRICT_NO_DELEG_STATUS" -ne 0 ] && \
   echo "$STRICT_NO_DELEG_OUT" | grep -q "ERROR: Runtime isolation required"; then
    pass "Strict mode fails closed with non-zero exit when runtime isolation cannot run"
else
    fail "Strict mode did not fail closed on missing delegation (status=$STRICT_NO_DELEG_STATUS, out=$STRICT_NO_DELEG_OUT)"
fi

echo "========================================================================"
echo "CGROUP DISCOVERY UNIT TEST SUMMARY:"
echo "  All 5 focused discovery and fallback unit checks PASSED."
echo "  Notice: This validates discovery/fallback logic only, not runtime isolation."
echo "========================================================================"
