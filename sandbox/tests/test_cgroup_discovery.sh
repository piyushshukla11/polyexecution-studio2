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

# Test 4: Verify test_runner.sh handles missing delegation safely
echo "Test 4: test_runner.sh fails closed on invalid cgroup root..."
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

echo "========================================================================"
echo "SUCCESS: All focused cgroup discovery tests PASSED!"
