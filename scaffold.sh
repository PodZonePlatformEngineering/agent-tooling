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
SKILLS_SRC="${AGENT_TOOLING_DIR}/skills"
# The team-lead variant (and ONLY it) carries this coordination skill subset under
# .claude/skills/ (PROJ-039/T-038). Build-agent home repos remain hooks-only.
TEAM_LEAD_SKILLS_MANIFEST="${SCAFFOLD_DIR}/team-lead-skills.manifest"

VALID_ROLES="team-lead coder archivist trainer cluster-operator curriculum-developer historian strategist trainee"

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
# curriculum-developer / historian / strategist are the fissioned-team BUILD agents
# (hestia / clio / kronos) migrated under C2c (PROJ-039/T-037). They are producers
# that do not spawn subagents and carry no automatic transcript-ingest, so they take
# the universal substrate base — same shape as team-lead/trainer. (The historian's
# log/memory ingestion is explicit, agent-invoked toolchain work in its task repo,
# NOT a SessionEnd transcript-embed hook like the archivist's — so no ingest hook.)
# session-materialise.py is in the universal base (PROJ-039/T-052): it is committed
# resident like every other hook — NEVER hand-copied + settings.local.json-wired.
# The hand-copy gotcha bit Thoth's first serial launch (T-022): the copy was omitted,
# the SessionStart wiring pointed at a missing file and failed silently → no brief-first
# session point, session_ids[] never appended, finalise ran "not brief-first". Resident
# + committed-settings.json-wired kills that class.
SUBSTRATE_BASE="session-start.sh session-materialise.py user-prompt-submit.sh post-compact.sh stop.sh stop-telemetry.py append-session-stop.py session-end-finalise.py"
role_hooks() {
  case "$1" in
    team-lead)             echo "${SUBSTRATE_BASE}" ;;
    coder)                 echo "${SUBSTRATE_BASE} subagent-stop.sh subagent-stop.py" ;;
    archivist)             echo "${SUBSTRATE_BASE} ingest-transcript.sh ingest-transcript.py" ;;
    trainer)               echo "${SUBSTRATE_BASE}" ;;
    cluster-operator)      echo "${SUBSTRATE_BASE} subagent-stop.sh subagent-stop.py" ;;
    curriculum-developer)  echo "${SUBSTRATE_BASE}" ;;
    historian)             echo "${SUBSTRATE_BASE}" ;;
    strategist)            echo "${SUBSTRATE_BASE}" ;;
    trainee)               echo "${SUBSTRATE_BASE} first-prompt-brief.py trainee-session-branch.py trainee-preflight.py trainee-read-guard.py" ;;
  esac
}

# Resident dependency dirs copied wholesale into .claude/ for self-containment.
# lib/ is deliberately NOT in this list — it is copied module-by-module from
# home-runtime-lib.manifest (the runtime closure), never as the whole 26-module
# agent-tooling lib/ (PROJ-039/T-011 C2-v2.1b — avoids shipping workstation/Hermes
# -only code into every home repo). Kept byte-identical with sync-agent-tooling.sh.
DEP_DIRS="primitives"

# Role-neutral resident tools (PROJ-039/T-056): single files under tools/ that
# ship to .claude/tools/ for EVERY role (unlike DEP_DIRS this is not a whole-dir
# copy — tools/ upstream also carries workstation/Hermes-only scripts, e.g.
# create-brief.py, that must never land in a home repo). update-tooling.py is
# the brief-gated self-update entry point, wired as the FIRST SessionStart hook
# command; wire-update-tooling.py is the settings.json wiring patcher/verifier
# the sync drives (PROJ-039/T-069). Kept byte-identical with sync-agent-tooling.sh.
TOOLS_FILES="update-tooling.py wire-update-tooling.py"

role_title() {
  case "$1" in
    team-lead)             echo "Team Lead" ;;
    coder)                 echo "Coder" ;;
    archivist)             echo "Archivist" ;;
    trainer)               echo "Trainer" ;;
    cluster-operator)      echo "Cluster Operator" ;;
    curriculum-developer)  echo "Curriculum Developer" ;;
    historian)             echo "Historian" ;;
    strategist)            echo "Strategist" ;;
    trainee)               echo "Trainee" ;;
  esac
}

