#!/usr/bin/env bash
# SessionStart hook — upsert session baseline into claude_session_telemetry.
# Point ID = session_id (stable anchor for this session's chain of events).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIMITIVES="${SCRIPT_DIR}/../primitives"
COLLECTION="claude_session_telemetry"

# Best-effort runtime logging (PROJ-039/T-029). Sourcing failure must not abort.
# shellcheck source=/dev/null
source "${PRIMITIVES}/log_primitive.sh" 2>/dev/null || true
command -v log_primitive >/dev/null 2>&1 || log_primitive() { :; }

STDIN="$(cat)"

# Unfinalised-session guard (PROJ-039/T-030): before doing anything else, recover
# any prior session whose SessionEnd finalise was truncated (killed / timed-out
# mid-sequence). Idempotent + best-effort — never blocks startup.
log_primitive "session-start.sh" "running unfinalised-session guard"
# Guard writes only to stderr + logs/libraries.log; nothing on stdout reaches the
# session context. Best-effort: a guard failure must never block SessionStart.
python3 "${SCRIPT_DIR}/session-end-finalise.py" --guard </dev/null || true

SESSION_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['session_id'])" "${STDIN}")"
CWD="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('cwd','.'))" "${STDIN}")"
TRANSCRIPT_PATH="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('transcript_path',''))" "${STDIN}")"
TIMESTAMP="$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())")"
REPO_NAME="$(basename "${CWD}")"
GIT_BRANCH="$(git -C "${CWD}" branch --show-current 2>/dev/null || echo 'unknown')"
COMMIT_SHA="$(git -C "${CWD}" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
OS_USER="${USER:-$(id -un)}"
PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')"

EMBED_TEXT="SessionStart: ${REPO_NAME} ${GIT_BRANCH} ${COMMIT_SHA}"
EMBEDDING="$("${PRIMITIVES}/ollama/embed-text.sh" "${EMBED_TEXT}")"
VECTOR_JSON="{\"intent_vector\": ${EMBEDDING}}"

PAYLOAD="$(python3 -c "
import json, sys
print(json.dumps({
  'event_type': 'SessionStart',
  'session_id': sys.argv[1],
  'timestamp': sys.argv[2],
  'repository': {'name': sys.argv[3], 'git_branch': sys.argv[4], 'commit_sha': sys.argv[5]},
  'environment': {'user': sys.argv[6], 'platform': sys.argv[7]},
  'transcript_path': sys.argv[8],
}))" "${SESSION_ID}" "${TIMESTAMP}" "${REPO_NAME}" "${GIT_BRANCH}" "${COMMIT_SHA}" "${OS_USER}" "${PLATFORM}" "${TRANSCRIPT_PATH}")"

"${PRIMITIVES}/qdrant/add-qdrant-point.sh" "${COLLECTION}" "${SESSION_ID}" "${VECTOR_JSON}" "${PAYLOAD}"
