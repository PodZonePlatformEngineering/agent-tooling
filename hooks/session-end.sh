#!/usr/bin/env bash
# session-end.sh — SessionEnd hook (PROJ-039 § 2.4).
#
# Delegates to session-end-finalise.py (best-effort; never breaks teardown):
#   1-3. Qdrant substrate writes — response upsert + response-vector patch +
#        rollups on the canonical `session` point.
#   4.   Commit + push the session JSONL to agent-telemetry.git FIRST (R-015).
#   5.   Delete raw PreToolUse/PostToolUse from CST — ONLY if the push landed
#        (C-006; deletion gated on the durable backstop).
#
# Follow-on: session-finalise (step 6) + brief-result PR (step 7) — the
# consolidate-tasks `session-finalise` refactor (DTD § 1.4).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

STDIN="$(cat)"

echo "==> session-end.sh: finalising session_substrate writes" >&2
printf '%s' "${STDIN}" | python3 "${SCRIPT_DIR}/session-end-finalise.py" || true

exit 0
