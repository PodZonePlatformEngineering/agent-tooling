#!/usr/bin/env bash
# sync-agent-tooling.sh — re-apply hook + resident-dependency updates to an
# existing home repo, keeping it byte-identical to the canonical agent-tooling
# source (template v2.1: hooks + primitives/ + lib/ resident under .claude/).
# Reference: planning/projects/PROJ-032-agent-home-repos/home-repo-template.md §13
# Tasks: PROJ-033/T-007 (CC-269); PROJ-039/T-011 C2-v2.1 (self-containment)
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

VALID_ROLES="team-lead coder archivist trainer cluster-operator curriculum-developer historian strategist trainee"

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

# --- Role hook set (template v2.1, PROJ-039/T-011 C2-v2.1) ---

# The real PROJ-039 substrate working set — the hooks proven on the Hephaestus
# canary, NOT the v2.0 stubs (startup.sh / session-end.sh / task-event.sh) that
# never existed as a runnable set. The universal substrate (session-start +
# user-prompt-submit + post-compact + stop, with
# append-session-stop.py as stop.sh's per-Stop tasking helper) emits session
# telemetry + tasking for every role. Roles that spawn subagents
# (coder / cluster-operator) additionally carry the SubagentStop chain.
# session-end-finalise.py (SessionEnd) anchors the self-contained session-end
# lifecycle — telemetry push → rollup → CST prune → session-finalise — and is
# universal (every role finalises). PROJ-039/T-011 C2-v2.1c.
# archivist additionally carries the resident ingest-transcript SessionEnd hook
# (embed user turns → Qdrant prompt_logs) — home-repo-resident, not workstation
# -global (PROJ-039/T-011 C2b). Kept byte-identical with scaffold.sh's role_hooks
# + role_settings_json.
# curriculum-developer / historian / strategist are the C2c fissioned-team build
# agents (hestia / clio / kronos, PROJ-039/T-037): producers, no subagent spawn,
# no transcript-ingest — universal substrate base, same shape as team-lead/trainer.
# session-materialise.py is in the universal base (PROJ-039/T-052): it is committed
# resident like every other hook — NEVER hand-copied + settings.local.json-wired.
# The hand-copy gotcha bit Thoth's first serial launch (T-022): the copy was omitted,
# the SessionStart wiring pointed at a missing file and failed silently → no brief-first
# session point, session_ids[] never appended, finalise ran "not brief-first". Resident
# + committed-settings.json-wired kills that class.
# Pre/PostToolUse CST writers retired at T-073 (CC-383): 91% of the collection
# was per-tool-call noise — the writers left the set, RETIRED_HOOKS prunes the
# resident copies, and the settings unwire step below strips their wiring.
SUBSTRATE_BASE="session-start.sh session-materialise.py user-prompt-submit.sh post-compact.sh stop.sh stop-telemetry.py append-session-stop.py session-end-finalise.py"
RETIRED_HOOKS="pre-tool-use.sh post-tool-use.sh"
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

# Resident dependencies mirrored into the home repo's .claude/ so it is
# self-contained (ADR-008 D2): no AGENT_TOOLING_DIR, no agent-tooling on the
# discovery path. The sync diff over these IS the byte-identity regression test.
# lib/ is NOT a wholesale dep dir — it is synced module-by-module from
# home-runtime-lib.manifest (the runtime closure) and any out-of-closure module
# left over from a pre-C2-v2.1b full-lib copy is pruned. PROJ-039/T-011 C2-v2.1b.
# Kept byte-identical with scaffold.sh.
DEP_DIRS="primitives"

# Role-neutral resident tools (PROJ-039/T-056): single files under tools/ that
# ship to .claude/tools/ for EVERY role. Not a whole-dir DEP_DIRS copy — tools/
# upstream also carries workstation/Hermes-only scripts (create-brief.py etc.)
# that must never land in a home repo. Kept byte-identical with scaffold.sh.
TOOLS_FILES="update-tooling.py wire-update-tooling.py"
TOOLS_SRC="${AGENT_TOOLING_DIR}/tools"
TOOLS_DST="${HOME_REPO}/.claude/tools"

