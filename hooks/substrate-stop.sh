#!/usr/bin/env bash
# substrate-stop.sh — Stop hook, tasking half only (PROJ-039 R-009, § 2.3).
#
# The substrate-only sibling of stop.sh. Where stop.sh does BOTH the CST
# observability point and the session_substrate append, this wrapper does ONLY
# the tasking append — for instances where the legacy CST Stop hook is supplied
# separately (the C-003 coexistence model): apex `~/.claude/settings.json` keeps
# firing the pure-CST stop.sh, and a project-level Stop hook adds this substrate
# append. Both fire; neither owns the other's collection.
#
# Parses the Stop-hook stdin JSON and calls append-session-stop.py (which keys the
# canonical `session` point by uuid5(session_id) and appends one session_stop[]
# entry via set_payload — never a full upsert). Best-effort: a Stop hook must
# never break the session, so every step tolerates failure and exits 0.
#
# Auth: PODZONE_QDRANT_APIKEY — inherited from the session env (the apex
# settings.json `env` block, PROJ-033/T-016). No secret is read from disk here.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STDIN="$(cat)"

read -r SESSION_ID STOP_REASON TRANSCRIPT_PATH <<<"$(
  python3 - "$STDIN" <<'PY'
import json, sys
try:
    d = json.loads(sys.argv[1] or "{}")
except Exception:
    d = {}
print(
    d.get("session_id", ""),
    d.get("stop_reason", "end_turn"),
    d.get("transcript_path", ""),
)
PY
)"

TIMESTAMP="$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())")"

[ -n "${SESSION_ID}" ] || { echo "[substrate-stop] no session_id — skip" >&2; exit 0; }

python3 "${SCRIPT_DIR}/append-session-stop.py" \
  "${SESSION_ID}" "${TIMESTAMP}" "${STOP_REASON}" "${TRANSCRIPT_PATH}" || true
exit 0
