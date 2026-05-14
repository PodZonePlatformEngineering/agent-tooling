#!/usr/bin/env bash
# Structural tests for primitives/qdrant/add-qdrant-point.sh
# Live integration tests are in tests/test_primitives.sh
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/primitives/qdrant/add-qdrant-point.sh"
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

echo "=== add-qdrant-point.sh (structural) ==="

expect_fail "missing all args"  "$SCRIPT"
expect_fail "missing id"        "$SCRIPT" "test-collection"
expect_fail "missing vector"    "$SCRIPT" "test-collection" "test-id"
expect_fail "missing payload"   "$SCRIPT" "test-collection" "test-id" "[]"
expect_fail "missing PODZONE_QDRANT_APIKEY" \
  env -u PODZONE_QDRANT_APIKEY "$SCRIPT" "agent-tooling-test" "test-id" "[]" "{}"

echo "  Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
