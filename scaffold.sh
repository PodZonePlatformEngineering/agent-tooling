#!/usr/bin/env bash
# scaffold.sh — create a new, self-contained agent home repo (template v2.1).
# Emits the real PROJ-039 substrate hook set + grouped settings.json, and copies
# primitives/ + lib/ resident under .claude/ so the repo runs with no
# AGENT_TOOLING_DIR and no agent-tooling clone on the discovery path (ADR-008 D2).
# Reference: planning/projects/PROJ-032-agent-home-repos/home-repo-template.md
# Tasks: PROJ-033/T-007 (CC-269); PROJ-039/T-011 C2-v2.1 (self-containment)
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
LIB_MANIFEST="${HOOKS_DIR}/home-runtime-lib.manifest"

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

# Real PROJ-039 substrate working set (template v2.1) — kept byte-identical with
# sync-agent-tooling.sh's role_hooks. Universal substrate emits session telemetry
# + tasking for every role; subagent-spawning roles add the SubagentStop chain.
# session-end-finalise.py (SessionEnd) anchors the self-contained session-end
# lifecycle — telemetry push → rollup → CST prune → session-finalise — and is
# universal (every role finalises). PROJ-039/T-011 C2-v2.1c.
# archivist additionally carries the resident ingest-transcript SessionEnd hook
# (embed user turns → Qdrant prompt_logs): a role nuance surfaced by C2b — it must
# be home-repo-resident, not workstation-global. PROJ-039/T-011 C2b.
SUBSTRATE_BASE="session-start.sh user-prompt-submit.sh pre-tool-use.sh post-tool-use.sh post-compact.sh stop.sh append-session-stop.py session-end-finalise.py"
role_hooks() {
  case "$1" in
    team-lead)        echo "${SUBSTRATE_BASE}" ;;
    coder)            echo "${SUBSTRATE_BASE} subagent-stop.sh subagent-stop.py" ;;
    archivist)        echo "${SUBSTRATE_BASE} ingest-transcript.sh ingest-transcript.py" ;;
    trainer)          echo "${SUBSTRATE_BASE}" ;;
    cluster-operator) echo "${SUBSTRATE_BASE} subagent-stop.sh subagent-stop.py" ;;
  esac
}

# Resident dependency dirs copied wholesale into .claude/ for self-containment.
# lib/ is deliberately NOT in this list — it is copied module-by-module from
# home-runtime-lib.manifest (the runtime closure), never as the whole 26-module
# agent-tooling lib/ (PROJ-039/T-011 C2-v2.1b — avoids shipping workstation/Hermes
# -only code into every home repo). Kept byte-identical with sync-agent-tooling.sh.
DEP_DIRS="primitives"

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

# Build .claude/settings.json in the real grouped Claude Code hook format
# (template v2.1) — universal PROJ-039 substrate events for every role; the
# SubagentStop chain is added only for subagent-spawning roles. Mirrors the
# Hephaestus canary settings.json.
role_settings_json() {
  local role="$1"
  local subagent_stop=""
  if [[ "$role" == "coder" || "$role" == "cluster-operator" ]]; then
    subagent_stop=',
    "SubagentStop": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "bash .claude/hooks/subagent-stop.sh" } ] }
    ]'
  fi
  # archivist: ingest the transcript to Qdrant prompt_logs on SessionEnd, alongside
  # the universal session-end-finalise.py. Resident hook (PROJ-039/T-011 C2b).
  local session_end_ingest=""
  if [[ "$role" == "archivist" ]]; then
    session_end_ingest=', { "type": "command", "command": "bash .claude/hooks/ingest-transcript.sh" }'
  fi

  cat <<SETTINGS
{
  "hooks": {
    "SessionStart": [
      { "matcher": "startup|resume", "hooks": [ { "type": "command", "command": "bash .claude/hooks/session-start.sh" } ] }
    ],
    "UserPromptSubmit": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "bash .claude/hooks/user-prompt-submit.sh" } ] }
    ],
    "PreToolUse": [
      { "matcher": "*", "hooks": [ { "type": "command", "command": "bash .claude/hooks/pre-tool-use.sh" } ] }
    ],
    "PostToolUse": [
      { "matcher": "*", "hooks": [ { "type": "command", "command": "bash .claude/hooks/post-tool-use.sh" } ] }
    ],
    "PostCompact": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "bash .claude/hooks/post-compact.sh" } ] }
    ],
    "Stop": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "bash .claude/hooks/stop.sh" } ] }
    ],
    "SessionEnd": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "python3 .claude/hooks/session-end-finalise.py", "timeout": 600 }${session_end_ingest} ] }
    ]${subagent_stop}
  }
}
SETTINGS
}