# .gitignore (PROJ-039/T-062, CC-372): a synced, manifest-tracked, ROOT-level file
# (not under .claude/, unlike everything else above) — the operator-raised T-048
# gap. The template dropped `*.log` so session logs (logs/libraries-{sid8}.log,
# logs/primitives-{sid8}.log, logs/finalise-state.log) commit and ride the
# session-result PR, but sync never touched .gitignore, so every existing fleet
# repo kept re-ignoring them. One template for every role — audited at T-062,
# no role (including trainee) carries a functionally distinct .gitignore; only
# .env + .claude/settings.local.json need to stay ignored everywhere, and both
# do in the template.
GITIGNORE_SRC="${AGENT_TOOLING_DIR}/scaffold/gitignore.template"
GITIGNORE_DST="${HOME_REPO}/.gitignore"

LIB_MANIFEST="${HOOKS_SRC}/home-runtime-lib.manifest"
SKILLS_SRC="${AGENT_TOOLING_DIR}/skills"
# Team-lead variant only: the coordination skill subset delivered under
# .claude/skills/, byte-identical to canonical, subset-exact. Build agents stay
# skill-free. PROJ-039/T-038.
TEAM_LEAD_SKILLS_MANIFEST="${AGENT_TOOLING_DIR}/scaffold/team-lead-skills.manifest"

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

# --- Retired hooks prune + settings unwire (PROJ-039/T-073, CC-383) ---
# The Pre/PostToolUse CST writers left the substrate set; existing fleet repos
# carry resident copies AND committed settings.json wiring, so a plain list-drop
# would leave every repo firing hooks at deleted scripts. Prune the files and
# strip ONLY the entries whose command references a retired script (other
# Pre/PostToolUse hooks — e.g. the trainee read guard — stay wired).

echo ""
echo "==> Pruning retired hooks (${RETIRED_HOOKS})"

for hook in $RETIRED_HOOKS; do
  dst="${HOOKS_DST}/${hook}"
  if [[ -f "$dst" ]]; then
    rm -f "$dst"
    echo "  Removed: ${hook} — retired (T-073)"
    ((UPDATED++))
  else
    echo "  OK    ${hook} — already absent"
    ((UNCHANGED++))
  fi
done

UNWIRE_SETTINGS="${HOME_REPO}/.claude/settings.json"
if [[ -f "$UNWIRE_SETTINGS" ]]; then
  python3 - "$UNWIRE_SETTINGS" <<'UNWIRE_PY' | sed 's/^/  /'
import json, sys

RETIRED = ("pre-tool-use.sh", "post-tool-use.sh")
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    settings = json.load(fh)

changed = False
hooks = settings.get("hooks") or {}
for event in list(hooks):
    groups = hooks[event]
    if not isinstance(groups, list):
        continue
    for group in groups:
        cmds = group.get("hooks")
        if not isinstance(cmds, list):
            continue
        kept = [c for c in cmds
                if not any(r in (c.get("command") or "") for r in RETIRED)]
        if len(kept) != len(cmds):
            group["hooks"] = kept
            changed = True
    pruned = [g for g in groups if g.get("hooks")]
    if pruned != groups:
        hooks[event] = pruned
        changed = True
    if not hooks[event]:
        del hooks[event]
        changed = True

if changed:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(settings, indent=2) + "\n")
    print("Unwired retired hook entries from settings.json")
else:
    print("OK    settings.json — no retired hook wiring")
UNWIRE_PY
fi

# --- Resident dependency dirs (primitives/ + lib/) ---

echo ""
echo "==> Syncing resident dependencies (${DEP_DIRS}) → ${HOME_REPO}/.claude/"

