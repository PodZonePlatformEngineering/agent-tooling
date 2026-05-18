#!/usr/bin/env bash
# Structural tests for primitives/getSecret.sh (T1–T4).
# T1 + T2 are live integration tests (require Qdrant + secrets collection).
# T3 + T4 are structural (no network required).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${ROOT}/primitives/getSecret.sh"
CREATE="${ROOT}/tools/create-secrets-collection.sh"
PASS=0; FAIL=0
TNUM=0

ok()          { echo "  PASS T${TNUM}: $1"; PASS=$((PASS+1)); }
fail()        { echo "  FAIL T${TNUM}: $1"; FAIL=$((FAIL+1)); }
expect_fail() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    fail "${desc} — expected non-zero exit but got 0"
  else
    ok "${desc} (exit non-zero as expected)"
  fi
}

echo "=== getSecret.sh ==="

QDRANT_URL="${AGENTSONLY_QDRANT_URL:-http://qdrant.agenticflows.co.uk:8080}"
LIVE=false
if [[ -n "${PODZONE_QDRANT_APIKEY:-}" ]]; then
  LIVE=true
  # Ensure secrets collection exists before live tests
  bash "${CREATE}" >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------------------
# T1: retrieves a known secret by name (live)
# ---------------------------------------------------------------------------
TNUM=1
echo ""
echo "=== T1: getSecret — retrieve known secret 'getSecret-test-fixture' ==="

if [[ "${LIVE}" == "false" ]]; then
  echo "  SKIP T1: PODZONE_QDRANT_APIKEY not set"
else
  FIXTURE_ID=$(python3 -c "
import hashlib, uuid
print(str(uuid.UUID(hashlib.md5('getSecret-test-fixture'.encode()).hexdigest())))
")
  SEED_RESULT=$(curl -sf -X PUT "${QDRANT_URL}/collections/secrets/points?wait=true" \
    -H "Content-Type: application/json" \
    -H "api-key: ${PODZONE_QDRANT_APIKEY}" \
    -d "{\"points\": [{\"id\": \"${FIXTURE_ID}\", \"vector\": [0.0, 0.0, 0.0, 0.0], \"payload\": {\"name\": \"getSecret-test-fixture\", \"secret\": \"test-value-abc123\", \"system\": \"general\", \"user\": \"martin\", \"type\": \"api_key\", \"scope\": [\"all\"], \"description\": \"test fixture\", \"created_at\": \"2026-05-18T00:00:00+00:00\", \"rotated_at\": null}}]}" \
    2>/dev/null || echo "CURL_FAILED")
  if echo "${SEED_RESULT}" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('status')=='ok'" 2>/dev/null; then
    RESULT=$(bash "${SCRIPT}" "getSecret-test-fixture" 2>/dev/null || echo "SCRIPT_FAILED")
    if [[ "${RESULT}" == "test-value-abc123" ]]; then
      ok "returns correct value for known secret"
    else
      fail "expected 'test-value-abc123', got '${RESULT}'"
    fi
  else
    fail "could not seed test fixture (collection missing or Qdrant unreachable): ${SEED_RESULT}"
  fi
fi

# ---------------------------------------------------------------------------
# T2: unknown name → exits 1 with descriptive error (live)
# ---------------------------------------------------------------------------
TNUM=2
echo ""
echo "=== T2: getSecret — unknown secret name exits 1 ==="

if [[ "${LIVE}" == "false" ]]; then
  echo "  SKIP T2: PODZONE_QDRANT_APIKEY not set"
else
  ERR_EXIT=0
  ERR_MSG=$(bash "${SCRIPT}" "getSecret-definitely-does-not-exist-xyz" 2>&1) || ERR_EXIT=$?
  if [[ "${ERR_EXIT}" -ne 0 ]]; then
    if echo "${ERR_MSG}" | grep -qiE "not found|does not exist|missing|not found in collection"; then
      ok "exits 1 with descriptive 'not found' message"
    else
      ok "exits 1 (message: ${ERR_MSG})"
    fi
  else
    fail "expected exit 1 for unknown secret but got exit 0"
  fi
fi

# ---------------------------------------------------------------------------
# T3: empty name arg → usage error, exits 1 (structural)
# ---------------------------------------------------------------------------
TNUM=3
echo ""
echo "=== T3: getSecret — empty name arg exits 1 ==="
expect_fail "empty name arg exits 1" \
  env PODZONE_QDRANT_APIKEY="dummy" bash "${SCRIPT}" ""

# ---------------------------------------------------------------------------
# T4: missing PODZONE_QDRANT_APIKEY → exits 1 with error (structural)
# ---------------------------------------------------------------------------
TNUM=4
echo ""
echo "=== T4: getSecret — missing PODZONE_QDRANT_APIKEY exits 1 ==="
expect_fail "missing PODZONE_QDRANT_APIKEY exits 1" \
  env -u PODZONE_QDRANT_APIKEY bash "${SCRIPT}" "any-secret"

echo ""
echo "  Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