role_task_filter() {
  case "$1" in
    team-lead)             echo "Team Lead" ;;
    archivist)             echo "Archivist" ;;
    curriculum-developer)  echo "Curriculum Developer" ;;
    historian)             echo "Historian" ;;
    strategist)            echo "Strategist" ;;
    *)                     echo "Claude-Code" ;;
  esac
}

# Build .claude/settings.json in the real grouped Claude Code hook format
# (template v2.1) — universal PROJ-039 substrate events for every role; the
# SubagentStop chain is added only for subagent-spawning roles. Mirrors the
# Hephaestus canary settings.json.
role_settings_json() {
  local role="$1"

  # trainee: a deliberately SLIM env (PROJ-011/T-025 R-14). No PODZONE_TELEMETRY_REMOTE
  # (pushing every student workstation's logs to a fleet telemetry repo will not scale —
  # the finalise copies the session log into logs/ before the session commit instead,
  # riding the R-3 session PR) and no PODZONETEAM_REPO (no apex clone in trainee
  # context). Only TRAINEE_RUNTIME=1 remains. SessionStart also runs the fail-soft
  # preflight (R-13) + branch hook; UserPromptSubmit the brief parser (R-1); PreToolUse
  # the context-containment read guard (R-9).
  if [[ "$role" == "trainee" ]]; then
    cat <<'TRAINEE_SETTINGS'
{
  "env": {
    "TRAINEE_RUNTIME": "1"
  },
  "hooks": {
    "SessionStart": [
      { "matcher": "startup|resume", "hooks": [ { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/session-start.sh" }, { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-preflight.py" }, { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-session-branch.py" }, { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/tools/update-tooling.py", "timeout": 300 } ] }
    ],
    "UserPromptSubmit": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/user-prompt-submit.sh" }, { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/first-prompt-brief.py" } ] }
    ],
    "PreToolUse": [
      { "matcher": "*", "hooks": [ { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-read-guard.py" } ] }
    ],
    "PostCompact": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/post-compact.sh" } ] }
    ],
    "Stop": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/stop.sh" } ] }
    ],
    "SessionEnd": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/session-end-finalise.py", "timeout": 600 } ] }
    ]
  }
}
TRAINEE_SETTINGS
    return 0
  fi

  local subagent_stop=""
  if [[ "$role" == "coder" || "$role" == "cluster-operator" ]]; then
    subagent_stop=',
    "SubagentStop": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/subagent-stop.sh" } ] }
    ]'
  fi
  # archivist: ingest the transcript to Qdrant prompt_logs on SessionEnd, alongside
  # the universal session-end-finalise.py. Resident hook (PROJ-039/T-011 C2b).
  # archivist: transcript ingest is FOLDED INTO the SessionEnd finalise as its last
  # step (PROJ-039/T-053), NOT a second SessionEnd hook. Two heavy SessionEnd hooks
  # (finalise + ingest) overran the CLI teardown budget and cancelled the finalise
  # mid-run on Thoth's headless exits (T-022/T-023). A single hook + PODZONE_INGEST_-
  # TRANSCRIPT=1 env makes the finalise run ingest last (after the load-bearing steps),
  # ledger-tracked so a cancel leaves a recoverable partial.
  local archivist_env=""
  if [[ "$role" == "archivist" ]]; then
    archivist_env='
    "PODZONE_INGEST_TRANSCRIPT": "1",'
  fi

  # trainee: the brief-first trainee runtime (PROJ-011/T-021). A trainee runs no git
  # and pastes a `Brief:` line as the first message. So:
  #   * env TRAINEE_RUNTIME=1 selects the SessionEnd trainee session-PR path (R-3).
  #   * SessionStart also runs trainee-session-branch.py — branch off main (R-2).
  #   * UserPromptSubmit also runs first-prompt-brief.py — parse the brief id from the
  #     first prompt + materialise (R-1). Materialise is NOT wired on SessionStart here
  #     (no pinned sid / BRIEF_ID env) — the first prompt owns it.
  local trainee_env=""
  local session_start_extra=""
  local ups_extra=""
  if [[ "$role" == "trainee" ]]; then
    trainee_env='
    "TRAINEE_RUNTIME": "1",'
    session_start_extra=', { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-session-branch.py" }'
    ups_extra=', { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/first-prompt-brief.py" }'
  fi

  # session-materialise on SessionStart is now COMMITTED-resident wiring (PROJ-039/T-052)
  # — settings.local.json is no longer load-bearing for it. A brief-first launch sets
  # BRIEF_ID (inline or via settings.local env); materialise reads it and stands up the
  # session point. BRIEF_ID unset → materialise runs its legacy session-point-keyed path
  # (a no-op when there is no matching point), so this is safe for every launch mode.
  # NOT wired for the trainee role: there the first prompt owns materialise (first-prompt-
  # brief.py on UserPromptSubmit), because the brief id arrives in the trainee's first
  # message, not as a launch-time BRIEF_ID env.
  local session_start_materialise=""
  if [[ "$role" != "trainee" ]]; then
    session_start_materialise=', { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/session-materialise.py" }'
  fi

  # update-tooling.py (PROJ-039/T-056) is the FIRST SessionStart hook command for
  # every non-trainee role — ahead of session-start.sh / session-materialise.py —
  # so a self-update can refresh the very hook set that runs after it. It reads
  # TOOLING_UPDATE from the env directly (no Qdrant/materialise dependency), so
  # there is no ordering hazard; unset it is a pure no-op. In serial simple-repo
  # mode the launcher has already switched to the session branch before `claude`
  # starts, so a commit this hook makes lands on the session branch (rides the
  # session/result PR, T-060). Trainee wiring differs: it runs AFTER
  # trainee-session-branch.py (TRAINEE_SETTINGS above) because the trainee's own
  # branch switch happens inside a SessionStart hook, not pre-launch — running
  # update-tooling before it would commit onto main instead of the session branch.

  # Telemetry + finalise env (PROJ-039/T-032). Non-secret config only — the
  # agent-telemetry remote the SessionEnd finalise pushes the session JSONL to
  # (R-015 keystone), and the apex repo for the non-migrated finalise path. Secrets
  # (PODZONE_QDRANT_APIKEY) are NEVER embedded here — they ride from the workstation
  # apex env block (~/.claude/settings.json) per the T-016 pattern. A migrated home
  # repo is hooks-only (no skills/): the SessionEnd finalise hook OWNS the session
  # result — step 6 (apex tasklist/STATUS) defers to Hermes /consolidate-tasks, and
  # step 7 authors the result + PR in the home repo's own results/ off home main
  # (PROJ-039/T-035). PODZONETEAM_REPO is inert for migrated repos (the hook
  # must never touch the apex clone) but documents the non-migrated apex path.
  cat <<SETTINGS
{
  "env": {${trainee_env}${archivist_env}
    "PODZONE_TELEMETRY_REMOTE": "https://github.com/PodZonePlatformEngineering/agent-telemetry.git",
    "PODZONETEAM_REPO": "${HOME}/workspace/podzoneTeam"
  },
  "hooks": {
    "SessionStart": [
      { "matcher": "startup|resume", "hooks": [ { "type": "command", "command": "python3 \"\$CLAUDE_PROJECT_DIR\"/.claude/tools/update-tooling.py", "timeout": 300 }, { "type": "command", "command": "bash \"\$CLAUDE_PROJECT_DIR\"/.claude/hooks/session-start.sh" }${session_start_materialise}${session_start_extra} ] }
    ],
    "UserPromptSubmit": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "bash \"\$CLAUDE_PROJECT_DIR\"/.claude/hooks/user-prompt-submit.sh" }${ups_extra} ] }
    ],
    "PostCompact": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "bash \"\$CLAUDE_PROJECT_DIR\"/.claude/hooks/post-compact.sh" } ] }
    ],
    "Stop": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "bash \"\$CLAUDE_PROJECT_DIR\"/.claude/hooks/stop.sh" } ] }
    ],
    "SessionEnd": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "python3 \"\$CLAUDE_PROJECT_DIR\"/.claude/hooks/session-end-finalise.py", "timeout": 600 } ] }
    ]${subagent_stop}
  }
}
SETTINGS
}

