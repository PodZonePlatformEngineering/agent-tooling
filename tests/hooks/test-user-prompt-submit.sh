#!/usr/bin/env bash
# Integration test for hooks/user-prompt-submit.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="${SCRIPT_DIR}/../../hooks/user-prompt-submit.sh"
PRIMITIVES="${SCRIPT_DIR}/../../primitives"
COLLECTION="claude_session_telemetry"
QDRANT_URL="${AGENTSONLY_QDRANT_URL:-http://qdrant.agenticflows.co.uk:8080}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"

TEST_SESSION_ID="test-$(python3 -c "import uuid; print(str(uuid.uuid4()))")"

PAYLOAD=$(python3 -c "
import json, os
print(json.dumps({
  'session_id': '${TEST_SESSION_ID}',
  'cwd': os.getcwd(),
  'prompt': 'test prompt for integration verification'
}))")

echo "==> Sending synthetic UserPromptSubmit payload (session_id=${TEST_SESSION_ID})..."
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

[ "${COUNT}" -ge 1 ] && [ "${EVENT}" = "UserPromptSubmit" ] \
  && echo "PASS: UserPromptSubmit point found (session_id=${TEST_SESSION_ID})" \
  || { echo "FAIL: expected 1+ UserPromptSubmit points, got ${COUNT} (event_type=${EVENT})"; exit 1; }
