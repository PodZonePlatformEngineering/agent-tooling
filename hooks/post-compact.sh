#!/usr/bin/env bash
# PostCompact hook — store compacted context summary (payload-only, PROJ-041/T-002).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIMITIVES="${SCRIPT_DIR}/../primitives"
COLLECTION="claude_session_telemetry"

STDIN="$(cat)"
SESSION_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['session_id'])" "${STDIN}")"
# Sid-keyed committed logs (PROJ-039/T-048): primitives invoked by this hook log
# to logs/primitives-{sid8}.log via this export.
export PODZONE_SESSION_ID="${SESSION_ID}"
SUMMARY="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('summary',''))" "${STDIN}")"
TIMESTAMP="$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())")"
CWD="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('cwd', '.'))" "${STDIN}")"
REPO_NAME="$(basename "${CWD}")"
GIT_BRANCH="$(git -C "${CWD}" branch --show-current 2>/dev/null || echo 'unknown')"
COMMIT_SHA="$(git -C "${CWD}" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
OS_USER="${USER:-$(id -un)}"
PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')"
POINT_ID="$(python3 -c "import uuid; print(str(uuid.uuid4()))")"

# Payload-only (PROJ-041/T-002): hooks never embed — an EMPTY named-vector map
# (an omitted `vector` field is a Qdrant 400); the PROJ-042 enrichment job
# embeds in retrospect.
VECTOR_JSON="{}"

PAYLOAD="$(python3 -c "
import json, sys
print(json.dumps({
  'event_type': 'PostCompact',
  'session_id': sys.argv[1],
  'timestamp': sys.argv[2],
  'repository': {'name': sys.argv[3], 'git_branch': sys.argv[4], 'commit_sha': sys.argv[5]},
  'environment': {'user': sys.argv[6], 'platform': sys.argv[7]},
  'retained_summary_block': sys.argv[8],
  'compaction_trigger': 'token_limit_exceeded',
}))" "${SESSION_ID}" "${TIMESTAMP}" "${REPO_NAME}" "${GIT_BRANCH}" "${COMMIT_SHA}" "${OS_USER}" "${PLATFORM}" "${SUMMARY}")"

"${PRIMITIVES}/qdrant/add-qdrant-point.sh" "${COLLECTION}" "${POINT_ID}" "${VECTOR_JSON}" "${PAYLOAD}"

# Session-stash push (PROJ-039/T-257, design doc §5.1) — best-effort resume
# scratch for the next session materialising this same brief, keyed off the
# retained-summary block Claude Code already produced above. Brief-first
# sessions only (design doc §1: brief_id is the addressing key) — a session
# with no BRIEF_ID is a silent no-op, not an error. Never blocks this hook:
# matches conclude-planning-session.py's own "best-effort... exits 0 with a
# note on stderr rather than failing the whole launch" posture.
if [[ -n "${BRIEF_ID:-}" && -n "${SUMMARY}" ]]; then
  IDENTITY_AGENT="$(python3 -c "
import json
from pathlib import Path
try:
    print(json.loads(Path('${CWD}/.workspace/identity.json').read_text()).get('agent', 'unknown'))
except Exception:
    print('unknown')
")"
  python3 "${SCRIPT_DIR}/../tools/session-stash-push.py" \
    --brief-id "${BRIEF_ID}" --session-id "${SESSION_ID}" --agent "${IDENTITY_AGENT}" \
    --trigger compaction --content "${SUMMARY}" >/dev/null 2>&1 \
    || echo "post-compact: session-stash push skipped (soft, best-effort, non-fatal)" >&2
fi
