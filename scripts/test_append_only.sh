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

# ----------------------------------------------------------------------
# Hook-level tests: invoke real git commits in this isolated repository.
# The hook resolves its repository root relative to its own location, so
# copy the checked-in hook and validators into this temporary repository.
# ----------------------------------------------------------------------
mkdir -p "$TMP_DIR/scripts/hooks"
cp "$REPO_ROOT/scripts/hooks/pre-commit" "$TMP_DIR/scripts/hooks/pre-commit"
cp "$REPO_ROOT/scripts/check_progress_append_only.sh" "$TMP_DIR/scripts/check_progress_append_only.sh"
cp "$REPO_ROOT/scripts/check_repo_hygiene.sh" "$TMP_DIR/scripts/check_repo_hygiene.sh"
chmod +x "$TMP_DIR/scripts/hooks/pre-commit"
git config --local core.hooksPath "$TMP_DIR/scripts/hooks"

# Test 5: A real append commit must pass through the configured hook.
echo "Test 5: Real append commit invokes the configured pre-commit hook..."
printf 'Line 3: append through real hook\n' >> PROGRESS.md
git add PROGRESS.md
set +e
TEST5_OUT=$(git commit -m "test valid append" 2>&1)
TEST5_STATUS=$?
set -e
if [ "$TEST5_STATUS" -eq 0 ] && git log -1 --format=%s | grep -q "test valid append"; then
    pass "A valid append commit succeeded through the configured pre-commit hook"
else
    fail "Test 5" "Expected valid append commit to succeed; status=$TEST5_STATUS; output=$TEST5_OUT"
fi

# Test 6: A real historical modification commit must be rejected.
echo "Test 6: Real historical edit commit is rejected by the configured hook..."
HOOK_HEAD_BEFORE_EDIT=$(git rev-parse HEAD)
sed -i 's/Line 1: init/Line 1: modified/' PROGRESS.md
git add PROGRESS.md
set +e
TEST6_OUT=$(git commit -m "test historical edit" 2>&1)
TEST6_STATUS=$?
set -e
HOOK_HEAD_AFTER_EDIT=$(git rev-parse HEAD)
if [ "$TEST6_STATUS" -ne 0 ] && echo "$TEST6_OUT" | grep -q "BLOCKED" && [ "$HOOK_HEAD_AFTER_EDIT" = "$HOOK_HEAD_BEFORE_EDIT" ]; then
    pass "A historical edit commit was rejected and HEAD did not advance"
else
    fail "Test 6" "Expected hook to reject historical edit without creating a commit; status=$TEST6_STATUS; output=$TEST6_OUT; HEAD before=$HOOK_HEAD_BEFORE_EDIT after=$HOOK_HEAD_AFTER_EDIT"
fi
git reset -q HEAD -- PROGRESS.md
git checkout -q -- PROGRESS.md

# Test 7: A real historical deletion commit must be rejected.
echo "Test 7: Real historical deletion commit is rejected by the configured hook..."
HOOK_HEAD_BEFORE_DELETE=$(git rev-parse HEAD)
sed -i '/Line 2: setup/d' PROGRESS.md
git add PROGRESS.md
set +e
TEST7_OUT=$(git commit -m "test historical deletion" 2>&1)
TEST7_STATUS=$?
set -e
HOOK_HEAD_AFTER_DELETE=$(git rev-parse HEAD)
if [ "$TEST7_STATUS" -ne 0 ] && echo "$TEST7_OUT" | grep -q "BLOCKED" && [ "$HOOK_HEAD_AFTER_DELETE" = "$HOOK_HEAD_BEFORE_DELETE" ]; then
    pass "A historical deletion commit was rejected and HEAD did not advance"
else
    fail "Test 7" "Expected hook to reject historical deletion without creating a commit; status=$TEST7_STATUS; output=$TEST7_OUT; HEAD before=$HOOK_HEAD_BEFORE_DELETE after=$HOOK_HEAD_AFTER_DELETE"
fi

echo "========================================================================"
if [ "$FAILED" -eq 0 ]; then
    echo "SUCCESS: All 7 append-only verification tests PASSED!"
    exit 0
else
    echo "FAILURE: $FAILED test(s) failed."
    exit 1
fi
