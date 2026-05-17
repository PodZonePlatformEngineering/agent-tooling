#!/usr/bin/env bash
# startup.sh — SessionStart hook for v2.0 home repos (ADR-008 / PROJ-033)
# Copies to .claude/hooks/ in each home repo via scaffold.sh.
#
# STUB — full implementation in PROJ-033/T-005 (startup context materialisation).
# Current behaviour: extracts agent identity from repo name and emits a minimal
# additionalContext block so sessions are not unidentified.
#
# Full behaviour (T-005):
#   1. Extract agent name from repo: basename $(git rev-parse --show-toplevel) | cut -d- -f3-
#   2. Query Qdrant work_items for approved brief (agent=<name>, status=approved)
#   3. Materialise context/brief.md + context/identity.yaml
#   4. Assemble 10-section additionalContext and inject via stdout (JSON block)
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
REPO_NAME="$(basename "$REPO_ROOT")"
AGENT_NAME="$(echo "$REPO_NAME" | cut -d- -f3-)"

echo "==> startup.sh: agent=${AGENT_NAME} repo=${REPO_NAME} [stub — PROJ-033/T-005 pending]" >&2
exit 0
