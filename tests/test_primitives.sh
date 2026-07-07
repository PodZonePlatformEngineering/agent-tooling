#!/usr/bin/env bash
# T1–T10 live integration tests for all agent-tooling primitives.
# Requires: Ollama (localhost:11434), Qdrant (qdrant.agenticflows.co.uk:8080),
#           Telegram test bot token (PODZONE_TELEGRAM_TEST_BOT),
#           Gmail OAuth token (~/.config/podzone/gmail-token.json).
# Collection: agent-tooling-test (768-dim cosine)
#
# Point IDs are deterministic UUIDs derived from friendly names (md5 hash),
# matching the pattern used in podzoneTeam hooks. Friendly names are
# stored in payload.name.
#   test-wf-001 → 878e3ac7-5b7e-0ae8-eee7-221a885595d1
#   test-wf-002 → 9bf66cfd-5318-ccd8-0998-e431e3e9d5d4
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EMBED="${ROOT}/primitives/ollama/embed-text.sh"
ADD="${ROOT}/primitives/qdrant/add-qdrant-point.sh"
PATCH="${ROOT}/primitives/qdrant/patch-qdrant-payload.sh"
SCROLL="${ROOT}/primitives/qdrant/scroll-qdrant.sh"
SEARCH="${ROOT}/primitives/qdrant/search-qdrant.sh"
TELEGRAM="${ROOT}/primitives/telegram/send-telegram-message.sh"
GMAIL="${ROOT}/primitives/gmail/create-gmail-draft.sh"

COLLECTION="agent-tooling-test"
TELEGRAM_CHAT_ID="8228837360"
ID_WF001="878e3ac7-5b7e-0ae8-eee7-221a885595d1"
ID_WF002="9bf66cfd-5318-ccd8-0998-e431e3e9d5d4"
PASS=0; FAIL=0

ok()   { echo "  PASS T${TNUM}: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL T${TNUM}: $1"; FAIL=$((FAIL + 1)); }

TNUM=1
echo ""
echo "=== T1: embed-text — embed 'hello agenticflows' ==="
VECTOR=$(bash "${EMBED}" "hello agenticflows")
LEN=$(python3 -c "import json,sys; v=json.loads(sys.argv[1]); print(len(v))" "${VECTOR}")
if [[ "${LEN}" == "768" ]]; then
  ok "output is JSON array of length 768"
else
  fail "expected 768 floats, got ${LEN}"
fi

TNUM=2
echo ""
echo "=== T2: add-qdrant-point — upsert test-wf-001 ==="
NOW=$(python3 -c "from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())")
PAYLOAD="{\"name\": \"test-wf-001\", \"source\": \"test\", \"agent\": \"hephaestus\", \"ts\": \"${NOW}\", \"status\": \"created\"}"
RESULT=$(bash "${ADD}" "${COLLECTION}" "${ID_WF001}" "${VECTOR}" "${PAYLOAD}")
if echo "${RESULT}" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('status')=='ok'" 2>/dev/null; then
  ok "response status ok"
else
  fail "unexpected response: ${RESULT}"
fi

TNUM=3
echo ""
echo "=== T3: scroll-qdrant — scroll collection, check test-wf-001 ==="
RESULT=$(bash "${SCROLL}" "${COLLECTION}" 5)
if echo "${RESULT}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
pts = data['result']['points']
pt = next((p for p in pts if p.get('payload',{}).get('name') == 'test-wf-001'), None)
assert pt is not None, f'test-wf-001 not found in {[p.get(\"payload\",{}).get(\"name\") for p in pts]}'
assert pt['payload']['source'] == 'test', f\"expected source=test, got {pt['payload']['source']}\"
" 2>/dev/null; then
  ok "test-wf-001 present with payload.source == 'test'"
else
  fail "test-wf-001 missing or payload wrong: ${RESULT}"
fi

TNUM=4
echo ""
echo "=== T4: patch-qdrant-payload — patch test-wf-001 status to 'patched' ==="
RESULT=$(bash "${PATCH}" "${COLLECTION}" "${ID_WF001}" '{"status": "patched"}')
if echo "${RESULT}" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('status')=='ok'" 2>/dev/null; then
  ok "response status ok"
else
  fail "unexpected response: ${RESULT}"
fi

TNUM=5
echo ""
echo "=== T5: scroll-qdrant with filter — test-wf-001 status should be 'patched' ==="
FILTER='{"must":[{"key":"source","match":{"value":"test"}}]}'
RESULT=$(bash "${SCROLL}" "${COLLECTION}" 10 "${FILTER}")
if echo "${RESULT}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
pts = data['result']['points']
pt = next((p for p in pts if p.get('payload',{}).get('name') == 'test-wf-001'), None)
assert pt is not None, 'test-wf-001 not found'
assert pt['payload']['status'] == 'patched', f\"expected patched, got {pt['payload']['status']}\"
" 2>/dev/null; then
  ok "test-wf-001 present, payload.status == 'patched'"
