#!/usr/bin/env bash
# session-end.sh — SessionEnd hook (PROJ-039 § 2.4).
#
# PR-A (this change): the Qdrant substrate write path — response upsert +
# response-vector patch + rollups, on the canonical `session` point. Delegated to
# session-end-finalise.py (best-effort; never breaks teardown).
#
# PR-B will extend this with the telemetry commit/push (step 4), the gated
# raw-event deletion (step 5 — conditional on the push landing, C-006),
# session-finalise (step 6) and the brief-result PR (step 7).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

STDIN="$(cat)"

echo "==> session-end.sh: finalising session_substrate writes" >&2
printf '%s' "${STDIN}" | python3 "${SCRIPT_DIR}/session-end-finalise.py" || true

exit 0