# Build AGENTS.md hook table rows (role-specific rows beyond the universal base).
# The PROJ-039 substrate (SessionStart/UserPromptSubmit/Pre+PostToolUse/
# PostCompact/Stop) is universal; only the SubagentStop chain is role-specific.
role_hook_rows() {
  if [[ "$1" == "coder" || "$1" == "cluster-operator" ]]; then
    echo "| SubagentStop | \`subagent-stop.sh\` | Record subagent outcomes to task_events |"
  fi
  if [[ "$1" == "archivist" ]]; then
    echo "| SessionEnd | \`ingest-transcript.sh\` | Embed user turns → Qdrant \`prompt_logs\` (resident) |"
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

# --- Copy resident dependencies (primitives/ + lib/) ---
# Self-containment (ADR-008 D2): the hooks resolve primitives via
# ${SCRIPT_DIR}/../primitives and lib via parents[1], both landing inside
# .claude/ — no AGENT_TOOLING_DIR, no agent-tooling clone on the discovery path.

echo "==> Copying resident dependencies (${DEP_DIRS}) into .claude/..."
for dep in $DEP_DIRS; do
  src="${AGENT_TOOLING_DIR}/${dep}"
  if [[ ! -d "$src" ]]; then
    echo "Warning: source dependency dir not found: ${src} — skipping"
    continue
  fi
  cp -R "$src" "${TARGET_DIR}/.claude/${dep}"
  find "${TARGET_DIR}/.claude/${dep}" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
  echo "    copied: ${dep}/"
done

# --- Copy runtime lib closure (home-runtime-lib.manifest) ---
# lib/ is NOT copied wholesale: only the modules in home-runtime-lib.manifest (the
# transitive import closure of the shipped hooks) land in .claude/lib/. This keeps
# the home repo free of workstation/Hermes-only code (decay detector, one-shots,
# harnesses, reporting). PROJ-039/T-011 C2-v2.1b. Kept identical with sync-agent-tooling.sh.

echo "==> Copying runtime lib closure (home-runtime-lib.manifest) into .claude/lib/..."
if [[ ! -f "$LIB_MANIFEST" ]]; then
  echo "Error: lib manifest not found: ${LIB_MANIFEST}"
  exit 1
fi
LIB_COUNT=0
while IFS= read -r line || [[ -n "$line" ]]; do
  entry="${line%%#*}"; entry="${entry//[[:space:]]/}"
  [[ -z "$entry" ]] && continue
  src="${AGENT_TOOLING_DIR}/lib/${entry}"
  dst="${TARGET_DIR}/.claude/lib/${entry}"
  if [[ ! -f "$src" ]]; then
    echo "Error: manifest lists '${entry}' but source is missing: ${src}"
    exit 1
  fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  LIB_COUNT=$((LIB_COUNT + 1))
done < "$LIB_MANIFEST"
echo "    copied: lib/ (${LIB_COUNT} modules from manifest)"

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

On session start, this repo's \`.claude/hooks/session-start.sh\` runs, which:

1. Extracts agent identity from the repo directory name
2. Queries Qdrant \`work_items\` for the active approved brief (agent=${AGENT}, status=approved)
3. Materialises \`context/brief.md\` and \`context/identity.yaml\`
4. Assembles and injects session context before the LLM opens

If no approved brief is found, session-start.sh injects identity + state only and
prints a notice: "No active brief — check with team lead for next task."

## Agent

- **Name:** ${AGENT_CAP}
- **Team:** ${TEAM}
- **Role class:** \`agenticflows/roles/${ROLE}/\`
- **Operator:** Martin

## Repos

This home repo is **self-contained** — its hooks, \`primitives/\`, and the \`lib/\`
runtime closure are resident under \`.claude/\` (no AGENT_TOOLING_DIR, no agent-tooling
clone needed to run). \`agent-tooling\` is the canonical **sync source**, not a runtime
dependency: clone it into \`.workspace/\` on demand — to pull hook/dependency updates, or
when a brief tasks you with agent-tooling itself — then run
\`.workspace/agent-tooling/sync-agent-tooling.sh --role ${ROLE}\` (keeps the resident set
byte-identical to source). Task repos are likewise cloned into \`.workspace/\` per brief
and deleted after PR merge.

## Hook set (role: ${ROLE})

| Event | Script | Purpose |
|---|---|---|
| SessionStart | \`session-start.sh\` | Identity + brief + session context |
| UserPromptSubmit | \`user-prompt-submit.sh\` | Record prompt telemetry |
| PreToolUse | \`pre-tool-use.sh\` | Record tool-call telemetry |
| PostToolUse | \`post-tool-use.sh\` | Record tool-result telemetry |
| PostCompact | \`post-compact.sh\` | Record compaction telemetry |
| Stop | \`stop.sh\` | CST Stop point + session_stop[] tasking append |
| SessionEnd | \`session-end-finalise.py\` | Telemetry push → rollup → CST prune (post-push) → session-finalise |
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
    purpose: canonical sync source for sync-agent-tooling.sh — cloned to .workspace/ on demand; not required at runtime (self-contained)
  # FILL IN — add task repos here
task_filter: "${TASK_FILTER}"
workspace: ${REPO_NAME}
IDENTITY

# --- Workspace file ---

# Committed default: the self-contained home repo folder alone. agent-tooling and task
# repos are added under .workspace/ (and here as folders) on demand per brief — agent-tooling
# is the sync source, not a permanent fixture (PROJ-039/T-011 C2-v2.1b).
cat > "${TARGET_DIR}/${REPO_NAME}.code-workspace" <<WORKSPACE
{
  "folders": [
    { "name": "${REPO_NAME}", "path": "." }
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
echo "        (hooks + primitives/ + lib/ are resident under .claude/ — the repo"
echo "         runs self-contained; no agent-tooling clone is required to fire hooks)"
echo "  [ ] Set PODZONE_QDRANT_APIKEY in .claude/settings.local.json (workstation only)"
echo "  [ ] Open ${REPO_NAME}.code-workspace and confirm session-start.sh fires"
