#!/usr/bin/env bash
# Integration test for hooks/session-start.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="${SCRIPT_DIR}/../../hooks/session-start.sh"
PRIMITIVES="${SCRIPT_DIR}/../../primitives"
COLLECTION="claude_session_telemetry"
QDRANT_URL="${QDRANT_URL:-${AGENTSONLY_QDRANT_URL:-https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io}}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"

TEST_SESSION_ID="$(python3 -c "import uuid; print(str(uuid.uuid4()))")"

PAYLOAD=$(python3 -c "
import json, os
print(json.dumps({
  'session_id': '${TEST_SESSION_ID}',
  'cwd': os.getcwd(),
  'transcript_path': '/tmp/test-transcript.jsonl'
}))")

echo "==> Sending synthetic SessionStart payload (session_id=${TEST_SESSION_ID})..."
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

[ "${COUNT}" -ge 1 ] && [ "${EVENT}" = "SessionStart" ] \
  && echo "PASS: SessionStart point found (session_id=${TEST_SESSION_ID})" \
  || { echo "FAIL: expected 1+ SessionStart points, got ${COUNT} (event_type=${EVENT})"; exit 1; }
