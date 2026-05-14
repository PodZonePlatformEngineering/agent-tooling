#!/usr/bin/env bash
# Structural tests for primitives/telegram/send-telegram-message.sh
# Live integration tests are in tests/test_primitives.sh
# Auth: prefers PODZONE_TELEGRAM_TEST_BOT, falls back to PODZONE_CLOUD_BOT_TOKEN
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

echo "=== send-telegram-message.sh (structural) ==="

expect_fail "missing all args"  "$SCRIPT"
expect_fail "missing text"      "$SCRIPT" "123456"
# Must unset both token vars to trigger the auth failure
expect_fail "missing both token vars" \
  env -u PODZONE_TELEGRAM_TEST_BOT -u PODZONE_CLOUD_BOT_TOKEN "$SCRIPT" "123456" "hello"

echo "  Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