# Build AGENTS.md hook table rows (role-specific rows beyond the universal base).
# The PROJ-039 substrate (SessionStart/UserPromptSubmit/PostCompact/Stop) is
# universal; only the SubagentStop chain is role-specific. Pre/PostToolUse CST
# writers retired at T-073 (CC-383): 91% of the collection was per-tool-call
# noise; tool counts live in the sessions rollups.
role_hook_rows() {
  if [[ "$1" == "coder" || "$1" == "cluster-operator" ]]; then
    echo "| SubagentStop | \`subagent-stop.sh\` | Record subagent outcomes to task_events |"
  fi
  if [[ "$1" == "archivist" ]]; then
    echo "| SessionEnd | \`session-end-finalise.py\` → \`ingest-transcript.sh\` | Transcript ingest is FOLDED INTO the finalise as its last step (single SessionEnd hook), gated by \`PODZONE_INGEST_TRANSCRIPT=1\` — PROJ-039/T-053 |"
  fi
}

# --- Create directory structure ---

# Layout by variant (PROJ-039/T-035 + T-038):
#   * BUILD-agent home repo (coder/archivist/trainer/curriculum-developer/historian/
#     strategist/cluster-operator) is HOOKS-ONLY: .claude/ carries hooks/ + lib/ +
#     primitives/ but NO skills/. Agent ceremony is fully hook-driven (SessionStart
#     materialise, SessionEnd finalise); there is no `/session-end` skill.
#   * TEAM-LEAD home repo is a clean superset: the same hooks-only base PLUS the
#     coordination skill subset (team-lead-skills.manifest) under .claude/skills/, so
#     a fissioned lead can /consolidate-tasks + /launch-session for its team. Result
#     authoring still rides the SessionEnd finalise hook (T-035) — session-end is NOT
#     added as a skill.
# (The Hermes apex repo stays fully skill-based — a separate, deliberately different layout.)
mkdir -p \
  "${TARGET_DIR}/.claude/hooks" \
  "${TARGET_DIR}/workspaces/identity" \
  "${TARGET_DIR}/results" \
  "${TARGET_DIR}/session-reports" \
  "${TARGET_DIR}/memory"