for dep in $DEP_DIRS; do
  src="${AGENT_TOOLING_DIR}/${dep}"
  dst="${HOME_REPO}/.claude/${dep}"

  if [[ ! -d "$src" ]]; then
    echo "  SKIP  ${dep}/ — not found in agent-tooling (${src})"
    ((SKIPPED++))
    continue
  fi

  if [[ -d "$dst" ]] && diff -rq -x '__pycache__' -x '*.pyc' "$src" "$dst" > /dev/null 2>&1; then
    echo "  OK    ${dep}/ — unchanged"
    ((UNCHANGED++))
    continue
  fi

  if [[ -d "$dst" ]]; then
    echo "  DIFF  ${dep}/:"
    diff -rq -x '__pycache__' -x '*.pyc' "$dst" "$src" || true
    echo ""
  else
    echo "  NEW   ${dep}/ — not present in home repo"
  fi

  if [[ $YES -eq 0 ]]; then
    printf "  Overwrite %s/? [y/N] " "$dep"
    read -r answer </dev/tty
    if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
      echo "  Skipped."
      ((SKIPPED++))
      continue
    fi
  fi

  rm -rf "$dst"
  cp -R "$src" "$dst"
  find "$dst" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
  echo "  Updated: ${dep}/"
  ((UPDATED++))
done

# --- Role-neutral resident tools (TOOLS_FILES) ---

echo ""
echo "==> Syncing resident tools (${TOOLS_FILES}) → ${TOOLS_DST}"
mkdir -p "$TOOLS_DST"

for tool in $TOOLS_FILES; do
  src="${TOOLS_SRC}/${tool}"
  dst="${TOOLS_DST}/${tool}"

  if [[ ! -f "$src" ]]; then
    echo "  SKIP  tools/${tool} — not found in agent-tooling (${src})"
    ((SKIPPED++))
    continue
  fi

  if [[ -f "$dst" ]] && diff -q "$src" "$dst" > /dev/null 2>&1; then
    echo "  OK    tools/${tool} — unchanged"
    ((UNCHANGED++))
    continue
  fi

  if [[ -f "$dst" ]]; then
    echo "  DIFF  tools/${tool}:"
    diff -u "$dst" "$src" || true
    echo ""
  else
    echo "  NEW   tools/${tool} — not present in home repo"
  fi

  if [[ $YES -eq 0 ]]; then
    printf "  Overwrite %s? [y/N] " "tools/${tool}"
    read -r answer </dev/tty
    if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
      echo "  Skipped."
      ((SKIPPED++))
      continue
    fi
  fi

  cp "$src" "$dst"
  chmod +x "$dst"
  echo "  Updated: tools/${tool}"
  ((UPDATED++))
done

# --- Root-level .gitignore (PROJ-039/T-062) ---

echo ""
echo "==> Syncing .gitignore → ${GITIGNORE_DST}"

if [[ ! -f "$GITIGNORE_SRC" ]]; then
  echo "  SKIP  .gitignore — not found in agent-tooling (${GITIGNORE_SRC})"
  ((SKIPPED++))
elif [[ -f "$GITIGNORE_DST" ]] && diff -q "$GITIGNORE_SRC" "$GITIGNORE_DST" > /dev/null 2>&1; then
  echo "  OK    .gitignore — unchanged"
  ((UNCHANGED++))
else
  if [[ -f "$GITIGNORE_DST" ]]; then
    echo "  DIFF  .gitignore:"
    diff -u "$GITIGNORE_DST" "$GITIGNORE_SRC" || true
    echo ""
  else
    echo "  NEW   .gitignore — not present in home repo"
  fi

  if [[ $YES -eq 0 ]]; then
    printf "  Overwrite %s? [y/N] " ".gitignore"
    read -r answer </dev/tty
    if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
      echo "  Skipped."
      ((SKIPPED++))
    else
      cp "$GITIGNORE_SRC" "$GITIGNORE_DST"
      echo "  Updated: .gitignore"
      ((UPDATED++))
    fi
  else
    cp "$GITIGNORE_SRC" "$GITIGNORE_DST"
    echo "  Updated: .gitignore"
    ((UPDATED++))
  fi
fi

# --- SessionStart updater wiring in settings.json (PROJ-039/T-069, T-065 F1) ---
# The committed .claude/settings.json can never join the byte-copy set (its env
# block is per-repo), so the updater WIRING joins the sync set structurally
# instead: wire-update-tooling.py patches the hooks block in place — updater as
# the FIRST SessionStart command (trainee: last, after trainee-session-branch.py)
# — leaving env and every other hook untouched. This closes the T-065 F1
# chicken-and-egg: the release that ships the updater now also wires it.

