#!/usr/bin/env bash
# Tests for primitives/ollama/embed-text.sh
# These make real Ollama calls (nomic-embed-text must be available on localhost:11434)
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/primitives/ollama/embed-text.sh"
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
    ok "$desc"
  else
    fail "$desc — expected exit 0 but got non-zero"
  fi
}

echo "=== embed-text.sh ==="

expect_fail "missing all args"  "$SCRIPT"

run_test "embeds text (default host)"        "$SCRIPT" "hello world"
run_test "embeds text (explicit host)"       "$SCRIPT" "hello world" "http://localhost:11434"

echo "  Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
