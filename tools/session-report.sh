#!/usr/bin/env bash
# session-report.sh — render a markdown volume report for a claude_session_telemetry session.
# Usage: session-report.sh [session_id]
# Auth: PODZONE_QDRANT_APIKEY
set -euo pipefail

COLLECTION="claude_session_telemetry"
QDRANT_URL="${AGENTSONLY_QDRANT_URL:-http://qdrant.agenticflows.co.uk:8080}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"

# ---------------------------------------------------------------------------
# 1. Resolve session_id
# ---------------------------------------------------------------------------
if [[ $# -ge 1 && -n "${1:-}" ]]; then
  SESSION_ID="$1"
else
  LATEST="$(curl -sf -X POST "${QDRANT_URL}/collections/${COLLECTION}/points/scroll" \
    -H "Content-Type: application/json" \
    -H "api-key: ${API_KEY}" \
    -d '{
      "limit": 1,
      "with_payload": true,
      "with_vector": false,
      "order_by": {"key": "timestamp", "direction": "desc"},
      "filter": {"must": [{"key": "event_type", "match": {"value": "SessionStart"}}]}
    }')"
  SESSION_ID="$(python3 -c "
import json, sys
pts = json.loads(sys.argv[1]).get('result', {}).get('points', [])
print(pts[0]['payload']['session_id'] if pts else '')
" "${LATEST}")"
  if [[ -z "${SESSION_ID}" ]]; then
    echo "No SessionStart events found in ${COLLECTION}." >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# 2. Scroll all points for this session_id (paginate until next_page_offset null)
# ---------------------------------------------------------------------------
FILTER="{\"must\":[{\"key\":\"session_id\",\"match\":{\"value\":\"${SESSION_ID}\"}}]}"
ALL_POINTS="[]"
OFFSET=""

while true; do
  if [[ -n "${OFFSET}" ]]; then
    BODY="{\"limit\":100,\"with_payload\":true,\"with_vector\":false,\"offset\":\"${OFFSET}\",\"filter\":${FILTER}}"
  else
    BODY="{\"limit\":100,\"with_payload\":true,\"with_vector\":false,\"filter\":${FILTER}}"
  fi

  RESPONSE="$(curl -sf -X POST "${QDRANT_URL}/collections/${COLLECTION}/points/scroll" \
    -H "Content-Type: application/json" \
    -H "api-key: ${API_KEY}" \
    -d "${BODY}")"

  PARSED="$(python3 -c "
import json, sys
result = json.loads(sys.argv[1]).get('result', {})
print(json.dumps({'points': result.get('points', []), 'offset': result.get('next_page_offset')}))
" "${RESPONSE}")"

  ALL_POINTS="$(python3 -c "
import json, sys
existing = json.loads(sys.argv[1])
new = json.loads(sys.argv[2])['points']
print(json.dumps(existing + new))
" "${ALL_POINTS}" "${PARSED}")"

  NEXT_OFFSET="$(python3 -c "
import json, sys
o = json.loads(sys.argv[1]).get('offset')
print(o if o is not None else '')
" "${PARSED}")"

  [[ -z "${NEXT_OFFSET}" ]] && break
  OFFSET="${NEXT_OFFSET}"
done

# ---------------------------------------------------------------------------
# 3. Bail gracefully if session not found
# ---------------------------------------------------------------------------
POINT_COUNT="$(python3 -c "import json,sys; print(len(json.loads(sys.argv[1])))" "${ALL_POINTS}")"
if [[ "${POINT_COUNT}" -eq 0 ]]; then
  echo "Session not found: ${SESSION_ID}" >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# 4. Parse and render markdown report
# ---------------------------------------------------------------------------
python3 -c "
import json, sys
from datetime import datetime, timezone

points = json.loads(sys.argv[1])
session_id = sys.argv[2]
qdrant_url = sys.argv[3]

counts = {}
timestamps = []
repos = set()
branches = set()

for p in points:
    payload = p.get('payload', {})
    et = payload.get('event_type', 'Unknown')
    counts[et] = counts.get(et, 0) + 1
    ts = payload.get('timestamp')
    if ts:
        timestamps.append(ts)
    repo = payload.get('repository', {})
    if repo.get('name'):
        repos.add(repo['name'])
    if repo.get('git_branch'):
        branches.add(repo['git_branch'])

timestamps.sort()
start_ts = timestamps[0] if timestamps else 'unknown'
end_ts   = timestamps[-1] if timestamps else 'unknown'

if len(timestamps) >= 2:
    try:
        t_start = datetime.fromisoformat(start_ts)
        t_end   = datetime.fromisoformat(end_ts)
        secs    = int((t_end - t_start).total_seconds())
        duration = f'{secs//3600:02d}:{(secs%3600)//60:02d}:{secs%60:02d}'
    except Exception:
        duration = 'unknown'
else:
    duration = '00:00:00'

now   = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
total = sum(counts.values())

print(f'# Session Report — {session_id}')
print()
print(f'**Repo:** {\" \".join(sorted(repos)) or \"unknown\"}  **Branch:** {\" \".join(sorted(branches)) or \"unknown\"}  **Duration:** {duration}')
print(f'**Period:** {start_ts} → {end_ts}')
print()
print('## Event Volume')
print()
print('| Event Type        | Count |')
print('|-------------------|-------|')

ordered = ['SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop', 'PostCompact']
printed = set()
for et in ordered:
    if et in counts:
        print(f'| {et:<17} | {counts[et]:<5} |')
        printed.add(et)
for et in sorted(counts):
    if et not in printed:
        print(f'| {et:<17} | {counts[et]:<5} |')
print(f'| **Total**         | {total:<5} |')
print()
print('## Notes')
print(f'- Generated: {now}')
print(f'- Collection: claude_session_telemetry @ {qdrant_url}')
" "${ALL_POINTS}" "${SESSION_ID}" "${QDRANT_URL}"
