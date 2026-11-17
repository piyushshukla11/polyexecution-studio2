#!/usr/bin/env bash
# Test harness demonstrating and verifying check_progress_append_only.sh behavior.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/check_progress_append_only.sh"
TMP_DIR="$(mktemp -d /tmp/pes2-append-test-XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

FAILED=0

pass() {
    echo "  [PASS] $1"
}

fail() {
    echo "  [FAIL] $1: $2"
    FAILED=$((FAILED + 1))
}

echo "========================================================================"
echo "Running Append-Only Verification Test Suite for PROGRESS.md"
echo "========================================================================"

cd "$TMP_DIR"
git init -q
git config user.name "test-user"
git config user.email "test@example.com"

# Setup initial baseline
cat <<'EOF' > PROGRESS.md
# PROGRESS.md baseline
Line 1: init
Line 2: setup
EOF
git add PROGRESS.md
git commit -qm "init baseline"

# Test 1: Valid append at end of file must succeed
echo "Test 1: Appending a new entry at end of file..."
cat <<'EOF' >> PROGRESS.md
Line 3: append valid
EOF
git add PROGRESS.md
set +e
TEST1_OUT=$(bash "$SCRIPT" 2>&1)
TEST1_STATUS=$?
set -e
if [ "$TEST1_STATUS" -eq 0 ]; then
    pass "Valid append to PROGRESS.md was cleanly accepted"
else
    fail "Test 1" "Valid append was rejected with code $TEST1_STATUS ($TEST1_OUT)"
fi
git reset -q HEAD
git checkout -q -- PROGRESS.md

# Test 2: In-place line modification must be rejected
echo "Test 2: Modifying an existing historical line..."
sed -i 's/Line 1: init/Line 1: modified/' PROGRESS.md
git add PROGRESS.md
set +e
TEST2_OUT=$(bash "$SCRIPT" 2>&1)
TEST2_STATUS=$?
set -e
if [ "$TEST2_STATUS" -ne 0 ] && echo "$TEST2_OUT" | grep -q "BLOCKED"; then
    pass "Modifying an existing line was correctly blocked"
else
    fail "Test 2" "Line modification was not rejected! Status: $TEST2_STATUS"
fi
git reset -q HEAD
git checkout -q -- PROGRESS.md

# Test 3: Deleting an existing line must be rejected
echo "Test 3: Deleting an existing historical line..."
sed -i '/Line 2: setup/d' PROGRESS.md
git add PROGRESS.md
set +e
TEST3_OUT=$(bash "$SCRIPT" 2>&1)
TEST3_STATUS=$?
set -e
if [ "$TEST3_STATUS" -ne 0 ] && echo "$TEST3_OUT" | grep -q "BLOCKED"; then
    pass "Deleting an existing line was correctly blocked"
else
    fail "Test 3" "Line deletion was not rejected! Status: $TEST3_STATUS"
fi
git reset -q HEAD
git checkout -q -- PROGRESS.md

# Test 4: Inserting a line in the middle of history must be rejected
echo "Test 4: Inserting a line in the middle of historical lines..."
sed -i '1a\Line 1.5: middle insertion' PROGRESS.md
git add PROGRESS.md
set +e
TEST4_OUT=$(bash "$SCRIPT" 2>&1)
TEST4_STATUS=$?
set -e
if [ "$TEST4_STATUS" -ne 0 ] && echo "$TEST4_OUT" | grep -q "Insertion detected"; then
    pass "Inserting a line in the middle was correctly blocked"
else
    fail "Test 4" "Middle insertion was not rejected! Status: $TEST4_STATUS ($TEST4_OUT)"
fi
git reset -q HEAD
git checkout -q -- PROGRESS.md

echo "========================================================================"
if [ "$FAILED" -eq 0 ]; then
    echo "SUCCESS: All append-only verification tests PASSED!"
    exit 0
else
    echo "FAILURE: $FAILED test(s) failed."
    exit 1
fi
