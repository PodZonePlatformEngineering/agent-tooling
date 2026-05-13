#!/usr/bin/env bash
# Tests for primitives/qdrant/scroll-qdrant.sh
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/primitives/qdrant/scroll-qdrant.sh"
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

echo "=== scroll-qdrant.sh ==="

expect_fail "missing all args"              "$SCRIPT"
expect_fail "missing PODZONE_QDRANT_APIKEY" \
  env -u PODZONE_QDRANT_APIKEY "$SCRIPT" "agent-tooling-test" "{}" "5"

# filter_json and limit are optional — valid with just collection
run_test "stub exits 0 with collection only" \
  env PODZONE_QDRANT_APIKEY=dummy QDRANT_URL="http://qdrant.agenticflows.co.uk:8080" \
  "$SCRIPT" "agent-tooling-test"

run_test "stub exits 0 with all args" \
  env PODZONE_QDRANT_APIKEY=dummy QDRANT_URL="http://qdrant.agenticflows.co.uk:8080" \
  "$SCRIPT" "agent-tooling-test" '{"must":[]}' "10"

echo "  Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