echo ""
echo "==> Wiring update-tooling.py into SessionStart (.claude/settings.json)"

SETTINGS_DST="${HOME_REPO}/.claude/settings.json"
if [[ ! -f "$SETTINGS_DST" ]]; then
  echo "  ERROR: ${SETTINGS_DST} not found — not a v2 home repo? Run scaffold.sh."
  exit 1
fi
python3 "${TOOLS_SRC}/wire-update-tooling.py" --settings "$SETTINGS_DST" --role "$ROLE" \
  | sed 's/^/  /'

# --- Runtime lib closure (home-runtime-lib.manifest) ---
# lib/ is synced module-by-module from the manifest (the runtime closure), NOT as
# the whole agent-tooling lib/. Out-of-closure modules left over from a pre-C2-v2.1b
# full-lib copy (decay/, one-shots, harnesses, reporting) are PRUNED so the home
# repo converges on the slim closure. PROJ-039/T-011 C2-v2.1b.

echo ""
echo "==> Syncing runtime lib closure (home-runtime-lib.manifest) → ${HOME_REPO}/.claude/lib/"

if [[ ! -f "$LIB_MANIFEST" ]]; then
  echo "  SKIP  lib/ — manifest not found (${LIB_MANIFEST})"
  ((SKIPPED++))