else
  fail "test-wf-001 missing or status not patched: ${RESULT}"
fi

TNUM=6
echo ""
echo "=== T6: add-qdrant-point — upsert test-wf-002 (agent: hermes) ==="
VECTOR2=$(bash "${EMBED}" "second test point hermes")
NOW2=$(python3 -c "from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())")
PAYLOAD2="{\"name\": \"test-wf-002\", \"source\": \"test\", \"agent\": \"hermes\", \"ts\": \"${NOW2}\", \"status\": \"created\"}"
RESULT=$(bash "${ADD}" "${COLLECTION}" "${ID_WF002}" "${VECTOR2}" "${PAYLOAD2}")
if echo "${RESULT}" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('status')=='ok'" 2>/dev/null; then
  ok "response status ok"
else
  fail "unexpected response: ${RESULT}"
fi

TNUM=7
echo ""
echo "=== T7: scroll-qdrant — both test-wf-001 and test-wf-002 present ==="
RESULT=$(bash "${SCROLL}" "${COLLECTION}" 10)
if echo "${RESULT}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
pts = data['result']['points']
names = [p.get('payload',{}).get('name') for p in pts]
assert 'test-wf-001' in names, f'test-wf-001 missing from {names}'
assert 'test-wf-002' in names, f'test-wf-002 missing from {names}'
" 2>/dev/null; then
  ok "both test-wf-001 and test-wf-002 in results"
else
  fail "one or both points missing: ${RESULT}"
fi

TNUM=8
echo ""
echo "=== T8: embed-text — embed 150-word string ==="
LONG_TEXT="The quick brown fox jumps over the lazy dog $(python3 -c "print(' '.join(['word']*143))")"
VECTOR3=$(bash "${EMBED}" "${LONG_TEXT}")
if echo "${VECTOR3}" | python3 -c "
import json, sys
v = json.load(sys.stdin)
assert isinstance(v, list), 'not a list'
assert len(v) == 768, f'expected 768, got {len(v)}'
assert all(isinstance(x, (int, float)) for x in v), 'non-numeric values'
" 2>/dev/null; then
  ok "valid 768-float array for 150-word input"
else
  fail "invalid output for long text"
fi

TNUM=9
echo ""
echo "=== T9: send-telegram-message — send test message ==="
RESULT=$(bash "${TELEGRAM}" "${TELEGRAM_CHAT_ID}" "agent-tooling T9 test — Qdrant+Ollama+Telegram ok")
if echo "${RESULT}" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('ok') is True" 2>/dev/null; then
  ok "response ok:true"
else
  fail "unexpected response: ${RESULT}"
fi

TNUM=10
echo ""
echo "=== T10: create-gmail-draft — draft to podzone.cloud@gmail.com ==="
RESULT=$(bash "${GMAIL}" \
  --to "podzone.cloud@gmail.com" \
  --subject "agent-tooling primitive test 2026-05-14" \
  --body "T-011 all primitives passing")
if echo "${RESULT}" | grep -qE "^draft_id=.+"; then
  DRAFT_ID=$(echo "${RESULT}" | sed 's/draft_id=//')
  ok "draft created: ${DRAFT_ID}"
else
  fail "unexpected output: ${RESULT}"
fi

TNUM=11
echo ""
echo "=== T11: create-gmail-draft — draft with attachment ==="
TMPFILE="$(mktemp /tmp/agent-tooling-test-attachment-XXXXXX.txt)"
echo "agent-tooling T11 attachment test — $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "${TMPFILE}"
RESULT=$(bash "${GMAIL}" \
  --to "podzone.cloud@gmail.com" \
  --subject "agent-tooling attachment test 2026-05-14" \
  --body "T11 draft with attachment" \
  --attachment "${TMPFILE}")
rm -f "${TMPFILE}"
if echo "${RESULT}" | grep -qE "^draft_id=.+"; then
  DRAFT_ID=$(echo "${RESULT}" | sed 's/draft_id=//')
  ok "draft with attachment created: ${DRAFT_ID}"
else
  fail "unexpected output: ${RESULT}"
fi

TNUM=12
echo ""
echo "=== T12: search-qdrant — semantic search returns test-wf-002 for hermes query ==="
RESULT=$(bash "${SEARCH}" "${COLLECTION}" "second test point hermes" 5)
if echo "${RESULT}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
pts = data['result']
assert len(pts) > 0, 'no results returned'
top = pts[0]
assert top['payload']['name'] == 'test-wf-002', f\"expected test-wf-002 top hit, got {top['payload']['name']}\"
assert 'score' in top, 'missing score field'
" 2>/dev/null; then
  ok "top hit is test-wf-002 with score"
else
  fail "search result wrong or malformed: ${RESULT}"
fi

echo ""
echo "========================================"
echo "Results: ${PASS} passed, ${FAIL} failed"
echo "========================================"
[[ $FAIL -eq 0 ]]
