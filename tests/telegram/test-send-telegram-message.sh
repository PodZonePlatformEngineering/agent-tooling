#!/usr/bin/env bash
# Tests for primitives/telegram/send-telegram-message.sh
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/primitives/telegram/send-telegram-message.sh"
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

echo "=== send-telegram-message.sh ==="

expect_fail "missing all args"                  "$SCRIPT"
expect_fail "missing text"                      "$SCRIPT" "123456"
expect_fail "missing PODZONE_CLOUD_BOT_TOKEN"   \
  env -u PODZONE_CLOUD_BOT_TOKEN "$SCRIPT" "123456" "hello"

run_test "stub exits 0 with valid args" \
  env PODZONE_CLOUD_BOT_TOKEN=dummy \
  "$SCRIPT" "123456" "test message"

echo "  Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