else
  LIB_DST="${HOME_REPO}/.claude/lib"
  mkdir -p "$LIB_DST"
  MANIFEST_SET=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    entry="${line%%#*}"; entry="${entry//[[:space:]]/}"
    [[ -z "$entry" ]] && continue
    MANIFEST_SET="${MANIFEST_SET} ${entry}"
    src="${AGENT_TOOLING_DIR}/lib/${entry}"
    dst="${LIB_DST}/${entry}"

    if [[ ! -f "$src" ]]; then
      echo "  SKIP  lib/${entry} — not found in agent-tooling (${src})"
      ((SKIPPED++))
      continue
    fi
    if [[ -f "$dst" ]] && diff -q "$src" "$dst" > /dev/null 2>&1; then
      echo "  OK    lib/${entry} — unchanged"
      ((UNCHANGED++))
      continue
    fi
    if [[ -f "$dst" ]]; then
      echo "  DIFF  lib/${entry}:"
      diff -u "$dst" "$src" || true
      echo ""
    else
      echo "  NEW   lib/${entry} — not present in home repo"
    fi
    if [[ $YES -eq 0 ]]; then
      printf "  Overwrite lib/%s? [y/N] " "$entry"
      read -r answer </dev/tty
      if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
        echo "  Skipped."
        ((SKIPPED++))
        continue
      fi
    fi
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    echo "  Updated: lib/${entry}"
    ((UPDATED++))
  done < "$LIB_MANIFEST"

  # Prune any tracked lib module NOT in the manifest (re-bloat / legacy full-lib copy).
  while IFS= read -r f; do
    rel="${f#"${LIB_DST}/"}"
    case " ${MANIFEST_SET} " in
      *" ${rel} "*) : ;;  # in closure — keep
      *)
        if [[ $YES -eq 0 ]]; then
          printf "  Prune out-of-closure lib/%s? [y/N] " "$rel"
          read -r answer </dev/tty
          [[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "  Kept."; continue; }
        fi
        rm -f "$f"
        echo "  Pruned: lib/${rel} (not in home-runtime-lib.manifest)"
        ((UPDATED++))
        ;;
    esac
  done < <(find "$LIB_DST" -type f ! -path '*/__pycache__/*')
  # Drop now-empty subdirectories left by pruning (e.g. decay/).
  find "$LIB_DST" -mindepth 1 -type d -empty -delete 2>/dev/null || true
fi

# --- Coordination skills (team-lead variant only) ---
# A team-lead home repo carries the coordination skill subset under .claude/skills/,
# byte-identical to the canonical agent-tooling/skills/ source and EXACTLY the subset
# (any out-of-subset skill is pruned — session ceremony stays hook-driven). Build-agent
# home repos stay skill-free: if a .claude/skills/ exists for a non-team-lead role it is
# flagged (and pruned on confirm). PROJ-039/T-038.

SKILLS_DST="${HOME_REPO}/.claude/skills"
SUBSET_SET=""
echo ""
if [[ "$ROLE" == "team-lead" ]]; then
  echo "==> Syncing coordination skills (team-lead) → ${SKILLS_DST}"
  if [[ ! -f "$TEAM_LEAD_SKILLS_MANIFEST" ]]; then
    echo "  SKIP  skills/ — manifest not found (${TEAM_LEAD_SKILLS_MANIFEST})"
    ((SKIPPED++))
  else
    mkdir -p "$SKILLS_DST"
    while IFS= read -r line || [[ -n "$line" ]]; do
      entry="${line%%#*}"; entry="${entry//[[:space:]]/}"
      [[ -z "$entry" ]] && continue
      SUBSET_SET="${SUBSET_SET} ${entry}"
      src="${SKILLS_SRC}/${entry}"
      dst="${SKILLS_DST}/${entry}"
      if [[ ! -d "$src" ]]; then
        echo "  SKIP  skills/${entry} — not found in agent-tooling (${src})"
        ((SKIPPED++)); continue
      fi
      if [[ -d "$dst" ]] && diff -rq -x '__pycache__' -x '*.pyc' "$src" "$dst" > /dev/null 2>&1; then
        echo "  OK    skills/${entry} — unchanged"
        ((UNCHANGED++)); continue
      fi
      if [[ -d "$dst" ]]; then
        echo "  DIFF  skills/${entry}:"
        diff -rq -x '__pycache__' -x '*.pyc' "$dst" "$src" || true
      else
        echo "  NEW   skills/${entry} — not present in home repo"
      fi
      if [[ $YES -eq 0 ]]; then
        printf "  Overwrite skills/%s? [y/N] " "$entry"
        read -r answer </dev/tty
        if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
          echo "  Skipped."; ((SKIPPED++)); continue
        fi
      fi
      rm -rf "$dst"
      cp -R "$src" "$dst"
      find "$dst" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
      echo "  Updated: skills/${entry}"
      ((UPDATED++))
    done < "$TEAM_LEAD_SKILLS_MANIFEST"

    # Prune any skill NOT in the subset (session ceremony / drift).
    if [[ -d "$SKILLS_DST" ]]; then
      while IFS= read -r d; do
        name="$(basename "$d")"
        case " ${SUBSET_SET} " in
          *" ${name} "*) : ;;  # in subset — keep
          *)
            if [[ $YES -eq 0 ]]; then
              printf "  Prune out-of-subset skills/%s? [y/N] " "$name"
              read -r answer </dev/tty
              [[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "  Kept."; continue; }
            fi
            rm -rf "$d"
            echo "  Pruned: skills/${name} (not in team-lead-skills.manifest)"
            ((UPDATED++))
            ;;
        esac
      done < <(find "$SKILLS_DST" -mindepth 1 -maxdepth 1 -type d)
    fi
  fi
else
  # Build-agent home repos are hooks-only. A stray skills/ is a regression.
  if [[ -d "$SKILLS_DST" ]]; then
    echo "==> WARNING: role '${ROLE}' is hooks-only but .claude/skills/ exists."
    if [[ $YES -eq 0 ]]; then
      printf "  Remove .claude/skills/ (build agents carry no skills)? [y/N] "
      read -r answer </dev/tty
      if [[ "$answer" == "y" || "$answer" == "Y" ]]; then
        rm -rf "$SKILLS_DST"; echo "  Removed: .claude/skills/"; ((UPDATED++))
      else
        echo "  Kept — note: this will FAIL the byte-identity invariant below."
      fi
    else
      rm -rf "$SKILLS_DST"; echo "  Removed: .claude/skills/"; ((UPDATED++))
    fi
  else
    echo "==> Skills: none (role '${ROLE}' is hooks-only — correct)"
  fi
fi

echo ""
echo "Sync complete: ${UPDATED} updated, ${UNCHANGED} unchanged, ${SKIPPED} skipped."

# --- Byte-identity invariant (the regression test) ---
# Updates flow only from the canonical agent-tooling source; after a clean sync
# the home repo's hook set + resident deps MUST be byte-identical to agent-tooling
# (ADR-008 D2 self-containment). Any residual diff is a regression — fail loudly.

DRIFT=0
for hook in $(role_hooks "$ROLE"); do
  [[ -f "${HOOKS_SRC}/${hook}" ]] || continue
  diff -q "${HOOKS_SRC}/${hook}" "${HOOKS_DST}/${hook}" > /dev/null 2>&1 \
    || { echo "  DRIFT: hooks/${hook}"; DRIFT=1; }
done
for dep in $DEP_DIRS; do
  diff -rq -x '__pycache__' -x '*.pyc' "${AGENT_TOOLING_DIR}/${dep}" "${HOME_REPO}/.claude/${dep}" > /dev/null 2>&1 \
    || { echo "  DRIFT: ${dep}/"; DRIFT=1; }
done
for tool in $TOOLS_FILES; do
  [[ -f "${TOOLS_SRC}/${tool}" ]] || continue
  diff -q "${TOOLS_SRC}/${tool}" "${TOOLS_DST}/${tool}" > /dev/null 2>&1 \
    || { echo "  DRIFT: tools/${tool}"; DRIFT=1; }
done
if [[ -f "$GITIGNORE_SRC" ]]; then
  diff -q "$GITIGNORE_SRC" "$GITIGNORE_DST" > /dev/null 2>&1 \
    || { echo "  DRIFT: .gitignore"; DRIFT=1; }
fi
# Updater wiring invariant (PROJ-039/T-069): structural, not byte — the updater
# must sit at its canonical SessionStart position in the committed settings.json.
python3 "${TOOLS_SRC}/wire-update-tooling.py" --settings "${HOME_REPO}/.claude/settings.json" --role "$ROLE" --check > /dev/null 2>&1 \
  || { echo "  DRIFT: settings.json (update-tooling.py not wired at canonical SessionStart position)"; DRIFT=1; }
# lib/ invariant is manifest-scoped: every manifest module byte-identical to source,
# AND no out-of-closure module present (the slim-closure guarantee). PROJ-039 C2-v2.1b.
if [[ -f "$LIB_MANIFEST" ]]; then
  LIB_DST="${HOME_REPO}/.claude/lib"
  MANIFEST_SET=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    entry="${line%%#*}"; entry="${entry//[[:space:]]/}"
    [[ -z "$entry" ]] && continue
    MANIFEST_SET="${MANIFEST_SET} ${entry}"
    diff -q "${AGENT_TOOLING_DIR}/lib/${entry}" "${LIB_DST}/${entry}" > /dev/null 2>&1 \
      || { echo "  DRIFT: lib/${entry}"; DRIFT=1; }
  done < "$LIB_MANIFEST"
  while IFS= read -r f; do
    rel="${f#"${LIB_DST}/"}"
    case " ${MANIFEST_SET} " in
      *" ${rel} "*) : ;;
      *) echo "  DRIFT: lib/${rel} (out of closure — not in manifest)"; DRIFT=1 ;;
    esac
  done < <(find "$LIB_DST" -type f ! -path '*/__pycache__/*' 2>/dev/null)