# git-track empty dirs
touch "${TARGET_DIR}/results/.gitkeep"
touch "${TARGET_DIR}/session-reports/.gitkeep"
touch "${TARGET_DIR}/memory/.gitkeep"

# --- Trainee structure (PROJ-011/T-025 R-5, R-6, R-10, R-13) ---
# The generated trainee repo carries a directory named for the trainee (placeholder
# "Trainee" in the template; personalise-trainee.py rewrites it to the handle at setup
# — R-8), equivalent to academy-prompt-engineering/trainees/{name}/. Session work lands
# inside it, never at the repo root (baseline defect: answer.md at root). Plus logs/
# (R-6) and docs/ (R-13). Non-trainee roles are unchanged.
TRAINEE_NAME="Trainee"
if [[ "$ROLE" == "trainee" ]]; then
  mkdir -p \
    "${TARGET_DIR}/${TRAINEE_NAME}/sourceDocs" \
    "${TARGET_DIR}/${TRAINEE_NAME}/inputDocs" \
    "${TARGET_DIR}/${TRAINEE_NAME}/outputDocs" \
    "${TARGET_DIR}/${TRAINEE_NAME}/sessions" \
    "${TARGET_DIR}/logs" \
    "${TARGET_DIR}/docs"
  touch \
    "${TARGET_DIR}/${TRAINEE_NAME}/sourceDocs/.gitkeep" \
    "${TARGET_DIR}/${TRAINEE_NAME}/inputDocs/.gitkeep" \
    "${TARGET_DIR}/${TRAINEE_NAME}/outputDocs/.gitkeep" \
    "${TARGET_DIR}/${TRAINEE_NAME}/sessions/.gitkeep" \
    "${TARGET_DIR}/logs/.gitkeep"

  # R-13 docs: dependency analysis + trainer-assisted workstation setup.
  cp "${SCAFFOLD_DIR}/trainee/docs/dependency-analysis.md" "${TARGET_DIR}/docs/dependency-analysis.md"
  cp "${SCAFFOLD_DIR}/trainee/docs/workstation-setup.md"    "${TARGET_DIR}/docs/workstation-setup.md"

  # R-10 profile: the profile template's canonical home is this template family (docs/);
  # seed the trainee's own trainee-profile.md from it. The academy _template/ copy becomes
  # a pointer (retired with the data take-on, T-026..T-029).
  cp "${SCAFFOLD_DIR}/trainee/docs/trainee-profile-template.md" "${TARGET_DIR}/docs/trainee-profile-template.md"
  cp "${SCAFFOLD_DIR}/trainee/docs/trainee-profile-template.md" "${TARGET_DIR}/${TRAINEE_NAME}/trainee-profile.md"

  # Session-management set, equivalent to academy-prompt-engineering/trainees/{name}/.
  cat > "${TARGET_DIR}/${TRAINEE_NAME}/training-state.md" <<'TSTATE'
