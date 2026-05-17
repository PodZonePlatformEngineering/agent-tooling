#!/usr/bin/env bash
# scaffold.sh — create a new agent home repo from the v2.0 template.
# Reference: planning/projects/PROJ-032-agent-home-repos/home-repo-template.md
# Task: PROJ-033/T-007 (CC-269)
#
# Usage:
#   bash scaffold.sh {team} {agent} {role-class} [--target-dir /path] [--force]
#
# Examples:
#   bash scaffold.sh training alex trainer
#   bash scaffold.sh podzone hephaestus coder --target-dir /tmp/test-home
set -euo pipefail

AGENT_TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCAFFOLD_DIR="${AGENT_TOOLING_DIR}/scaffold"
HOOKS_DIR="${AGENT_TOOLING_DIR}/hooks"

VALID_ROLES="team-lead coder archivist trainer cluster-operator"

usage() {
  echo "Usage: bash scaffold.sh {team} {agent} {role-class} [--target-dir /path] [--force]"
  echo ""
  echo "Valid role classes: ${VALID_ROLES}"
  echo ""
  echo "Examples:"
  echo "  bash scaffold.sh training alex trainer"
  echo "  bash scaffold.sh podzone hephaestus coder --target-dir /tmp/test-home"
  exit 1
}

# --- Argument parsing ---

TEAM=""
AGENT=""
ROLE=""
TARGET_DIR=""
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-dir) TARGET_DIR="$2"; shift 2 ;;
    --force)      FORCE=1; shift ;;
    --help|-h)    usage ;;
    -*)           echo "Unknown flag: $1"; usage ;;
    *)
      if   [[ -z "$TEAM" ]];  then TEAM="$1"
      elif [[ -z "$AGENT" ]]; then AGENT="$1"
      elif [[ -z "$ROLE" ]];  then ROLE="$1"
      else echo "Unexpected argument: $1"; usage
      fi
      shift
      ;;
  esac
done

[[ -z "$TEAM" || -z "$AGENT" || -z "$ROLE" ]] && { echo "Error: team, agent, and role-class are required."; usage; }

# Validate role class
if ! echo "$VALID_ROLES" | grep -qw "$ROLE"; then
  echo "Error: unknown role-class '${ROLE}'"
  echo "Valid role classes: ${VALID_ROLES}"
  exit 1
fi

REPO_NAME="home-${TEAM}-${AGENT}"
[[ -z "$TARGET_DIR" ]] && TARGET_DIR="${HOME}/workspace/${REPO_NAME}"
AGENT_CAP="$(echo "${AGENT:0:1}" | tr '[:lower:]' '[:upper:]')${AGENT:1}"

# --- Check target ---

if [[ -e "$TARGET_DIR" && $FORCE -eq 0 ]]; then
  echo "Error: '${TARGET_DIR}' already exists. Use --force to overwrite."
  exit 1
fi

echo "==> Scaffolding ${REPO_NAME} (role: ${ROLE}) → ${TARGET_DIR}"

# --- Role definitions ---

role_hooks() {
  case "$1" in
    team-lead)        echo "startup.sh session-end.sh stop.sh task-event.sh" ;;
    coder)            echo "startup.sh session-end.sh stop.sh task-event.sh subagent-stop.sh" ;;
    archivist)        echo "startup.sh session-end.sh stop.sh ingest-transcript.sh" ;;
    trainer)          echo "startup.sh session-end.sh stop.sh" ;;
    cluster-operator) echo "startup.sh session-end.sh stop.sh task-event.sh subagent-stop.sh" ;;
  esac
}

role_title() {
  case "$1" in
    team-lead)        echo "Team Lead" ;;
    coder)            echo "Coder" ;;
    archivist)        echo "Archivist" ;;
    trainer)          echo "Trainer" ;;
    cluster-operator) echo "Cluster Operator" ;;
  esac
}

role_task_filter() {
  case "$1" in
    team-lead)  echo "Team Lead" ;;
    archivist)  echo "Archivist" ;;
    *)          echo "Claude-Code" ;;
  esac
}

