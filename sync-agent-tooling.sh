#!/usr/bin/env bash
# sync-agent-tooling.sh — re-apply hook updates to an existing v2.0 home repo.
# Reference: planning/projects/PROJ-032-agent-home-repos/home-repo-template.md §13
# Task: PROJ-033/T-007 (CC-269)
#
# Usage (from inside the home repo, via .workspace clone):
#   bash .workspace/agent-tooling/sync-agent-tooling.sh --role {role-class}
#
# Or with explicit paths:
#   bash sync-agent-tooling.sh --role {role-class} \
#     --home-repo /path/to/home-repo \
#     --agent-tooling /path/to/agent-tooling
#
# Flags:
#   --role {role-class}          Required (or auto-detected from identity YAML)
#   --home-repo /path            Default: git root of CWD
#   --agent-tooling /path        Default: directory containing this script
#   --yes                        Skip confirmation prompts (for automation)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VALID_ROLES="team-lead coder archivist trainer cluster-operator"

usage() {
  echo "Usage: bash sync-agent-tooling.sh --role {role-class} [--home-repo /path] [--agent-tooling /path] [--yes]"
  echo ""
  echo "Valid role classes: ${VALID_ROLES}"
  exit 1
}

# --- Argument parsing ---

ROLE=""
HOME_REPO=""
AGENT_TOOLING_DIR="$SCRIPT_DIR"
YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role)          ROLE="$2"; shift 2 ;;
    --home-repo)     HOME_REPO="$2"; shift 2 ;;
    --agent-tooling) AGENT_TOOLING_DIR="$2"; shift 2 ;;
    --yes)           YES=1; shift ;;
    --help|-h)       usage ;;
    *) echo "Unknown argument: $1"; usage ;;
  esac
done

# Resolve home repo from git root if not specified
if [[ -z "$HOME_REPO" ]]; then
  HOME_REPO="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi

# Auto-detect role from identity YAML if --role not given
if [[ -z "$ROLE" ]]; then
  IDENTITY_FILE="$(find "${HOME_REPO}/workspaces/identity" -name "*.identity.yaml" 2>/dev/null | head -1)"
  if [[ -n "$IDENTITY_FILE" ]]; then
    ROLE="$(grep '^role_class:' "$IDENTITY_FILE" | sed 's|.*roles/\(.*\)/.*|\1|' | tr -d ' ')"
    echo "==> Auto-detected role '${ROLE}' from ${IDENTITY_FILE}"
  fi
fi

if [[ -z "$ROLE" ]]; then
  echo "Error: --role is required (or provide a workspaces/identity/*.identity.yaml with role_class)"
  usage
fi

if ! echo "$VALID_ROLES" | grep -qw "$ROLE"; then
  echo "Error: unknown role-class '${ROLE}'"
  echo "Valid role classes: ${VALID_ROLES}"
  exit 1
fi

HOOKS_SRC="${AGENT_TOOLING_DIR}/hooks"
HOOKS_DST="${HOME_REPO}/.claude/hooks"

if [[ ! -d "$HOOKS_SRC" ]]; then
  echo "Error: agent-tooling hooks directory not found: ${HOOKS_SRC}"
  exit 1
fi

if [[ ! -d "$HOOKS_DST" ]]; then
  echo "Error: home repo .claude/hooks/ not found: ${HOOKS_DST}"
  echo "Is this a v2.0 home repo? Run scaffold.sh to create a fresh one."
  exit 1
fi

# --- Role hook set ---

# NOTE (PROJ-039/T-011 C2a): the v2.0 template names below (startup.sh /
# session-end.sh / task-event.sh) are STUBS; the functional hooks are
# session-start.sh / post-tool-use.sh + the per-Stop telemetry chain. Adopting
# the real working set here must be done together with scaffold.sh's role_hooks
# AND role_settings_json (grouped settings format) so scaffold+sync stay
# consistent — that is the template v2.1 package (see c2a-canary/migration-recipe.md).
# Left on the existing set in C2a to keep scaffold+sync coherent; the canary
# relocated the real hooks manually.
role_hooks() {
  case "$1" in
    team-lead)        echo "startup.sh session-end.sh stop.sh task-event.sh" ;;
    coder)            echo "startup.sh session-end.sh stop.sh task-event.sh subagent-stop.sh" ;;
    archivist)        echo "startup.sh session-end.sh stop.sh ingest-transcript.sh" ;;
    trainer)          echo "startup.sh session-end.sh stop.sh" ;;
    cluster-operator) echo "startup.sh session-end.sh stop.sh task-event.sh subagent-stop.sh" ;;
  esac
}

# --- Sync ---

UPDATED=0
UNCHANGED=0
SKIPPED=0

echo "==> Syncing hooks for role '${ROLE}' (${HOME_REPO})"
echo ""

for hook in $(role_hooks "$ROLE"); do
  src="${HOOKS_SRC}/${hook}"
  dst="${HOOKS_DST}/${hook}"

  if [[ ! -f "$src" ]]; then
    echo "  SKIP  ${hook} — not found in agent-tooling (${src})"
    ((SKIPPED++))
    continue
  fi

  if [[ -f "$dst" ]] && diff -q "$src" "$dst" > /dev/null 2>&1; then
    echo "  OK    ${hook} — unchanged"
    ((UNCHANGED++))
    continue
  fi

  if [[ -f "$dst" ]]; then
    echo "  DIFF  ${hook}:"
    diff -u "$dst" "$src" || true
    echo ""
  else
    echo "  NEW   ${hook} — not present in home repo"
  fi

  if [[ $YES -eq 0 ]]; then
    printf "  Overwrite %s? [y/N] " "$hook"
    read -r answer </dev/tty
    if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
      echo "  Skipped."
      ((SKIPPED++))
      continue
    fi
  fi

  cp "$src" "$dst"
  chmod +x "$dst"
  echo "  Updated: ${hook}"
  ((UPDATED++))
done

echo ""
echo "Sync complete: ${UPDATED} updated, ${UNCHANGED} unchanged, ${SKIPPED} skipped."