# Training state

Where you are in the curriculum. Updated as sessions progress.

- **Current module:** (not started)
- **Sessions completed:** 0
- **Next up:** paste your `Brief:` line to begin (see README.md).
TSTATE
  cat > "${TARGET_DIR}/${TRAINEE_NAME}/feedback.md" <<'TFEEDBACK'
# Feedback

Your trainer's running feedback across sessions.
TFEEDBACK
  cat > "${TARGET_DIR}/${TRAINEE_NAME}/improvement-recommendations.md" <<'TIMPROVE'
# Improvement recommendations

Concrete, prioritised recommendations from your trainer.
TIMPROVE
fi

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

# --- Copy role-neutral resident tools (TOOLS_FILES) ---
# Single files, not a whole-dir copy (tools/ upstream also carries workstation
# -only scripts). Ships for EVERY role. PROJ-039/T-056.

echo "==> Copying resident tools (${TOOLS_FILES}) into .claude/tools/..."
mkdir -p "${TARGET_DIR}/.claude/tools"
for tool in $TOOLS_FILES; do
  src="${AGENT_TOOLING_DIR}/tools/${tool}"
  if [[ ! -f "$src" ]]; then
    echo "Warning: source tool not found: ${src} — skipping"
    continue
  fi
  cp "$src" "${TARGET_DIR}/.claude/tools/${tool}"
  chmod +x "${TARGET_DIR}/.claude/tools/${tool}"
  echo "    copied: tools/${tool}"
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

# --- Copy coordination skills (team-lead variant only) ---
# A team lead is the ONLY home-repo class that carries skills: the coordination
# subset it invokes to lead its team (consolidate-tasks, launch-session). Build
# agents get nothing here. Copied byte-identical from the canonical agent-tooling/
# skills/ source per team-lead-skills.manifest; test_skills_parity.py enforces both
# the byte-identity and the "subset only" shape. PROJ-039/T-038.

if [[ "$ROLE" == "team-lead" ]]; then
  echo "==> Copying coordination skills (team-lead variant) into .claude/skills/..."
  if [[ ! -f "$TEAM_LEAD_SKILLS_MANIFEST" ]]; then
    echo "Error: team-lead skills manifest not found: ${TEAM_LEAD_SKILLS_MANIFEST}"
    exit 1
  fi
  mkdir -p "${TARGET_DIR}/.claude/skills"
  SKILL_COUNT=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    entry="${line%%#*}"; entry="${entry//[[:space:]]/}"
    [[ -z "$entry" ]] && continue
    src="${SKILLS_SRC}/${entry}"
    dst="${TARGET_DIR}/.claude/skills/${entry}"
    if [[ ! -d "$src" ]]; then
      echo "Error: manifest lists skill '${entry}' but source is missing: ${src}"
      exit 1
    fi
    rm -rf "$dst"
    cp -R "$src" "$dst"
    find "$dst" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
    SKILL_COUNT=$((SKILL_COUNT + 1))
    echo "    copied: skills/${entry}"
  done < "$TEAM_LEAD_SKILLS_MANIFEST"
  echo "    copied: skills/ (${SKILL_COUNT} coordination skills)"
fi

# --- .gitignore ---
# Session log files are COMMITTED for EVERY role now (PROJ-039/T-048): the sid-keyed
# logs/ files (libraries-{sid8}.log, primitives-{sid8}.log) ride the session-result
# PR as durable per-session diagnostics — the trainee R-14 decision, generalised
# fleet-wide. The template no longer ignores *.log, so a plain copy is correct for
# all roles (the former trainee-only *.log grep is gone).
cp "${SCAFFOLD_DIR}/gitignore.template" "${TARGET_DIR}/.gitignore"

# --- .claude/settings.json ---

role_settings_json "$ROLE" > "${TARGET_DIR}/.claude/settings.json"

# --- AGENTS.md ---

ROLE_TITLE="$(role_title "$ROLE")"
EXTRA_HOOK_ROWS="$(role_hook_rows "$ROLE")"

# Coordination-skills section (team-lead variant only). Build agents render nothing.
COORD_SKILLS_SECTION=""
if [[ "$ROLE" == "team-lead" ]]; then
  COORD_SKILLS_SECTION="
## Coordination skills (team-lead variant)