fi

# Skills invariant (PROJ-039/T-038): a team-lead home repo's .claude/skills/ MUST be
# exactly the coordination subset, each byte-identical to source; a build-agent home
# repo MUST have NO .claude/skills/ at all.
if [[ "$ROLE" == "team-lead" && -f "$TEAM_LEAD_SKILLS_MANIFEST" ]]; then
  for skill in $SUBSET_SET; do
    diff -rq -x '__pycache__' -x '*.pyc' "${SKILLS_SRC}/${skill}" "${SKILLS_DST}/${skill}" > /dev/null 2>&1 \
      || { echo "  DRIFT: skills/${skill} (not byte-identical to source / missing)"; DRIFT=1; }
  done
  while IFS= read -r d; do
    name="$(basename "$d")"
    case " ${SUBSET_SET} " in
      *" ${name} "*) : ;;
      *) echo "  DRIFT: skills/${name} (out of subset — not in team-lead-skills.manifest)"; DRIFT=1 ;;
    esac
  done < <(find "$SKILLS_DST" -mindepth 1 -maxdepth 1 -type d 2>/dev/null)
elif [[ "$ROLE" != "team-lead" && -d "$SKILLS_DST" ]]; then
  echo "  DRIFT: .claude/skills/ present for hooks-only role '${ROLE}' (build agents carry no skills)"
  DRIFT=1
