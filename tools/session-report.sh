#!/usr/bin/env bash
# session-report.sh — render a markdown volume report for a claude_session_telemetry session.
# Usage: session-report.sh [session_id]
# Auth: PODZONE_QDRANT_APIKEY
set -euo pipefail

COLLECTION="claude_session_telemetry"
QDRANT_URL="${QDRANT_URL:-${AGENTSONLY_QDRANT_URL:-https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io}}"
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

def fmt_ts(ts):
    if not ts:
        return 'unknown'
    try:
        return datetime.fromisoformat(ts).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    except Exception:
        return ts

counts = {}
timestamps = []
repos = set()
branches = set()
shas = []
user = None
platform = None
agent_identity = None
prompts = []
tool_calls = {}
stop_reasons = {}

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
    if repo.get('commit_sha') and ts:
        shas.append((ts, repo['commit_sha']))
    env = payload.get('environment', {})
    if not user and env.get('user'):
        user = env['user']
    if not platform and env.get('platform'):
        platform = env['platform']
    if et == 'SessionStart' and not agent_identity:
        agent_identity = payload.get('agent') or payload.get('scope')
    if et == 'UserPromptSubmit':
        msg = payload.get('user_message', '')
        if msg and ts:
            prompts.append((ts, msg))
    if et == 'PreToolUse':
        tool = payload.get('tool_name', 'Unknown')
        tool_calls[tool] = tool_calls.get(tool, 0) + 1
    if et == 'Stop':
        reason = payload.get('stop_reason')
        if reason:
            stop_reasons[reason] = stop_reasons.get(reason, 0) + 1

timestamps.sort()
start_ts = timestamps[0] if timestamps else None
end_ts   = timestamps[-1] if timestamps else None

if len(timestamps) >= 2:
    try:
        t_start = datetime.fromisoformat(timestamps[0])
        t_end   = datetime.fromisoformat(timestamps[-1])
        secs    = int((t_end - t_start).total_seconds())
        duration = f'{secs//3600:02d}:{(secs%3600)//60:02d}:{secs%60:02d}'
    except Exception:
        duration = 'unknown'
else:
    duration = '00:00:00'

shas.sort()
first_sha = shas[0][1] if shas else None
last_sha  = shas[-1][1] if shas else None

pre_count  = counts.get('PreToolUse', 0)
post_count = counts.get('PostToolUse', 0)
now      = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
total    = sum(counts.values())
short_id = session_id[:8]

print(f'# Session Report — {short_id}')
print()

if agent_identity:
    print(f'**Agent:** {agent_identity}')
    print()

print(f'**Session:** {short_id}')
if repos:
    print(f'**Repos:** {\", \".join(sorted(repos))}')
if branches:
    print(f'**Branches:** {\", \".join(sorted(branches))}')
print(f'**Duration:** {duration}')
if start_ts and end_ts:
    print(f'**Period:** {fmt_ts(start_ts)} → {fmt_ts(end_ts)}')
if pre_count > 0:
    gap = pre_count - post_count
    if gap > 0:
        pct = int(100 * post_count / pre_count)
        print(f'**Tool completion:** {post_count}/{pre_count} ({pct}%)  — {gap} calls did not complete')
    else:
        print(f'**Tool completion:** {pre_count}/{pre_count} (100%)')
if user or platform:
    parts = []
    if user: parts.append(f'**User:** {user}')
    if platform: parts.append(f'**Platform:** {platform}')
    print('  '.join(parts))
if first_sha and last_sha:
    if first_sha == last_sha:
        print(f'**Commit:** {first_sha[:7]}')
    else:
        print(f'**Commits:** {first_sha[:7]} → {last_sha[:7]}')

print()
print('## Event Volume')
print()
print('| Event Type        | Count |')
print('|-------------------|-------|')

ordered = ['SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop', 'SubagentStop', 'PostCompact']
printed = set()
for et in ordered:
    c = counts.get(et, 0)
    print(f'| {et:<17} | {c:<5} |')
    printed.add(et)
for et in sorted(counts):
    if et not in printed:
        print(f'| {et:<17} | {counts[et]:<5} |')
print(f'| **Total**         | {total:<5} |')

prompts.sort()
if prompts:
    print()
    print(f'## Operator Prompts ({len(prompts)})')
    print()
    for i, (_, msg) in enumerate(prompts, 1):
        truncated = (msg[:120] + '…') if len(msg) > 120 else msg
        print(f'{i}. {truncated}')

if tool_calls:
    sorted_tools = sorted(tool_calls.items(), key=lambda x: -x[1])
    total_pre = sum(tool_calls.values())
    print()
    print(f'## Tool Breakdown (PreToolUse — {total_pre} calls)')
    print()
    print(f'| {\"Tool\":<18} | Count |')
    print(f'|{\"-\"*20}|-------|')
    for tool, cnt in sorted_tools:
        print(f'| {tool:<18} | {cnt:<5} |')

if stop_reasons:
    sorted_reasons = sorted(stop_reasons.items(), key=lambda x: -x[1])
    total_stops = sum(stop_reasons.values())
    print()
    print(f'## Stop Reasons ({total_stops})')
    print()
    print(f'| {\"Reason\":<18} | Count |')
    print(f'|{\"-\"*20}|-------|')
    for reason, cnt in sorted_reasons:
        print(f'| {reason:<18} | {cnt:<5} |')

print()
print('## Notes')
print(f'- Session ID: {session_id}')
print(f'- Generated: {now}')
print(f'- Collection: claude_session_telemetry @ {qdrant_url}')
" "${ALL_POINTS}" "${SESSION_ID}" "${QDRANT_URL}"
