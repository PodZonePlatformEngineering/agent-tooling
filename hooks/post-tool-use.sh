#!/usr/bin/env bash
# PostToolUse hook — log tool result (success or failure).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIMITIVES="${SCRIPT_DIR}/../primitives"
COLLECTION="claude_session_telemetry"

STDIN="$(cat)"
SESSION_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['session_id'])" "${STDIN}")"
# Sid-keyed committed logs (PROJ-039/T-048): primitives invoked by this hook log
# to logs/primitives-{sid8}.log via this export.
export PODZONE_SESSION_ID="${SESSION_ID}"
TOOL_NAME="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('tool_name','unknown'))" "${STDIN}")"
TIMESTAMP="$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())")"
CWD="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('cwd','.'))" "${STDIN}")"
REPO_NAME="$(basename "${CWD}")"
GIT_BRANCH="$(git -C "${CWD}" branch --show-current 2>/dev/null || echo 'unknown')"
COMMIT_SHA="$(git -C "${CWD}" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
OS_USER="${USER:-$(id -un)}"
PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')"
POINT_ID="$(python3 -c "import uuid; print(str(uuid.uuid4()))")"

RESPONSE_SUMMARY="$(python3 -c "
import json, sys
d = json.loads(sys.argv[1])
resp = str(d.get('tool_response') or '')
print(resp[:500])
" "${STDIN}")"

TOOL_INPUT_JSON="$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(json.dumps(d.get('tool_input', {})))" "${STDIN}")"

EMBEDDING="$("${PRIMITIVES}/ollama/embed-text.sh" "${TOOL_NAME}: ${RESPONSE_SUMMARY}")"
VECTOR_JSON="{\"action_vector\": ${EMBEDDING}}"

PAYLOAD="$(python3 -c "
import json, sys
d = json.loads(sys.argv[1])
resp = str(d.get('tool_response') or '')
print(json.dumps({
  'event_type': 'PostToolUse',
  'session_id': sys.argv[2],
  'timestamp': sys.argv[3],
  'repository': {'name': sys.argv[4], 'git_branch': sys.argv[5], 'commit_sha': sys.argv[6]},
  'environment': {'user': sys.argv[7], 'platform': sys.argv[8]},
  'tool_name': sys.argv[9],
  'tool_input': json.loads(sys.argv[10]),
  'tool_output_summary': resp[:500],
  'status': 'failure' if 'error' in resp.lower() else 'success',
}))" "${STDIN}" "${SESSION_ID}" "${TIMESTAMP}" "${REPO_NAME}" "${GIT_BRANCH}" "${COMMIT_SHA}" "${OS_USER}" "${PLATFORM}" "${TOOL_NAME}" "${TOOL_INPUT_JSON}")"

"${PRIMITIVES}/qdrant/add-qdrant-point.sh" "${COLLECTION}" "${POINT_ID}" "${VECTOR_JSON}" "${PAYLOAD}"
