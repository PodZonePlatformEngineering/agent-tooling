#!/usr/bin/env bash
# Tests for tools/load-secrets.sh (T5–T6).
# T5: --dry-run lists secrets without writing (uses mock JSON, no vault access needed).
# T6: idempotency check (requires PODZONE_QDRANT_APIKEY; skips if not set).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${ROOT}/tools/load-secrets.sh"
PASS=0; FAIL=0
TNUM=0

ok()   { echo "  PASS T${TNUM}: $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL T${TNUM}: $1"; FAIL=$((FAIL+1)); }

echo "=== load-secrets.sh ==="

# ---------------------------------------------------------------------------
# T5: --dry-run lists secrets without writing to Qdrant
# ---------------------------------------------------------------------------
TNUM=5
echo ""
echo "=== T5: load-secrets --dry-run — lists without writing ==="

MOCK='[{"name":"podzone_cloud_bot_token","value":"test-token-123"},
       {"name":"podzone_qdrant_apikey","value":"test-key-456"}]'

OUTPUT=$(echo "${MOCK}" | bash "${SCRIPT}" --dry-run 2>&1) || {
  fail "dry-run exited non-zero: ${OUTPUT}"
  OUTPUT=""
}
if [[ -n "${OUTPUT}" ]]; then
  if echo "${OUTPUT}" | grep -q "\[dry-run\]"; then
    ok "dry-run mode outputs '[dry-run]' lines"
  else
    fail "expected '[dry-run]' in output; got: ${OUTPUT}"
  fi
  if echo "${OUTPUT}" | grep -qE "^\s+loaded:"; then
    fail "dry-run output contains 'loaded:' — writes occurred unexpectedly"
  else
    ok "dry-run output contains no 'loaded:' lines (no writes)"
  fi
fi

# ---------------------------------------------------------------------------
# T6: idempotency — running twice produces same state (no duplicates)
# ---------------------------------------------------------------------------
TNUM=6
echo ""
echo "=== T6: load-secrets — idempotency (two runs, same point count) ==="

if [[ -z "${PODZONE_QDRANT_APIKEY:-}" ]]; then
  echo "  SKIP T6: PODZONE_QDRANT_APIKEY not set"
else
  QDRANT_URL="${QDRANT_URL:-${AGENTSONLY_QDRANT_URL:-https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io}}"
  MOCK='[{"name":"test-idempotency-secret","value":"idempotency-value"}]'

  count_points() {
    curl -sf -X POST "${QDRANT_URL}/collections/secrets/points/scroll" \
      -H "Content-Type: application/json" \
      -H "api-key: ${PODZONE_QDRANT_APIKEY}" \
      -d '{"limit": 1000, "with_payload": false, "with_vector": false}' \
      2>/dev/null | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('result',{}).get('points',[])))"
  }

  echo "${MOCK}" | bash "${SCRIPT}" >/dev/null 2>&1 || true
  COUNT1=$(count_points)

  echo "${MOCK}" | bash "${SCRIPT}" >/dev/null 2>&1 || true
  COUNT2=$(count_points)

  if [[ "${COUNT1}" -eq "${COUNT2}" ]]; then
    ok "point count unchanged after second run: ${COUNT1} points"
  else
    fail "count changed between runs: first=${COUNT1}, second=${COUNT2} — upsert not idempotent"
  fi
fi

echo ""
echo "  Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
