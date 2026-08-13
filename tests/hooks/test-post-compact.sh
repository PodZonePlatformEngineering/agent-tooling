#!/usr/bin/env bash
# Integration test for hooks/post-compact.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HOOK="${SCRIPT_DIR}/../../hooks/post-compact.sh"
PRIMITIVES="${SCRIPT_DIR}/../../primitives"
COLLECTION="claude_session_telemetry"
QDRANT_URL="${QDRANT_URL:-${AGENTSONLY_QDRANT_URL:-https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io}}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"

TEST_SESSION_ID="test-$(python3 -c "import uuid; print(str(uuid.uuid4()))")"

PAYLOAD=$(python3 -c "
import json
print(json.dumps({
  'session_id': '${TEST_SESSION_ID}',
  'summary': 'Test compaction summary for integration verification of PostCompact hook.'
}))")

echo "==> Sending synthetic PostCompact payload (session_id=${TEST_SESSION_ID})..."
echo "${PAYLOAD}" | bash "${HOOK}" && echo "  Hook: ok" || { echo "FAIL: hook exited non-zero"; exit 1; }

sleep 1

echo "==> Verifying point in Qdrant..."
RESULT="$(bash "${PRIMITIVES}/qdrant/scroll-qdrant.sh" "${COLLECTION}" 10 \
  "{\"must\": [{\"key\": \"session_id\", \"match\": {\"value\": \"${TEST_SESSION_ID}\"}}]}")"

COUNT="$(python3 -c "import json,sys; print(len(json.loads(sys.argv[1])['result']['points']))" "${RESULT}")"
EVENT="$(python3 -c "import json,sys; pts=json.loads(sys.argv[1])['result']['points']; print(pts[0]['payload']['event_type'] if pts else 'NOT_FOUND')" "${RESULT}")"

echo "==> Cleaning up test point..."
curl -sf -X POST "${QDRANT_URL}/collections/${COLLECTION}/points/delete" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d "{\"filter\": {\"must\": [{\"key\": \"session_id\", \"match\": {\"value\": \"${TEST_SESSION_ID}\"}}]}}" \
  > /dev/null

[ "${COUNT}" -ge 1 ] && [ "${EVENT}" = "PostCompact" ] \
  && echo "PASS: PostCompact point found (session_id=${TEST_SESSION_ID})" \
  || { echo "FAIL: expected 1+ PostCompact points, got ${COUNT} (event_type=${EVENT})"; exit 1; }

# --- PROJ-039/T-257 §5.1: session-stash push, brief-first only -------------
STASH_COLLECTION="session_stash"
TEST_BRIEF_ID="test/2026-08-13-post-compact-stash-wiring-$(python3 -c "import uuid; print(str(uuid.uuid4())[:8])")"
TEST_STASH_SESSION_ID="test-stash-$(python3 -c "import uuid; print(str(uuid.uuid4()))")"

echo "==> Sending synthetic PostCompact payload WITH BRIEF_ID set (brief_id=${TEST_BRIEF_ID})..."
PAYLOAD2=$(python3 -c "
import json
print(json.dumps({
  'session_id': '${TEST_STASH_SESSION_ID}',
  'summary': 'Test resume narrative for session-stash push verification.'
}))")
echo "${PAYLOAD2}" | BRIEF_ID="${TEST_BRIEF_ID}" bash "${HOOK}" \
  && echo "  Hook: ok" || { echo "FAIL: hook exited non-zero with BRIEF_ID set"; exit 1; }

echo "==> Verifying session_stash point was pushed for ${TEST_BRIEF_ID}..."
STASH_CONTENT="$(python3 "${REPO_ROOT}/tools/session-stash-pop.py" \
  --brief-id "${TEST_BRIEF_ID}" --session-id "verify-post-compact-cleanup")"
[ "${STASH_CONTENT}" = "Test resume narrative for session-stash push verification." ] \
  && echo "PASS: session_stash entry found and content matches (popped clean, no residue)" \
  || { echo "FAIL: expected the pushed summary back from pop, got: ${STASH_CONTENT}"; exit 1; }

echo "==> Confirming BRIEF_ID unset does not error (silent no-op path)..."
TEST_NO_BRIEF_SESSION_ID="test-nostash-$(python3 -c "import uuid; print(str(uuid.uuid4()))")"
PAYLOAD3=$(python3 -c "
import json
print(json.dumps({
  'session_id': '${TEST_NO_BRIEF_SESSION_ID}',
  'summary': 'Should never reach session_stash — BRIEF_ID unset.'
}))")
echo "${PAYLOAD3}" | env -u BRIEF_ID bash "${HOOK}" \
  && echo "PASS: hook exits 0 without BRIEF_ID (session-stash push silently skipped)" \
  || { echo "FAIL: hook exited non-zero without BRIEF_ID"; exit 1; }
