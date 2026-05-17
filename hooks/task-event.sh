#!/usr/bin/env bash
# task-event.sh — PostToolUse hook for v2.0 home repos (ADR-008 / PROJ-033)
# Used by: coder, team-lead, cluster-operator role classes.
# Copies to .claude/hooks/ in each home repo via scaffold.sh.
#
# STUB — full implementation in PROJ-033/T-005.
#
# Full behaviour:
#   Records tool invocations to the task_events Qdrant collection:
#   session_id, tool_name, timestamp, task_id, outcome
set -euo pipefail

exit 0
