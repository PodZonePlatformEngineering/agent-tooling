#!/usr/bin/env bash
# Structural tests for primitives/qdrant/search-qdrant.sh
# Live integration is exercised via tests/test_primitives.sh when Qdrant + Ollama are reachable.
# Arg order: search-qdrant.sh <collection> <query> [limit] [filter_json] [embed_model] [ollama_host]
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/primitives/qdrant/search-qdrant.sh"
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

echo "=== search-qdrant.sh (structural) ==="

expect_fail "missing all args"              "$SCRIPT"
expect_fail "missing query"                 "$SCRIPT" "agent-tooling-test"
expect_fail "missing PODZONE_QDRANT_APIKEY" \
  env -u PODZONE_QDRANT_APIKEY "$SCRIPT" "agent-tooling-test" "hello"

echo "  Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
