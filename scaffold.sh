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
# (ingest user turns → Qdrant prompt_logs): a role nuance surfaced by C2b — it must
# be home-repo-resident, not workstation-global. PROJ-039/T-011 C2b.
# curriculum-developer / historian / strategist are the fissioned-team BUILD agents
# (hestia / clio / kronos) migrated under C2c (PROJ-039/T-037). They are producers
# that do not spawn subagents and carry no automatic transcript-ingest, so they take
# the universal substrate base — same shape as team-lead/trainer. (The historian's
# log/memory ingestion is explicit, agent-invoked toolchain work in its task repo,
# NOT a SessionEnd transcript-ingest hook like the archivist's — so no ingest hook.)
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
    # trainee (PROJ-011/T-030 v3): NO fleet substrate hook ships at all — the
    # trainee set routes exclusively to the training_* collections via the
    # committed training-config.yaml (R2-3), so no fleet-collection URL can be
    # produced by construction. first-prompt-brief.py is v2-retired for this
    # role: the operational brief id lives in the config file, not the first
    # prompt, and the personalised brief is the in-repo trainee-brief.md (R2-4).
    trainee)               echo "trainee-preflight.py trainee-session-branch.py trainee-read-guard.py trainee-materialise.py trainee-telemetry.py trainee-finalise.py" ;;
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

  # trainee (PROJ-011/T-030 v3): NO fleet substrate hook is wired — every wired
  # hook routes to the training_* collections via the committed
  # training-config.yaml (R2-3), or touches only the local clone. The env stays
  # deliberately SLIM: no PODZONE_TELEMETRY_REMOTE and no log pushes of any kind
  # (R2-5 — trainee observability is training_session_telemetry points only),
  # no PODZONETEAM_REPO (no apex clone in trainee context), no fleet Qdrant key
  # (the credential lives in training-config.yaml). Only TRAINEE_RUNTIME=1
  # remains, as the defence-in-depth selector for any legacy fleet code path.
  # SessionStart order matters: preflight (report) → finalise --guard (recover
  # a truncated close) → session branch → update-tooling (after the branch
  # switch so a self-update commit lands on the session branch) → operational-
  # brief materialise → telemetry baseline.
  if [[ "$role" == "trainee" ]]; then
    cat <<'TRAINEE_SETTINGS'
{
  "env": {
    "TRAINEE_RUNTIME": "1"
  },
  "hooks": {
    "SessionStart": [
      { "matcher": "startup|resume", "hooks": [ { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-preflight.py" }, { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-finalise.py --guard" }, { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-session-branch.py" }, { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/tools/update-tooling.py", "timeout": 300 }, { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-materialise.py" }, { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-telemetry.py" } ] }
    ],
    "UserPromptSubmit": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-telemetry.py" } ] }
    ],
    "PreToolUse": [
      { "matcher": "*", "hooks": [ { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-read-guard.py" } ] }
    ],
    "PostCompact": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-telemetry.py" } ] }
    ],
    "Stop": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-telemetry.py" } ] }
    ],
    "SessionEnd": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-finalise.py", "timeout": 600 } ] }
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

  # (Every role below is non-trainee — the trainee variant returned above with its
  # own complete settings document. The v2 trainee_env / first-prompt-brief splices
  # are gone with the v3 config-driven trainee set, PROJ-011/T-030.)
  local trainee_env=""

  # session-materialise on SessionStart is COMMITTED-resident wiring (PROJ-039/T-052)
  # — settings.local.json is no longer load-bearing for it. A brief-first launch sets
  # BRIEF_ID (inline or via settings.local env); materialise reads it and stands up the
  # session point. BRIEF_ID unset → materialise runs its legacy session-point-keyed path
  # (a no-op when there is no matching point), so this is safe for every launch mode.
  local session_start_materialise=', { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/session-materialise.py" }'
  local session_start_extra=""
  local ups_extra=""

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
#     authoring still rides the SessionEnd finalise hook (T-035); the `session-end`
#     SKILL in the subset is only the T-100 manual-finalise wrapper (sidebar /exit
#     equivalent) — it triggers that same hook, never replaces it.
# (The apex repo is the same team-lead layout — apex-ness lives in the identity YAML
#  [role_class team-lead-apex] and the extra podzoneTeam-resident management skills,
#  not in a different scaffold; T-066/T-100.)
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

  # --- v3 committed config + offline-first briefing set (PROJ-011/T-030) ---
  # training-config.yaml is the SINGLE configuration surface (R2-2): the granular
  # Database API Key ({{PLACEHOLDER}} until take-on Phase A), the operational
  # brief id, and the training-collection names. The cluster URL is baked at
  # scaffold from the qdrant_http single source of truth (not a secret).
  QDRANT_URL_DEFAULT="$(python3 -c "
import sys; sys.path.insert(0, '${AGENT_TOOLING_DIR}')
from lib.qdrant_http import CLOUD_QDRANT_URL; print(CLOUD_QDRANT_URL)")"
  sed "s|__QDRANT_URL__|${QDRANT_URL_DEFAULT}|g" \
    "${SCAFFOLD_DIR}/trainee/training-config.template" > "${TARGET_DIR}/training-config.yaml"

  # Offline-first briefing (R2-4, from the T-032 seed): CLAUDE.md is a pointer to
  # AGENTS.md (the merged tutor briefing + repo manual, rendered below with the
  # other role files); trainee-brief.md is the personalised in-repo brief skeleton
  # ({{PLACEHOLDERS}} filled by the trainer at take-on). The repo must operate —
  # agent briefed, trainee working — with hooks inoperative or Qdrant unavailable.
  cp "${SCAFFOLD_DIR}/trainee/CLAUDE.template" "${TARGET_DIR}/CLAUDE.md"
  sed "s|__TRAINEE__|${TRAINEE_NAME}|g" \
    "${SCAFFOLD_DIR}/trainee/trainee-brief.template" > "${TARGET_DIR}/trainee-brief.md"
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

# --- Operating manual (team-lead variant only, PROJ-039/T-081) ---
# The compact team-lead operating manual is a resident, sync-managed artefact:
# rendered from scaffold/team-lead/OPERATING-MANUAL.template to the repo root
# (same pattern as the trainee AGENTS.template), substitution-variable keyed,
# and carried in the sync manifest so every team lead receives updates via
# TOOLING_UPDATE — never copied ad hoc. Other roles emit nothing here.

TEAM_LEAD_MANUAL_TEMPLATE="${SCAFFOLD_DIR}/team-lead/OPERATING-MANUAL.template"
if [[ "$ROLE" == "team-lead" ]]; then
  echo "==> Rendering team-lead operating manual (OPERATING-MANUAL.md)..."
  if [[ ! -f "$TEAM_LEAD_MANUAL_TEMPLATE" ]]; then
    echo "Error: team-lead manual template not found: ${TEAM_LEAD_MANUAL_TEMPLATE}"
    exit 1
  fi
  sed -e "s|__AGENT__|${AGENT_CAP}|g" \
      -e "s|__AGENT_LC__|${AGENT}|g" \
      -e "s|__TEAM__|${TEAM}|g" \
      -e "s|__TEAM_REPO__|${TEAM}Team|g" \
      -e "s|__REPO_NAME__|${REPO_NAME}|g" \
    "$TEAM_LEAD_MANUAL_TEMPLATE" > "${TARGET_DIR}/OPERATING-MANUAL.md"
  echo "    rendered: OPERATING-MANUAL.md"
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
| \`/create-task\` | Guided task-board entry creation (identity-routed) |
| \`/usage-report\` | 7-day usage digest into the resolved team repo |
| \`/session-end\` | Manual finalise — the /exit equivalent for sidebar sessions (T-100 wrapper over the finalise hook) |

Result authoring is NOT a skill — the SessionEnd finalise hook owns the session
result (PROJ-039/T-035), exactly as for a build agent. Session ceremony stays
hook-driven; \`/session-start\` is deliberately not present (orientation = the
OPERATING-MANUAL §3 procedure). \`/session-end\` is only the manual-finalise
wrapper for sessions without an /exit built-in (T-100) — it fires the same hook.

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
# Trainee (R-7): trainee operating manual (structure map + launch/review rituals), no
# provenance — trainee-owned, byte-untouched by sync.
# Non-trainee (PROJ-039/T-095): operator-facing orientation — resident, sync-managed,
# rendered from scaffold/home-repo-README.template (same pattern as OPERATING-MANUAL.md),
# carried in the sync manifest so every non-trainee agent receives updates via
# TOOLING_UPDATE. Audience is the OPERATOR, not the agent — AGENTS.md (and, for
# team-lead, OPERATING-MANUAL.md) remain the agent-facing artefacts.

if [[ "$ROLE" == "trainee" ]]; then
  sed -e "s|__TRAINEE__|${TRAINEE_NAME}|g" -e "s|__REPO_NAME__|${REPO_NAME}|g" \
    "${SCAFFOLD_DIR}/trainee/README.template" > "${TARGET_DIR}/README.md"
else
  HOME_README_TEMPLATE="${SCAFFOLD_DIR}/home-repo-README.template"
  if [[ ! -f "$HOME_README_TEMPLATE" ]]; then
    echo "Error: home-repo README template not found: ${HOME_README_TEMPLATE}"
    exit 1
  fi
  sed -e "s|__AGENT__|${AGENT_CAP}|g" \
      -e "s|__AGENT_LC__|${AGENT}|g" \
      -e "s|__TEAM__|${TEAM}|g" \
      -e "s|__TEAM_REPO__|${TEAM}Team|g" \
      -e "s|__REPO_NAME__|${REPO_NAME}|g" \
      -e "s|__ROLE_TITLE__|${ROLE_TITLE}|g" \
    "$HOME_README_TEMPLATE" > "${TARGET_DIR}/README.md"
  echo "    rendered: README.md"
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
agent: ${AGENT_CAP}
scope: ${TEAM}
home_repo: ${REPO_NAME}
role_class: agenticflows/roles/${ROLE}/
repos:
  - name: agent-tooling
    purpose: canonical sync source for sync-agent-tooling.sh — cloned to .workspace/ on demand; not required at runtime (self-contained)
  # task repos are added on clone per brief (see docs/workstation-setup.md)
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
  echo "Per-trainee provisioning (take-on, see docs/workstation-setup.md):"
  echo "  [ ] Use this template → create podzone-training-<handle> (trainee's own account, Private)"
  echo "  [ ] python3 tools/personalise-trainee.py --handle <handle>  (renames ${TRAINEE_NAME}/ + identity + config handle)"
  echo "  [ ] Fill qdrant_api_key in training-config.yaml (console-minted granular Database API Key, Phase A)"
  echo "  [ ] Trainer fills the {{PLACEHOLDERS}} in trainee-brief.md + AGENTS.md (curriculum personalisation)"
  echo "  [ ] Invite the training lead as a collaborator"
  echo "  [ ] python3 .claude/hooks/trainee-preflight.py  (expect all OK) — the trainee then just greets Alex"
else
  echo "FILL IN checklist — complete before opening the workspace:"
  echo "  [ ] Review workspaces/identity/${WORKSPACE_NAME}.identity.yaml"
  echo "        — complete at scaffold (T-099: agent/role_class/scope are real values,"
  echo "         identity resolution reads this file); adjust scope / add task repos if needed"
  echo "  [ ] Edit .claude/instructions.md — fill in role behaviour rules"
  echo "  [ ] Edit .claude/guardrails.md   — fill in role-specific prohibitions"
  echo "  [ ] Create repo '${REPO_NAME}' in PodZonePlatformEngineering and push"
  echo "        (hooks + primitives/ + lib/ are resident under .claude/ — the repo"
  echo "         runs self-contained; no agent-tooling clone is required to fire hooks)"
  echo "  [ ] Set PODZONE_QDRANT_APIKEY in .claude/settings.local.json (workstation only)"
  echo "  [ ] Open ${REPO_NAME}.code-workspace and confirm session-start.sh fires"
fi
