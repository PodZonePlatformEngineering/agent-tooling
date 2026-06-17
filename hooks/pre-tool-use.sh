#!/usr/bin/env bash
# PreToolUse hook — log intended tool action.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIMITIVES="${SCRIPT_DIR}/../primitives"
COLLECTION="claude_session_telemetry"

STDIN="$(cat)"
SESSION_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['session_id'])" "${STDIN}")"
TOOL_NAME="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('tool_name','unknown'))" "${STDIN}")"
TIMESTAMP="$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())")"
CWD="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('cwd','.'))" "${STDIN}")"
REPO_NAME="$(basename "${CWD}")"
GIT_BRANCH="$(git -C "${CWD}" branch --show-current 2>/dev/null || echo 'unknown')"
COMMIT_SHA="$(git -C "${CWD}" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
OS_USER="${USER:-$(id -un)}"
PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')"
POINT_ID="$(python3 -c "import uuid; print(str(uuid.uuid4()))")"

TOOL_INPUT_JSON="$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(json.dumps(d.get('tool_input', {})))" "${STDIN}")"
EMBED_DETAIL="$(python3 -c "
import json, sys
ti = json.loads(sys.argv[1])
tool = sys.argv[2]
detail = ti.get('command') or ti.get('file_path') or ti.get('query') or json.dumps(ti)[:200]
print(f'{tool}: {detail}')
" "${TOOL_INPUT_JSON}" "${TOOL_NAME}")"

EMBEDDING="$("${PRIMITIVES}/ollama/embed-text.sh" "${EMBED_DETAIL}")"
VECTOR_JSON="{\"action_vector\": ${EMBEDDING}}"

PAYLOAD="$(python3 -c "
import json, sys
print(json.dumps({
  'event_type': 'PreToolUse',
  'session_id': sys.argv[1],
  'timestamp': sys.argv[2],
  'repository': {'name': sys.argv[3], 'git_branch': sys.argv[4], 'commit_sha': sys.argv[5]},
  'environment': {'user': sys.argv[6], 'platform': sys.argv[7]},
  'tool_name': sys.argv[8],
  'tool_input': json.loads(sys.argv[9]),
}))" "${SESSION_ID}" "${TIMESTAMP}" "${REPO_NAME}" "${GIT_BRANCH}" "${COMMIT_SHA}" "${OS_USER}" "${PLATFORM}" "${TOOL_NAME}" "${TOOL_INPUT_JSON}")"

"${PRIMITIVES}/qdrant/add-qdrant-point.sh" "${COLLECTION}" "${POINT_ID}" "${VECTOR_JSON}" "${PAYLOAD}"
