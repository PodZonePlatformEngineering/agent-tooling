#!/usr/bin/env bash
# session-end.sh — SessionEnd hook for v2.0 home repos (ADR-008 / PROJ-033)
# Copies to .claude/hooks/ in each home repo via scaffold.sh.
#
# STUB — full implementation in PROJ-033/T-006 (result materialisation + PR raise).
#
# Full behaviour (T-006):
#   1. Write session_results Qdrant point (task_id, summary, files_changed, status)
#   2. Generate results/session-{date}-{slug}.md from Qdrant point
#   3. Commit + push to PAT branch on home repo
#   4. Raise PR on home repo for Martin review
set -euo pipefail

echo "==> session-end.sh: session end recorded [stub — PROJ-033/T-006 pending]" >&2
exit 0
