#!/usr/bin/env bash
# log_primitive.sh — best-effort append-only runtime logging for resident shell
# primitives + hooks (PROJ-039/T-029, CC-328).
#
# SOURCE it, then call:
#     log_primitive <component> <message...>
#
# Writes one timestamped, caller-identified line to <project>/logs/primitives.log:
#     2026-06-27T17:48:03Z [INFO] embed-text.sh: embedded 41 chars -> 768d vector
#
# A log-write failure NEVER aborts the caller — the function swallows every error
# and always returns 0, so it is safe under `set -e` (the whole body is the left
# operand of `|| true`, which disables errexit for its internal commands).
#
# Pure bash + coreutils: no python, no network (matches the substrate
# requests-free constraint). The log directory is resolved best-effort:
#   1. $PODZONE_LOG_DIR          — explicit override (tests / odd layouts)
#   2. $CLAUDE_PROJECT_DIR/logs  — home-repo root Claude Code exports to hooks
#   3. <git toplevel>/logs       — fallback when neither env var is set
#   4. <cwd>/logs                — last resort (not in a git repo)

log_primitive() {
  {
    local component="${1:-unknown}"
    shift 2>/dev/null || true
    local message="$*"
    local base
    if [[ -n "${PODZONE_LOG_DIR:-}" ]]; then
      base="${PODZONE_LOG_DIR}"
    elif [[ -n "${CLAUDE_PROJECT_DIR:-}" ]]; then
      base="${CLAUDE_PROJECT_DIR}/logs"
    else
      base="$(git rev-parse --show-toplevel 2>/dev/null || pwd)/logs"
    fi
    mkdir -p "${base}" 2>/dev/null
    local ts
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    # Sid-keyed committed log (PROJ-039/T-048): the session's primitive lines land in
    # logs/primitives-{sid8}.log (which rides the session-result PR as a per-session
    # diagnostic) when the substrate hook exported PODZONE_SESSION_ID; else the
    # unkeyed primitives.log.
    local logfile="primitives.log"
    if [[ -n "${PODZONE_SESSION_ID:-}" ]]; then
      logfile="primitives-${PODZONE_SESSION_ID:0:8}.log"
    fi
    printf '%s [INFO] %s: %s\n' "${ts}" "${component}" "${message}" >> "${base}/${logfile}"
  } 2>/dev/null || true
  return 0
}

# Allow standalone invocation too: `log_primitive.sh <component> <message...>`.
# (Sourcing is the primary path; this lets a non-bash caller log via a subprocess.)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  log_primitive "$@"
fi
