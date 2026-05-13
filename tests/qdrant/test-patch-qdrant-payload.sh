#!/usr/bin/env bash
# Tests for primitives/qdrant/patch-qdrant-payload.sh
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/primitives/qdrant/patch-qdrant-payload.sh"
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

echo "=== patch-qdrant-payload.sh ==="

expect_fail "missing all args"           "$SCRIPT"
expect_fail "missing id"                 "$SCRIPT" "test-collection"
expect_fail "missing payload"            "$SCRIPT" "test-collection" "test-id"
expect_fail "missing PODZONE_QDRANT_APIKEY" \
  env -u PODZONE_QDRANT_APIKEY "$SCRIPT" "agent-tooling-test" "test-id" "{}"

run_test "stub exits 0 with valid args" \
  env PODZONE_QDRANT_APIKEY=dummy QDRANT_URL="http://qdrant.agenticflows.co.uk:8080" \
  "$SCRIPT" "agent-tooling-test" "$(uuidgen)" '{"key":"value"}'

echo "  Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