fi

if [[ $DRIFT -eq 0 ]]; then
  echo "Byte-identity invariant: PASS — hooks + ${DEP_DIRS} + tools + .gitignore + lib closure + skills match agent-tooling."
else
  echo "Byte-identity invariant: FAIL — drift listed above (re-run without --skip, or investigate)."
  exit 1
fi

# --- Shipped tooling manifest v2 (PROJ-039/T-055) ---
# Written only after a byte-identity PASS: {version, source_commit, role, synced_at,
# files: {path: sha256}} at .claude/tooling-manifest.json. ``path`` is relative to
# .claude/ and the hash is of the shipped (post-sync) file — since sync just proved
# byte-identity, this is equivalently a hash of the agent-tooling source. Turns the
# existing direct-diff byte-identity check from "identical to whatever canonical
# main is today" into "identical to a NAMED release": `version` + `source_commit`
# make drift a report, not hand-tracked lore.
VERSION_FILE="${AGENT_TOOLING_DIR}/VERSION"
TOOLING_VERSION="unknown"
if [[ -f "$VERSION_FILE" ]]; then
  TOOLING_VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
fi
SOURCE_COMMIT="$(git -C "$AGENT_TOOLING_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"

echo ""
echo "==> Writing shipped manifest (.claude/tooling-manifest.json)"
ROLE="$ROLE" TOOLING_VERSION="$TOOLING_VERSION" SOURCE_COMMIT="$SOURCE_COMMIT" \
python3 - "${HOME_REPO}/.claude" "${HOME_REPO}" <<'PYEOF'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

claude_dir = Path(sys.argv[1])
home_repo_dir = Path(sys.argv[2])
files = {}
for sub in ("hooks", "primitives", "lib", "skills", "tools"):
    d = claude_dir / sub
    if not d.is_dir():
        continue
    for f in sorted(d.rglob("*")):
        if not f.is_file():
            continue
        if "__pycache__" in f.parts or f.suffix == ".pyc":
            continue
        rel = f.relative_to(claude_dir).as_posix()
        files[rel] = hashlib.sha256(f.read_bytes()).hexdigest()

# Root-level files (PROJ-039/T-062): tracked separately from `files` (which is
# .claude/-relative) since these live at the home-repo root — .gitignore is the
# first and, for now, only entry.
root_files = {}
gitignore = home_repo_dir / ".gitignore"
if gitignore.is_file():
    root_files[".gitignore"] = hashlib.sha256(gitignore.read_bytes()).hexdigest()

manifest = {
    "version": os.environ["TOOLING_VERSION"],
    "source_commit": os.environ["SOURCE_COMMIT"],
    "role": os.environ["ROLE"],
    "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "files": files,
    "root_files": root_files,
}
# Idempotent re-sync (PROJ-039/T-069): an already-current repo must produce NO
# diff — otherwise every TOOLING_UPDATE launch commits a synced_at-only change.
# Keep the existing timestamp when nothing else moved.
existing_path = claude_dir / "tooling-manifest.json"
if existing_path.is_file():
    try:
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
        if {k: v for k, v in existing.items() if k != "synced_at"} == \
           {k: v for k, v in manifest.items() if k != "synced_at"} and existing.get("synced_at"):
            manifest["synced_at"] = existing["synced_at"]
    except ValueError:
        pass
claude_dir.mkdir(parents=True, exist_ok=True)
(claude_dir / "tooling-manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"  Wrote {claude_dir / 'tooling-manifest.json'} ({len(files)} files, "
      f"version={manifest['version']}, source_commit={manifest['source_commit'][:8]})")
PYEOF