# Build settings.json hook entries for the role
role_settings_json() {
  local role="$1"
  local base_hooks
  base_hooks=$(cat <<'HOOKS'
        { "type": "command", "event": "SessionStart", "command": "bash .claude/hooks/startup.sh" },
        { "type": "command", "event": "SessionEnd",   "command": "bash .claude/hooks/session-end.sh" },
        { "type": "command", "event": "Stop",         "command": "bash .claude/hooks/stop.sh" }
HOOKS
)

  local extra_hooks=""
  case "$role" in
    team-lead|cluster-operator|coder)
      extra_hooks=',
        { "type": "command", "event": "PostToolUse",  "command": "bash .claude/hooks/task-event.sh" }'
      ;;
    archivist)
      extra_hooks=',
        { "type": "command", "event": "PostToolUse",  "command": "bash .claude/hooks/ingest-transcript.sh" }'
      ;;
  esac

  if [[ "$role" == "coder" || "$role" == "cluster-operator" ]]; then
    extra_hooks="${extra_hooks}"',
        { "type": "command", "event": "SubagentStop", "command": "bash .claude/hooks/subagent-stop.sh" }'
  fi

  cat <<SETTINGS
{
  "hooks": [
    {
      "matcher": "",
      "hooks": [
${base_hooks}${extra_hooks}
      ]
    }
  ]
}
SETTINGS
}

# Build AGENTS.md hook table rows (role-specific rows beyond base)
role_hook_rows() {
  case "$1" in
    team-lead|cluster-operator|coder)
      echo "| PostToolUse | \`task-event.sh\` | Record tool invocations to task_events |"
      ;;
    archivist)
      echo "| PostToolUse | \`ingest-transcript.sh\` | Embed user prompts to prompt_logs |"
      ;;
    trainer)
      echo ""
      ;;
  esac
  if [[ "$1" == "coder" || "$1" == "cluster-operator" ]]; then
    echo "| SubagentStop | \`subagent-stop.sh\` | Record subagent outcomes |"
  fi
}

# --- Create directory structure ---

mkdir -p \
  "${TARGET_DIR}/.claude/hooks" \
  "${TARGET_DIR}/workspaces/identity" \
  "${TARGET_DIR}/results" \
  "${TARGET_DIR}/session-reports" \
  "${TARGET_DIR}/memory" \
  "${TARGET_DIR}/context"

# git-track empty dirs
touch "${TARGET_DIR}/results/.gitkeep"
touch "${TARGET_DIR}/session-reports/.gitkeep"
touch "${TARGET_DIR}/memory/.gitkeep"

# --- Copy hooks ---

echo "==> Copying hooks for role '${ROLE}'..."
for hook in $(role_hooks "$ROLE"); do
  src="${HOOKS_DIR}/${hook}"
  if [[ ! -f "$src" ]]; then
    echo "Warning: source hook not found: ${src} — skipping"
    continue
  fi
  cp "$src" "${TARGET_DIR}/.claude/hooks/${hook}"
  chmod +x "${TARGET_DIR}/.claude/hooks/${hook}"
  echo "    copied: ${hook}"
done

# --- .gitignore ---

cp "${SCAFFOLD_DIR}/gitignore.template" "${TARGET_DIR}/.gitignore"

# --- .claude/settings.json ---

role_settings_json "$ROLE" > "${TARGET_DIR}/.claude/settings.json"

# --- AGENTS.md ---

ROLE_TITLE="$(role_title "$ROLE")"
EXTRA_HOOK_ROWS="$(role_hook_rows "$ROLE")"

cat > "${TARGET_DIR}/AGENTS.md" <<AGENTS
# ${AGENT_CAP} — ${ROLE_TITLE}

## Startup

On session start, this repo's hook runs \`.claude/hooks/startup.sh\`, which:

