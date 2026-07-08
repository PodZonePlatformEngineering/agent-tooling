#!/usr/bin/env bash
# Stop hook — PROJ-039 dual-write (DC-1):
#   (a) observability: enriched CST Stop point via hooks/stop-telemetry.py
#       (payload assembly lives fixture-tested in lib/stop_telemetry.py —
#       T-071, CC-381: last_assistant_message + background_tasks +
#       session_crons, embed input = head-truncated message text).
#   (b) tasking: append a session_stop[] entry to the canonical `session` point
#       in session_substrate via set_payload (R-009, § 2.3) — never a full upsert.
# Best-effort throughout: a Stop hook must never break the session.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

STDIN="$(cat)"
SESSION_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['session_id'])" "${STDIN}")"
# Sid-keyed committed logs (PROJ-039/T-048): primitives invoked by this hook log
# to logs/primitives-{sid8}.log via this export.
export PODZONE_SESSION_ID="${SESSION_ID}"
STOP_REASON="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('stop_reason','end_turn'))" "${STDIN}")"
TIMESTAMP="$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())")"
TRANSCRIPT_PATH="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('transcript_path',''))" "${STDIN}")"

# (a) observability — enriched CST Stop point (R-008 / T-071). Payload assembly,
#     embed and write all live in the python entry; best-effort.
printf '%s' "${STDIN}" | python3 "${SCRIPT_DIR}/stop-telemetry.py" || true

# (b) tasking — append session_stop[] to the session_substrate `session` point
#     (R-009, set_payload only). Best-effort: never fail the Stop hook.
python3 "${SCRIPT_DIR}/append-session-stop.py" \
  "${SESSION_ID}" "${TIMESTAMP}" "${STOP_REASON}" "${TRANSCRIPT_PATH}" || true
