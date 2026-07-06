#!/usr/bin/env bash
# UserPromptSubmit hook — embed and store user intent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIMITIVES="${SCRIPT_DIR}/../primitives"
COLLECTION="claude_session_telemetry"

STDIN="$(cat)"
SESSION_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['session_id'])" "${STDIN}")"
# Sid-keyed committed logs (PROJ-039/T-048): primitives invoked by this hook log
# to logs/primitives-{sid8}.log via this export.
export PODZONE_SESSION_ID="${SESSION_ID}"
PROMPT="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('prompt',''))" "${STDIN}")"
TIMESTAMP="$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())")"
CWD="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('cwd','.'))" "${STDIN}")"
REPO_NAME="$(basename "${CWD}")"
GIT_BRANCH="$(git -C "${CWD}" branch --show-current 2>/dev/null || echo 'unknown')"
COMMIT_SHA="$(git -C "${CWD}" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
OS_USER="${USER:-$(id -un)}"
PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')"
POINT_ID="$(python3 -c "import uuid; print(str(uuid.uuid4()))")"

EMBEDDING="$("${PRIMITIVES}/ollama/embed-text.sh" "${PROMPT}")"
VECTOR_JSON="{\"intent_vector\": ${EMBEDDING}}"

PAYLOAD="$(python3 -c "
import json, sys
print(json.dumps({
  'event_type': 'UserPromptSubmit',
  'session_id': sys.argv[1],
  'timestamp': sys.argv[2],
  'repository': {'name': sys.argv[3], 'git_branch': sys.argv[4], 'commit_sha': sys.argv[5]},
  'environment': {'user': sys.argv[6], 'platform': sys.argv[7]},
  'user_message': sys.argv[8],
}))" "${SESSION_ID}" "${TIMESTAMP}" "${REPO_NAME}" "${GIT_BRANCH}" "${COMMIT_SHA}" "${OS_USER}" "${PLATFORM}" "${PROMPT}")"

"${PRIMITIVES}/qdrant/add-qdrant-point.sh" "${COLLECTION}" "${POINT_ID}" "${VECTOR_JSON}" "${PAYLOAD}"
