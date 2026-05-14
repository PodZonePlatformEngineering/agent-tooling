#!/usr/bin/env bash
# Structural tests for primitives/gmail/create-gmail-draft.sh
# Live integration test (T10) is in tests/test_primitives.sh
# Interface: create-gmail-draft.sh --to <email> --subject <subject> --body <body> [--attachment <path>]
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

echo "=== create-gmail-draft.sh (structural) ==="

# Missing token file
expect_fail "missing token file" \
  env GMAIL_TOKEN_FILE="/tmp/no-such-token-file-$$" \
  "$SCRIPT" --to "to@example.com" --subject "Subject" --body "Body"

echo "  Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
