#!/usr/bin/env bash
# Tests for primitives/gmail/create-gmail-draft.sh
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/primitives/gmail/create-gmail-draft.sh"
PASS=0; FAIL=0

ok()          { echo "  PASS: $1"; PASS=$((PASS+1)); }
fail()        { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
expect_fail() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    fail "$desc — expected non-zero exit but got 0"
  else
    ok "$desc (exit non-zero as expected)"
  fi
}
run_test() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    ok "$desc (exit 0)"
  else
    fail "$desc — expected exit 0 but got non-zero"
  fi
}

echo "=== create-gmail-draft.sh ==="

expect_fail "missing all args"    "$SCRIPT"
expect_fail "missing subject"     "$SCRIPT" "to@example.com"
expect_fail "missing body"        "$SCRIPT" "to@example.com" "Subject"

# Missing token file — use a path that doesn't exist
expect_fail "missing token file" \
  env GMAIL_TOKEN_FILE="/tmp/no-such-token-file-$$" \
  "$SCRIPT" "to@example.com" "Subject" "Body"

# Valid stub invocation — token file must exist (create temp)
TMPTOKEN="$(mktemp)"
echo '{"access_token":"dummy"}' > "$TMPTOKEN"
run_test "stub exits 0 with valid args + token file" \
  env GMAIL_TOKEN_FILE="$TMPTOKEN" \
  "$SCRIPT" "to@example.com" "Test Subject" "Test body"
rm -f "$TMPTOKEN"

echo "  Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