1. Extracts agent identity from the repo directory name
2. Queries Qdrant \`work_items\` for the active approved brief (agent=${AGENT}, status=approved)
3. Materialises \`context/brief.md\` and \`context/identity.yaml\`
4. Assembles and injects 10-section additionalContext before the LLM opens

If no approved brief is found, startup.sh injects identity + state only and
prints a notice: "No active brief — check with team lead for next task."

## Agent

- **Name:** ${AGENT_CAP}
- **Team:** ${TEAM}
- **Role class:** \`agenticflows/roles/${ROLE}/\`
- **Operator:** Martin

## Repos

Working repos cloned into \`.workspace/\`:

- \`agent-tooling\` — always present (hooks, primitives, sync script)
- \`{task-repo}\` — cloned per brief; deleted after PR merge

## Hook set (role: ${ROLE})

| Event | Script | Purpose |
|---|---|---|
| SessionStart | \`startup.sh\` | Identity + brief + 10-section context |
| SessionEnd | \`session-end.sh\` | Materialise results + push + raise PR |
| Stop | \`stop.sh\` | Write session_state snapshot |
${EXTRA_HOOK_ROWS}

## Constraints

- Open sessions via \`${REPO_NAME}.code-workspace\` only
- Do not open task repos directly — identity resolution requires home repo as CWD
- \`context/\` is ephemeral — do not commit its contents
AGENTS

# --- README.md ---

cat > "${TARGET_DIR}/README.md" <<README
# ${REPO_NAME} — ${AGENT_CAP} (${ROLE_TITLE})

Home repo for the ${AGENT_CAP} agent (team: ${TEAM}, role: ${ROLE}).

Open \`${REPO_NAME}.code-workspace\` to start a session.
README

# --- .claude/instructions.md ---

cat > "${TARGET_DIR}/.claude/instructions.md" <<INSTRUCTIONS
Role: ${ROLE_TITLE} — FILL IN one-line summary of primary responsibility
Team: ${TEAM}; operator: Martin (system-owner)
Task source: context/brief.md (pulled from Qdrant work_items at session start)
Cross-team work: raise draft in podzoneAgentTeam/briefs/{recipient}/ — do not write to other agents' home repos
Results: write to results/session-{date}-{slug}.md; hook pushes and raises PR
Memory: read memory/MEMORY.md; update memory/ when learning something durable
INSTRUCTIONS

# --- .claude/guardrails.md ---

cat > "${TARGET_DIR}/.claude/guardrails.md" <<GUARDRAILS
Never commit context/ — it is ephemeral and gitignored
Never open task repos directly — always use ${REPO_NAME}.code-workspace
FILL IN role-specific prohibition 1
FILL IN role-specific prohibition 2
Secrets via getSecret.sh only — never hardcode or log secret values
If a secret appears in context, stop and notify Martin immediately
GUARDRAILS

# --- .claude/output-format.md ---

cp "${SCAFFOLD_DIR}/output-format.template" "${TARGET_DIR}/.claude/output-format.md"

# --- Identity YAML stub ---

TASK_FILTER="$(role_task_filter "$ROLE")"
WORKSPACE_NAME="martin-${AGENT}-${ROLE}"

cat > "${TARGET_DIR}/workspaces/identity/${WORKSPACE_NAME}.identity.yaml" <<IDENTITY
operator: Martin
operator_mode: system-owner
agent: ${AGENT_CAP}           # FILL IN — capitalised agent name confirmed
scope: FILL_IN                # FILL IN — session scope label
home_repo: ${REPO_NAME}
role_class: agenticflows/roles/${ROLE}/
repos:
  - name: agent-tooling
    purpose: always-present — hooks and primitives source
  # FILL IN — add task repos here
task_filter: "${TASK_FILTER}"
workspace: ${REPO_NAME}
IDENTITY

# --- Workspace file ---

cat > "${TARGET_DIR}/${REPO_NAME}.code-workspace" <<WORKSPACE
{
  "folders": [
    { "name": "${REPO_NAME}", "path": "." },
    { "name": "agent-tooling", "path": ".workspace/agent-tooling" }
  ],
  "settings": {}
}
WORKSPACE

# --- memory/MEMORY.md ---

cat > "${TARGET_DIR}/memory/MEMORY.md" <<MEMINDEX
# Memory Index
MEMINDEX

# --- Summary ---

echo ""
echo "Scaffold complete: ${TARGET_DIR}"
echo ""
echo "FILL IN checklist — complete before opening the workspace:"
echo "  [ ] Edit workspaces/identity/${WORKSPACE_NAME}.identity.yaml"
echo "        — set scope, add task repos"
echo "  [ ] Edit .claude/instructions.md — fill in role behaviour rules"
echo "  [ ] Edit .claude/guardrails.md   — fill in role-specific prohibitions"
echo "  [ ] Create repo '${REPO_NAME}' in PodZonePlatformEngineering and push"
echo "  [ ] Clone agent-tooling into .workspace/agent-tooling/"
echo "        git clone https://github.com/PodZonePlatformEngineering/agent-tooling.git .workspace/agent-tooling"
echo "  [ ] Open ${REPO_NAME}.code-workspace and confirm startup.sh fires"
