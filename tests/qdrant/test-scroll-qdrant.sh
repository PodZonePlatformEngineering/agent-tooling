#!/usr/bin/env bash
# Structural tests for primitives/qdrant/scroll-qdrant.sh
# Live integration tests are in tests/test_primitives.sh
# Arg order: scroll-qdrant.sh <collection> [limit] [filter_json]
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

echo "=== scroll-qdrant.sh (structural) ==="

expect_fail "missing all args"              "$SCRIPT"
expect_fail "missing PODZONE_QDRANT_APIKEY" \
  env -u PODZONE_QDRANT_APIKEY "$SCRIPT" "agent-tooling-test"

echo "  Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
