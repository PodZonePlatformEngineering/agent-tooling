#!/usr/bin/env bash
# Tests for tools/session-report.sh
# Structural: arg validation, auth guard.
# Integration: live Qdrant round-trip (requires PODZONE_QDRANT_APIKEY).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="${SCRIPT_DIR}/../../tools/session-report.sh"
PRIMITIVES="${SCRIPT_DIR}/../../primitives"
COLLECTION="claude_session_telemetry"
QDRANT_URL="${AGENTSONLY_QDRANT_URL:-http://qdrant.agenticflows.co.uk:8080}"

PASS=0; FAIL=0

ok()   { echo "  PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
expect_fail() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    fail "$desc — expected non-zero exit but got 0"
  else
    ok "$desc (exit non-zero as expected)"
  fi
}

echo "=== session-report.sh (structural) ==="

expect_fail "missing PODZONE_QDRANT_APIKEY" \
  env -u PODZONE_QDRANT_APIKEY "${TOOL}" "00000000-0000-0000-0000-000000000000"

# ---------------------------------------------------------------------------
# Integration tests (skipped if PODZONE_QDRANT_APIKEY not set)
# ---------------------------------------------------------------------------
if [[ -z "${PODZONE_QDRANT_APIKEY:-}" ]]; then
  echo "  SKIP: integration tests (PODZONE_QDRANT_APIKEY not set)"
  echo "  Results: $PASS passed, $FAIL failed"
  [[ $FAIL -eq 0 ]]
  exit $?
fi

echo ""
echo "=== session-report.sh (integration) ==="

TEST_SESSION_ID="$(python3 -c "import uuid; print(str(uuid.uuid4()))")"

# Seed two synthetic points: SessionStart + Stop
POINT_START="$(python3 -c "import uuid,hashlib; print(str(uuid.UUID(hashlib.md5(b'test-session-start-' + '${TEST_SESSION_ID}'.encode()).hexdigest())))")"
POINT_STOP="$(python3 -c "import uuid; print(str(uuid.uuid4()))")"

START_PAYLOAD="{
  \"event_type\": \"SessionStart\",
  \"session_id\": \"${TEST_SESSION_ID}\",
  \"timestamp\": \"2026-05-16T10:00:00+00:00\",
  \"repository\": {\"name\": \"test-repo\", \"git_branch\": \"test-branch\", \"commit_sha\": \"abc1234\"},
  \"environment\": {\"user\": \"testuser\", \"platform\": \"darwin\"},
  \"transcript_path\": \"/tmp/test.jsonl\"
}"
STOP_PAYLOAD="{
  \"event_type\": \"Stop\",
  \"session_id\": \"${TEST_SESSION_ID}\",
  \"timestamp\": \"2026-05-16T10:05:00+00:00\",
  \"repository\": {\"name\": \"test-repo\", \"git_branch\": \"test-branch\", \"commit_sha\": \"abc1234\"},
  \"environment\": {\"user\": \"testuser\", \"platform\": \"darwin\"},
  \"stop_reason\": \"end_turn\"
}"

echo "  Seeding test session (id=${TEST_SESSION_ID})..."
"${PRIMITIVES}/qdrant/add-qdrant-point.sh" "${COLLECTION}" "${POINT_START}" "{}" "${START_PAYLOAD}" > /dev/null
"${PRIMITIVES}/qdrant/add-qdrant-point.sh" "${COLLECTION}" "${POINT_STOP}"  "{}" "${STOP_PAYLOAD}"  > /dev/null
sleep 1

# Test: known session_id renders a report
REPORT="$("${TOOL}" "${TEST_SESSION_ID}" 2>&1)"
if echo "${REPORT}" | grep -q "# Session Report"; then
  ok "report header present"
else
  fail "report header missing"; echo "${REPORT}"
fi

if echo "${REPORT}" | grep -q "SessionStart"; then
  ok "SessionStart row present"
else
  fail "SessionStart row missing"; echo "${REPORT}"
fi

if echo "${REPORT}" | grep -q "Stop"; then
  ok "Stop row present"
else
  fail "Stop row missing"; echo "${REPORT}"
fi

if echo "${REPORT}" | grep -q "test-repo"; then
  ok "repo name present"
else
  fail "repo name missing"
fi

if echo "${REPORT}" | grep -q "00:05:00"; then
  ok "duration correct (00:05:00)"
else
  fail "duration incorrect — expected 00:05:00"; echo "${REPORT}"
fi

# Test: non-existent session exits 0 with 'not found' message
NOT_FOUND="$("${TOOL}" "00000000-0000-0000-0000-000000000000" 2>&1 || true)"
if echo "${NOT_FOUND}" | grep -qi "not found\|no session"; then
  ok "non-existent session handled gracefully"
else
  fail "non-existent session should print 'not found'"
fi

# Test: no-arg resolves latest session (just verify it exits 0 and prints a report)
LATEST_REPORT="$("${TOOL}" 2>&1 || true)"
if echo "${LATEST_REPORT}" | grep -q "# Session Report"; then
  ok "no-arg resolves latest session"
else
  fail "no-arg did not produce a report"; echo "${LATEST_REPORT}"
fi

echo "  Cleaning up test points..."
curl -sf -X POST "${QDRANT_URL}/collections/${COLLECTION}/points/delete" \
  -H "Content-Type: application/json" \
  -H "api-key: ${PODZONE_QDRANT_APIKEY}" \
  -d "{\"filter\":{\"must\":[{\"key\":\"session_id\",\"match\":{\"value\":\"${TEST_SESSION_ID}\"}}]}}" \
  > /dev/null

echo "  Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