This is the **team-lead** home-repo variant (PROJ-039/T-038): the hooks-only base
**plus** the coordination skills a fissioned lead invokes to lead its team. These live
under \`.claude/skills/\` and are kept byte-identical to the canonical
\`agent-tooling/skills/\` source (parity enforced by test_skills_parity.py):

| Skill | Purpose |
|---|---|
| \`/consolidate-tasks\` | Local-mode consolidation of this lead's **team repo** (\`${TEAM}Team\`) — outbox/results scan, tasklist + STATUS, session-PR review |
| \`/launch-session\` | Launch this lead's team agents |

Result authoring is NOT a skill — the SessionEnd finalise hook owns the session
result (PROJ-039/T-035), exactly as for a build agent. Session ceremony stays
hook-driven; \`/session-start\` and \`/session-end\` are deliberately not present.

**home_repo ≠ team_repo:** this home repo (\`${REPO_NAME}\`) is NOT the team repo. The
coordination skills resolve the team repo from identity (\`home-<team>-<agent>\` →
\`<team>Team\`); the canonical resolver is \`lib/team_repo.py\` in agent-tooling (cloned
to \`.workspace/\` on demand). Clone \`${TEAM}Team\` into \`.workspace/\` to consolidate/launch.
"
fi

if [[ "$ROLE" == "trainee" ]]; then
  # R-7/R-8: the trainee AGENTS.md is an operating manual ONLY — no scaffold provenance,
  # no R-/T-numbers, no headless model (trainee sessions are interactive), no template
  # naming. Rendered from the trainee source with the placeholder name substituted.
  sed -e "s|__TRAINEE__|${TRAINEE_NAME}|g" -e "s|__REPO_NAME__|${REPO_NAME}|g" \
    "${SCAFFOLD_DIR}/trainee/AGENTS.template" > "${TARGET_DIR}/AGENTS.md"
else
cat > "${TARGET_DIR}/AGENTS.md" <<AGENTS
# ${AGENT_CAP} — ${ROLE_TITLE}

## Startup

On session start, the resident SessionStart hooks run \`session-start.sh\` (identity +
telemetry) and \`session-materialise.py\`, which:

1. Resolves \`BRIEF_ID\` (brief-first launch) or falls back to a legacy session-point
   lookup for this agent
2. Reads the resolved brief/tasking from the cloud \`session_substrate\` / \`briefs\`
   Qdrant collections
3. Materialises \`.workspace/brief.md\`, \`.workspace/tasks.json\`, and
   \`.workspace/identity.json\` from that read, plus a \`.workspace/.materialise-status.json\`
   sentinel
4. Assembles and injects session context before the LLM opens

If materialisation fails (Qdrant unreachable, no active brief, stale read), the sentinel
carries \`ok: false\` and the orientation procedure HALTs rather than fabricating a stale
\`.workspace\` — see \`.workspace/.materialise-status.json\`.

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
| PostCompact | \`post-compact.sh\` | Record compaction telemetry |
| Stop | \`stop.sh\` | Enriched CST Stop point (via \`stop-telemetry.py\`) + session_stop[] tasking append |
| SessionEnd | \`session-end-finalise.py\` | Telemetry push → rollup → CST prune (post-push) → session-finalise |
${EXTRA_HOOK_ROWS}
${COORD_SKILLS_SECTION}
## Headless operating model (PROJ-039/T-040 + T-041)

Autonomous builds are launched **headless** (\`claude --session-id {uuid} -p "<continue+escalate
prompt>"\`) — a non-interactive one-shot, not a long-lived interactive session. The continue+escalate
prompt instructs: *continue the brief, commit and push all PRs when done, and if anything needs
operator direction you cannot resolve, raise it to the team lead with progress so far via the
session response and exit.* This avoids the resume-reload prompt-cache tax of continuing an
interactive session after a subscription-limit halt; a limit-exit is the **preferred** failure
mode (re-launch **fresh** from committed state — never resume).

**Operating rule (build agents AND the team-lead variant):** when a brief cannot complete without
operator direction, **raise to the team lead with progress so far via the session response, and
exit.** Do **not** block interactively and do **not** \`AskUserQuestion\`-and-wait — \`AskUserQuestion\`
is incompatible with headless (no operator on the line). The escalation channel is the substrate
**response**: the SessionEnd finalise records it and the team lead picks it up at
\`/consolidate-tasks\`. For self-contained briefs, prefer *pick the simplest reasonable option, note
it for the team lead, and keep going* over escalating; reserve raise-and-exit for genuine
operator-only decisions. (Team-lead apex / Hermes stays interactive by design — this model is for
migrated home-repo agents.)

## Constraints

- Open sessions via \`${REPO_NAME}.code-workspace\` only
- Do not open task repos directly — identity resolution requires home repo as CWD
- \`.workspace/\` is ephemeral (materialised fresh each session, plus on-demand repo
  clones) — do not commit its contents
AGENTS
fi

# --- README.md ---

if [[ "$ROLE" == "trainee" ]]; then
  # R-7: trainee operating manual (structure map + launch/review rituals), no provenance.
  sed -e "s|__TRAINEE__|${TRAINEE_NAME}|g" -e "s|__REPO_NAME__|${REPO_NAME}|g" \
    "${SCAFFOLD_DIR}/trainee/README.template" > "${TARGET_DIR}/README.md"
else
cat > "${TARGET_DIR}/README.md" <<README
# ${REPO_NAME} — ${AGENT_CAP} (${ROLE_TITLE})

Home repo for the ${AGENT_CAP} agent (team: ${TEAM}, role: ${ROLE}).

Open \`${REPO_NAME}.code-workspace\` to start a session.
README
fi

# --- .claude/instructions.md + guardrails.md + output-format.md ---
# Trainee (R-9/R-12): operator = "Trainee", interactive (no headless model), context
# containment; training-appropriate output format. Rendered from the trainee source with
# the placeholder name substituted — no FILL-IN survives generation (R-8).

if [[ "$ROLE" == "trainee" ]]; then
  sed "s|__TRAINEE__|${TRAINEE_NAME}|g" "${SCAFFOLD_DIR}/trainee/instructions.template" > "${TARGET_DIR}/.claude/instructions.md"
  sed "s|__TRAINEE__|${TRAINEE_NAME}|g" "${SCAFFOLD_DIR}/trainee/guardrails.template"   > "${TARGET_DIR}/.claude/guardrails.md"
  sed "s|__TRAINEE__|${TRAINEE_NAME}|g" "${SCAFFOLD_DIR}/trainee/output-format.template" > "${TARGET_DIR}/.claude/output-format.md"
else
cat > "${TARGET_DIR}/.claude/instructions.md" <<INSTRUCTIONS
Role: ${ROLE_TITLE} — FILL IN one-line summary of primary responsibility
Team: ${TEAM}; operator: Martin (system-owner)
Task source: .workspace/brief.md (materialised from Qdrant session_substrate/briefs at session start)
Cross-team work: raise draft in podzoneTeam/briefs/{recipient}/ — do not write to other agents' home repos
Results: write to results/session-{date}-{slug}-{sid}.md; hook pushes and raises PR
Memory: read memory/MEMORY.md; update memory/ when learning something durable
Headless (PROJ-039/T-041): if the brief needs operator direction you cannot resolve, raise it to your team lead with progress so far via the session response and exit — never block, never AskUserQuestion-and-wait (no operator is on the line)
INSTRUCTIONS

cat > "${TARGET_DIR}/.claude/guardrails.md" <<GUARDRAILS
Never commit .workspace/ — it is ephemeral (materialised session context + on-demand repo clones) and gitignored
Never open task repos directly — always use ${REPO_NAME}.code-workspace
Never AskUserQuestion-and-wait or block interactively when operator-blocked — raise to your team lead via the session response and exit (headless: no operator is on the line — PROJ-039/T-041)
FILL IN role-specific prohibition 2
Secrets via getSecret.sh only — never hardcode or log secret values
If a secret appears in context, stop and notify Martin immediately
GUARDRAILS

cp "${SCAFFOLD_DIR}/output-format.template" "${TARGET_DIR}/.claude/output-format.md"
fi

# --- memory/ (trainee: hooks-troubleshooting summary, R-8) ---
if [[ "$ROLE" == "trainee" ]]; then
  cp "${SCAFFOLD_DIR}/trainee/memory/hooks-troubleshooting.md" "${TARGET_DIR}/memory/hooks-troubleshooting.md"
fi

# --- Identity YAML stub ---

TASK_FILTER="$(role_task_filter "$ROLE")"

if [[ "$ROLE" == "trainee" ]]; then
  # R-8: a trainee-named identity (placeholder "trainee" in the template — personalise-
  # trainee.py rewrites it to the handle at setup), NOT a template-named martin-*-coder
  # file and NO FILL-IN placeholders. Working defaults, not markers.
  WORKSPACE_NAME="trainee"
  cat > "${TARGET_DIR}/workspaces/identity/${WORKSPACE_NAME}.identity.yaml" <<IDENTITY
operator: Trainee
operator_mode: interactive
agent: ${TRAINEE_NAME}
scope: training
home_repo: ${REPO_NAME}
role_class: agenticflows/roles/${ROLE}/
repos:
  - name: agent-tooling
    purpose: canonical sync source for sync-agent-tooling.sh — cloned to .workspace/ on demand; not required at runtime (self-contained)
  # curriculum task repo(s) are added on clone (see docs/workstation-setup.md)
task_filter: "${TASK_FILTER}"
workspace: ${REPO_NAME}
IDENTITY
else
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
fi

# --- Workspace file ---
# Trainee (R-8): NO template-named .code-workspace is emitted — a trainee "just clones
# and runs claude" (see README launch ritual); the baseline leaked a
# home-training-template.code-workspace. Other roles keep the committed workspace file.
if [[ "$ROLE" != "trainee" ]]; then
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
fi

# --- memory/MEMORY.md ---

if [[ "$ROLE" == "trainee" ]]; then
cat > "${TARGET_DIR}/memory/MEMORY.md" <<MEMINDEX
# Memory Index

- [hooks-troubleshooting](hooks-troubleshooting.md) — what the substrate hooks do + how to recover
MEMINDEX
else
cat > "${TARGET_DIR}/memory/MEMORY.md" <<MEMINDEX
# Memory Index
MEMINDEX
fi

# --- Telemetry backstop bootstrap (PROJ-039/T-032) ---
# Best-effort: init the agent-telemetry repo the SessionEnd finalise pushes to
# (R-015 keystone) so a freshly scaffolded home repo can push from its first session.
# Idempotent — no-op if already a repo. Never fails the scaffold (the hook also
# self-heals lazily). Skipped under --no-telemetry-bootstrap or when python can't
# import the lib.

if [[ "$ROLE" == "trainee" ]]; then
  # R-14: trainee repos do NOT push to the fleet agent-telemetry remote (won't scale to
  # every student workstation) — the finalise commits the session log into logs/ instead.
  # So there is no telemetry backstop to bootstrap.
  echo "==> Telemetry backstop: skipped (trainee role — session logs are committed to logs/, R-14)"
elif [[ "${NO_TELEMETRY_BOOTSTRAP:-0}" != "1" ]]; then
  TELEMETRY_REMOTE="https://github.com/PodZonePlatformEngineering/agent-telemetry.git"
  echo "==> Bootstrapping telemetry backstop (best-effort)..."
  AGENT_TOOLING_DIR="$AGENT_TOOLING_DIR" TELEMETRY_REMOTE="$TELEMETRY_REMOTE" python3 - <<'PYBOOT' 2>/dev/null || echo "    (telemetry bootstrap skipped — hook will self-heal on first session-end)"
import os, sys
sys.path.insert(0, os.environ["AGENT_TOOLING_DIR"])
from lib import telemetry_repo
res = telemetry_repo.ensure_repo(remote=os.environ["TELEMETRY_REMOTE"])
print(f"    telemetry repo: {res['repo_dir']} (initialised={res['initialised']}, origin set)")
PYBOOT
fi

# --- Summary ---

echo ""
echo "Scaffold complete: ${TARGET_DIR}"
echo ""
if [[ "$ROLE" == "trainee" ]]; then
  # Trainee generation is artifact-clean by construction (R-8) — no FILL-IN checklist.
  echo "Trainee template generated (curriculum-agnostic; placeholder name '${TRAINEE_NAME}')."
  echo "Per-trainee provisioning (setup-time, see docs/workstation-setup.md):"
  echo "  [ ] Use this template → create podzone-training-<handle> (trainee's own account, Private)"
  echo "  [ ] python3 tools/personalise-trainee.py --handle <handle>  (renames ${TRAINEE_NAME}/ + identity)"
  echo "  [ ] Set PODZONE_QDRANT_APIKEY in .claude/settings.local.json (workstation only)"
  echo "  [ ] Invite the training lead as a collaborator; author + --approve the trainee brief"
  echo "  [ ] python3 .claude/hooks/trainee-preflight.py  (expect all OK), then paste the Brief: first prompt"
else
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
fi
