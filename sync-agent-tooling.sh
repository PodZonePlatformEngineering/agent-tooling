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
#   --prune-orphan-skills        Allow deletion of .claude/skills/<name> that has NO
#                                canonical source in agent-tooling/skills/. Off by
#                                default: such a skill is repo-local work and its
#                                deletion is unrecoverable (PROJ-039/T-122).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VALID_ROLES="team-lead coder archivist trainer cluster-operator curriculum-developer historian strategist trainee"

usage() {
  echo "Usage: bash sync-agent-tooling.sh --role {role-class} [--home-repo /path] [--agent-tooling /path] [--yes] [--prune-orphan-skills]"
  echo ""
  echo "Valid role classes: ${VALID_ROLES}"
  echo "Role-class variants: team-lead-apex (aliased to the team-lead file set)"
  exit 1
}

# --- Argument parsing ---

ROLE=""
HOME_REPO=""
AGENT_TOOLING_DIR="$SCRIPT_DIR"
YES=0
PRUNE_ORPHANS=0
# Out-of-subset skills found with no canonical source (T-122). Retained, reported.
ORPHAN_SKILLS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role)          ROLE="$2"; shift 2 ;;
    --home-repo)     HOME_REPO="$2"; shift 2 ;;
    --agent-tooling) AGENT_TOOLING_DIR="$2"; shift 2 ;;
    --yes)           YES=1; shift ;;
    --prune-orphan-skills) PRUNE_ORPHANS=1; shift ;;
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

# Role-class variant alias (PROJ-039/T-101, CC-420): `team-lead-apex` is the
# apex team-lead variant carried in the identity YAML (Hermes's live
# role_class), a variant, not a tenth role. Alias it to the team-lead file
# set here — mirroring lib/agent_identity.py's token match — so a
# TOOLING_UPDATE dispatch to the apex home repo needs no TOOLING_UPDATE_ROLE
# override; apex-ness stays expressed solely in the identity YAML (which
# sync never edits).
if [[ "$ROLE" == "team-lead-apex" ]]; then
  echo "==> Role-class variant 'team-lead-apex' → applying the 'team-lead' file set"
  ROLE="team-lead"
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
# (ingest user turns → Qdrant prompt_logs) — home-repo-resident, not workstation
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
# session-context.{py,sh} retired at T-099 (CC-409): home-repo identity now
# single-sources from workspaces/identity/*.identity.yaml (lib/agent_identity);
# the only resident copies are interim hand-patches (home-podzone-hermes
# 3335d6b) that must converge away.
# first-prompt-brief.py retired at T-030 (PROJ-011, CC-415): the v3 trainee
# runtime reads the operational brief id from the committed training-config.yaml
# on SessionStart (trainee-materialise.py) — no brief id in the first prompt.
# The hook only ever shipped to trainee repos, so a global prune is a no-op
# everywhere else. Keep RETIRED_HOOKS in lockstep with the
# RETIRED tuple in the settings-unwire python below.
SUBSTRATE_BASE="session-start.sh session-materialise.py user-prompt-submit.sh post-compact.sh stop.sh stop-telemetry.py append-session-stop.py session-end-finalise.py"
RETIRED_HOOKS="pre-tool-use.sh post-tool-use.sh session-context.py session-context.sh first-prompt-brief.py"
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
    # run-hook.sh is the python3 entry shim EVERY trainee hook command routes
    # through (PROJ-011/T-128): it must ship with the hooks it guards, or the
    # settings.json wiring points at a file that is not there. Trainee-only —
    # no other role has a workstation that might lack python3.
    trainee)               echo "run-hook.sh trainee-preflight.py trainee-session-branch.py trainee-read-guard.py trainee-materialise.py trainee-telemetry.py trainee-finalise.py" ;;
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
# that must never land in a home repo. session-stash-push.py/session-stash-pop.py
# (PROJ-039/T-255/T-256/T-257) are the session-stash primitives — post-compact.sh
# shells out to the push script (design doc §5.1), and an agent invokes either
# directly for the §5.3 explicit-handoff / manual-inspection path. Kept
# byte-identical with scaffold.sh.
TOOLS_FILES="update-tooling.py wire-update-tooling.py session-stash-push.py session-stash-pop.py"
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

# --- Atomic write helpers (PROJ-039/T-098, CC-408) ---
# SessionStart hooks run concurrently with a TOOLING_UPDATE-driven sync: a
# plain `cp src dst` rewrites dst's EXISTING inode, so a bash interpreter
# mid-read of that file sees the bytes shift under it (live incident,
# home-podzone-hermes 2026-07-11: the resident 1.7.0 session-start.sh was
# executing while the v1.12.0 sync copied over it — 7813→8993 bytes mid-read
# → `syntax error near unexpected token '('` at a statically-valid line).
# Every sync write therefore stages a temp file IN THE SAME DIRECTORY and
# atomically renames it into place: the rename swaps in a NEW inode, and any
# in-flight reader keeps its old inode to EOF, unharmed.

atomic_install() {  # atomic_install <src> <dst> [+x|<mode>]
  local src="$1" dst="$2" mode="${3:-}"
  local tmp="${dst}.sync-tmp.$$"
  cp -p "$src" "$tmp"
  if [[ -n "$mode" ]]; then
    chmod "$mode" "$tmp"
  fi
  mv -f "$tmp" "$dst"
}

# Directory variant: stage the whole copy next to the destination, then swap.
# The dir swap itself (rm -rf + mv) cannot be one atomic rename, but every
# file inside arrives as a new inode — an in-flight reader of any OLD file
# keeps its unlinked inode to EOF, the same guarantee as atomic_install.
atomic_install_dir() {  # atomic_install_dir <src> <dst>
  local src="$1" dst="$2"
  local tmp="${dst}.sync-tmp.$$"
  rm -rf "$tmp"
  cp -R "$src" "$tmp"
  find "$tmp" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
  rm -rf "$dst"
  mv "$tmp" "$dst"
}

# --- Sync ---

# Sweep stale atomic-write staging debris (T-098): a sync killed between the
# temp cp and the rename leaves *.sync-tmp* files behind that the next sync
# commit would otherwise pick up (the T-091 post-commit dirt class). Also
# covered by the synced .gitignore as a second line of defence.
while IFS= read -r stale; do
  rm -rf "$stale"
done < <(find "$HOME_REPO" -name .git -prune -o -name '*.sync-tmp*' -print 2>/dev/null)

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

  atomic_install "$src" "$dst" +x
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
    echo "  Removed: ${hook} — retired"
    ((UPDATED++))
  else
    echo "  OK    ${hook} — already absent"
    ((UNCHANGED++))
  fi
done

UNWIRE_SETTINGS="${HOME_REPO}/.claude/settings.json"
if [[ -f "$UNWIRE_SETTINGS" ]]; then
  python3 - "$UNWIRE_SETTINGS" <<'UNWIRE_PY' | sed 's/^/  /'
import json, os, sys

RETIRED = ("pre-tool-use.sh", "post-tool-use.sh",
           "session-context.py", "session-context.sh",
           "first-prompt-brief.py")
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
    # Temp-in-same-dir + atomic rename (T-098): settings.json has concurrent
    # readers (the harness) — never rewrite its inode in place.
    tmp = path + ".sync-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(settings, indent=2) + "\n")
    os.replace(tmp, path)
    print("Unwired retired hook entries from settings.json")
else:
    print("OK    settings.json — no retired hook wiring")
UNWIRE_PY
fi

# --- Retired identity fallback prune (PROJ-039/T-099, CC-409) ---
# `.claude/current-agent` was step 4 of the legacy resolution chain — a second
# source of truth next to the identity YAML. Identity now single-sources from
# workspaces/identity/*.identity.yaml; delete the file where present so
# interim hand-patches converge away on the next sync/TOOLING_UPDATE.

CURRENT_AGENT_DST="${HOME_REPO}/.claude/current-agent"
if [[ -f "$CURRENT_AGENT_DST" ]]; then
  rm -f "$CURRENT_AGENT_DST"
  echo "  Removed: .claude/current-agent — retired (T-099: identity YAML is the single source)"
  ((UPDATED++))
else
  echo "  OK    .claude/current-agent — already absent"
  ((UNCHANGED++))
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

  atomic_install_dir "$src" "$dst"
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

  atomic_install "$src" "$dst" +x
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
      atomic_install "$GITIGNORE_SRC" "$GITIGNORE_DST"
      echo "  Updated: .gitignore"
      ((UPDATED++))
    fi
  else
    atomic_install "$GITIGNORE_SRC" "$GITIGNORE_DST"
    echo "  Updated: .gitignore"
    ((UPDATED++))
  fi
fi

# --- SessionStart updater wiring in settings.json (PROJ-039/T-069, T-065 F1) ---
# The committed .claude/settings.json can never join the byte-copy set (its env
# block is per-repo), so the updater WIRING joins the sync set structurally
# instead: wire-update-tooling.py patches the hooks block in place — updater as
# the FIRST SessionStart command (trainee: immediately after
# trainee-session-branch.py, PROJ-011/T-030) — leaving env and every other hook
# untouched. This closes the T-065 F1
# chicken-and-egg: the release that ships the updater now also wires it.

echo ""
echo "==> Patching structural settings.json wiring (.claude/settings.json)"
echo "    (update-tooling.py SessionStart position; trainee: python3 shell guard, T-125)"

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
    atomic_install "$src" "$dst"
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

# --- Role skill set (team-lead coordination subset + T-106 resident skills) ---
# A team-lead home repo carries the coordination skill subset under .claude/skills/,
# byte-identical to the canonical agent-tooling/skills/ source and EXACTLY the subset
# (any out-of-subset skill is pruned — session ceremony stays hook-driven). PROJ-039/
# T-038. Since T-106 (CC-438, operator policy 2026-07-18) the Neon agent-skills
# (`neon`, `neon-postgres` — neondatabase/agent-skills, hash-locked via
# skills/skills-lock.json) are additionally required residents for the team-lead,
# coder, and trainer (training-admin) roles: for a team lead they ride the manifest;
# for coder/trainer they ARE the role's skill set. Policy: Neon skills are
# VERIFICATION-ONLY — DB changes are scripted (migrations/tooling), never applied
# ad hoc via skills. Every other role stays skill-free: a stray .claude/skills/ is
# flagged (and pruned on confirm).

# T-106 resident external skills per role (empty = none). Keep in lockstep with
# role_resident_skills in scaffold.sh and RESIDENT_SKILLS in test_skills_parity.py.
role_resident_skills() {
  case "$1" in
    team-lead|coder|trainer) echo "neon neon-postgres" ;;
    *) echo "" ;;
  esac
}
RESIDENT_SKILLS="$(role_resident_skills "$ROLE")"

# PROJ-039/T-122 (CC-520) role DOMAIN skills: first-party canonical skills a role
# needs to do its job, as opposed to the T-106 external residents. They ride the
# role's .claude/skills/ set but NOT the resident adjuncts (.agents/skills mirror /
# skills-lock.json — those are the hash-locked upstream-install artefacts).
#   trainer: create-trainee-brief — authoring trainee briefs + the enrolment-email
#     draft is the trainer's job (Athena). Curriculum authoring (Hestia,
#     curriculum-developer) does not brief trainees, so it does NOT get this skill.
# Keep in lockstep with role_domain_skills in scaffold.sh and DOMAIN_SKILLS in
# test_skills_parity.py.
role_domain_skills() {
  case "$1" in
    trainer) echo "create-trainee-brief" ;;
    *) echo "" ;;
  esac
}
DOMAIN_SKILLS="$(role_domain_skills "$ROLE")"

SKILLS_DST="${HOME_REPO}/.claude/skills"
SUBSET_SET=""
echo ""
if [[ "$ROLE" == "team-lead" ]]; then
  if [[ ! -f "$TEAM_LEAD_SKILLS_MANIFEST" ]]; then
    echo "==> SKIP  skills/ — manifest not found (${TEAM_LEAD_SKILLS_MANIFEST})"
    ((SKIPPED++))
  else
    while IFS= read -r line || [[ -n "$line" ]]; do
      entry="${line%%#*}"; entry="${entry//[[:space:]]/}"
      [[ -z "$entry" ]] && continue
      SUBSET_SET="${SUBSET_SET} ${entry}"
    done < "$TEAM_LEAD_SKILLS_MANIFEST"
  fi
else
  SUBSET_SET=" ${RESIDENT_SKILLS}"
  SUBSET_SET="${SUBSET_SET% }"
fi
# Domain skills ride every role's set (team-lead's manifest included).
if [[ -n "${DOMAIN_SKILLS// /}" ]]; then
  SUBSET_SET="${SUBSET_SET} ${DOMAIN_SKILLS}"
fi

if [[ -n "${SUBSET_SET// /}" ]]; then
  echo "==> Syncing role skill set (${ROLE}) → ${SKILLS_DST}"
  mkdir -p "$SKILLS_DST"
  for entry in $SUBSET_SET; do
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
    atomic_install_dir "$src" "$dst"
    echo "  Updated: skills/${entry}"
    ((UPDATED++))
  done

  # Prune any skill NOT in the role's set (session ceremony / drift).
  #
  # PRE-PRUNE ORPHAN GUARD (PROJ-039/T-122, CC-520). Pruning an out-of-subset skill
  # is only safe when the skill EXISTS in the canonical source: the copy is then
  # recoverable and the prune is just de-drifting. A skill with NO canonical source
  # is repo-local work someone invented (Athena's create-trainee-brief was one sync
  # from deletion) — deleting it under --yes is unrecoverable DATA LOSS, and silent.
  # So: orphans are RETAINED and reported loudly, never deleted implicitly. Deleting
  # one requires the explicit --prune-orphan-skills opt-in.
  if [[ -d "$SKILLS_DST" ]]; then
    while IFS= read -r d; do
      name="$(basename "$d")"
      case " ${SUBSET_SET} " in
        *" ${name} "*) : ;;  # in subset — keep
        *)
          if [[ ! -d "${SKILLS_SRC}/${name}" ]]; then
            if [[ $PRUNE_ORPHANS -eq 1 ]]; then
              rm -rf "$d"
              echo "  Pruned: skills/${name} (ORPHAN — deleted on explicit --prune-orphan-skills)"
              ((UPDATED++))
              continue
            fi
            ORPHAN_SKILLS="${ORPHAN_SKILLS} ${name}"
            echo "  ORPHAN skills/${name} — no canonical source in agent-tooling/skills/."
            echo "         KEPT (not pruned): deleting it would be unrecoverable."
            ((SKIPPED++))
            continue
          fi
          if [[ $YES -eq 0 ]]; then
            printf "  Prune out-of-subset skills/%s? [y/N] " "$name"
            read -r answer </dev/tty
            [[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "  Kept."; continue; }
          fi
          rm -rf "$d"
          echo "  Pruned: skills/${name} (not in the '${ROLE}' role skill set)"
          ((UPDATED++))
          ;;
      esac
    done < <(find "$SKILLS_DST" -mindepth 1 -maxdepth 1 -type d)
  fi
else
  # Skill-free roles are hooks-only. A stray skills/ is a regression.
  if [[ -d "$SKILLS_DST" ]]; then
    echo "==> WARNING: role '${ROLE}' is hooks-only but .claude/skills/ exists."
    # Same orphan guard as the prune path (T-122): a stray skills/ holding a skill
    # with no canonical source is someone's repo-local work, not recoverable drift.
    STRAY_ORPHANS=""
    while IFS= read -r d; do
      name="$(basename "$d")"
      [[ -d "${SKILLS_SRC}/${name}" ]] || STRAY_ORPHANS="${STRAY_ORPHANS} ${name}"
    done < <(find "$SKILLS_DST" -mindepth 1 -maxdepth 1 -type d)
    if [[ -n "${STRAY_ORPHANS// /}" && $PRUNE_ORPHANS -eq 0 ]]; then
      for name in $STRAY_ORPHANS; do
        ORPHAN_SKILLS="${ORPHAN_SKILLS} ${name}"
        echo "  ORPHAN skills/${name} — no canonical source in agent-tooling/skills/."
      done
      echo "  KEPT .claude/skills/ — removing it would be unrecoverable."
      ((SKIPPED++))
    elif [[ $YES -eq 0 ]]; then
      printf "  Remove .claude/skills/ (this role carries no skills)? [y/N] "
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

# --- T-106 resident-skill adjuncts: .agents/skills mirror + skills-lock.json ---
# The Neon skill install carries two adjacent artefacts the role-sync must manage
# (not prune): a multi-runtime `.agents/skills/` mirror of the same skill dirs, and
# the root `skills-lock.json` hash-lock (canonical copy: skills/skills-lock.json).
# Only roles with resident skills carry them; both are byte-identity-synced.
AGENTS_SKILLS_DST="${HOME_REPO}/.agents/skills"
SKILLS_LOCK_SRC="${SKILLS_SRC}/skills-lock.json"
SKILLS_LOCK_DST="${HOME_REPO}/skills-lock.json"
if [[ -n "${RESIDENT_SKILLS// /}" ]]; then
  echo ""
  echo "==> Syncing resident-skill adjuncts (${ROLE}) → .agents/skills/ + skills-lock.json"
  mkdir -p "$AGENTS_SKILLS_DST"
  for entry in $RESIDENT_SKILLS; do
    src="${SKILLS_SRC}/${entry}"
    dst="${AGENTS_SKILLS_DST}/${entry}"
    if [[ ! -d "$src" ]]; then
      echo "  SKIP  .agents/skills/${entry} — not found in agent-tooling (${src})"
      ((SKIPPED++)); continue
    fi
    if [[ -d "$dst" ]] && diff -rq -x '__pycache__' -x '*.pyc' "$src" "$dst" > /dev/null 2>&1; then
      echo "  OK    .agents/skills/${entry} — unchanged"
      ((UNCHANGED++)); continue
    fi
    if [[ $YES -eq 0 ]]; then
      printf "  Overwrite .agents/skills/%s? [y/N] " "$entry"
      read -r answer </dev/tty
      if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
        echo "  Skipped."; ((SKIPPED++)); continue
      fi
    fi
    atomic_install_dir "$src" "$dst"
    echo "  Updated: .agents/skills/${entry}"
    ((UPDATED++))
  done
  if [[ -f "$SKILLS_LOCK_SRC" ]]; then
    if [[ -f "$SKILLS_LOCK_DST" ]] && diff -q "$SKILLS_LOCK_SRC" "$SKILLS_LOCK_DST" > /dev/null 2>&1; then
      echo "  OK    skills-lock.json — unchanged"
      ((UNCHANGED++))
    else
      APPLY=1
      if [[ $YES -eq 0 ]]; then
        printf "  Overwrite skills-lock.json? [y/N] "
        read -r answer </dev/tty
        [[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "  Skipped."; ((SKIPPED++)); APPLY=0; }
      fi
      if [[ $APPLY -eq 1 ]]; then
        atomic_install "$SKILLS_LOCK_SRC" "$SKILLS_LOCK_DST" 644
        echo "  Updated: skills-lock.json"
        ((UPDATED++))
      fi
    fi
  else
    echo "  SKIP  skills-lock.json — canonical copy not found (${SKILLS_LOCK_SRC})"
    ((SKIPPED++))
  fi
fi

# --- Operating manual (team-lead variant only, PROJ-039/T-081) ---
# The compact team-lead operating manual is a resident, sync-managed root-level
# artefact (like .gitignore): rendered from scaffold/team-lead/OPERATING-MANUAL
# .template with the repo's own {team, agent} substituted, so existing team-lead
# repos receive updates via TOOLING_UPDATE. Byte-identity here means "identical
# to the freshly rendered template", not to the raw template file. Other roles
# carry no manual and this section is skipped for them.

MANUAL_TEMPLATE="${AGENT_TOOLING_DIR}/scaffold/team-lead/OPERATING-MANUAL.template"
MANUAL_DST="${HOME_REPO}/OPERATING-MANUAL.md"
MANUAL_RENDERED=""
if [[ "$ROLE" == "team-lead" ]]; then
  echo ""
  echo "==> Syncing team-lead operating manual → ${MANUAL_DST}"
  if [[ ! -f "$MANUAL_TEMPLATE" ]]; then
    echo "  SKIP  OPERATING-MANUAL.md — template not found (${MANUAL_TEMPLATE})"
    ((SKIPPED++))
  else
    # Derive {team, agent} from the identity YAML's home_repo (authoritative —
    # survives a --home-repo path whose basename is not the repo name, e.g. a
    # test scaffold in /tmp), falling back to the directory basename.
    HR_NAME=""
    M_IDENTITY_FILE="$(find "${HOME_REPO}/workspaces/identity" -name "*.identity.yaml" 2>/dev/null | head -1)"
    if [[ -n "$M_IDENTITY_FILE" ]]; then
      HR_NAME="$(grep '^home_repo:' "$M_IDENTITY_FILE" | head -1 | sed 's/^home_repo:[[:space:]]*//' | tr -d '[:space:]')"
    fi
    [[ -z "$HR_NAME" ]] && HR_NAME="$(basename "$HOME_REPO")"
    if [[ "$HR_NAME" =~ ^home-([a-z0-9]+)-([a-z0-9-]+)$ ]]; then
      M_TEAM="${BASH_REMATCH[1]}"
      M_AGENT="${BASH_REMATCH[2]}"
      M_AGENT_CAP="$(echo "${M_AGENT:0:1}" | tr '[:lower:]' '[:upper:]')${M_AGENT:1}"
      MANUAL_RENDERED="$(mktemp)"
      sed -e "s|__AGENT__|${M_AGENT_CAP}|g" \
          -e "s|__AGENT_LC__|${M_AGENT}|g" \
          -e "s|__TEAM__|${M_TEAM}|g" \
          -e "s|__TEAM_REPO__|${M_TEAM}Team|g" \
          -e "s|__REPO_NAME__|${HR_NAME}|g" \
        "$MANUAL_TEMPLATE" > "$MANUAL_RENDERED"

      if [[ -f "$MANUAL_DST" ]] && diff -q "$MANUAL_RENDERED" "$MANUAL_DST" > /dev/null 2>&1; then
        echo "  OK    OPERATING-MANUAL.md — unchanged"
        ((UNCHANGED++))
      else
        if [[ -f "$MANUAL_DST" ]]; then
          echo "  DIFF  OPERATING-MANUAL.md:"
          diff -u "$MANUAL_DST" "$MANUAL_RENDERED" || true
          echo ""
        else
          echo "  NEW   OPERATING-MANUAL.md — not present in home repo"
        fi
        APPLY=1
        if [[ $YES -eq 0 ]]; then
          printf "  Overwrite %s? [y/N] " "OPERATING-MANUAL.md"
          read -r answer </dev/tty
          if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
            echo "  Skipped."
            ((SKIPPED++))
            APPLY=0
          fi
        fi
        if [[ $APPLY -eq 1 ]]; then
          # 644, not cp -p: the rendered source is a 600 mktemp file.
          atomic_install "$MANUAL_RENDERED" "$MANUAL_DST" 644
          echo "  Updated: OPERATING-MANUAL.md"
          ((UPDATED++))
        fi
      fi
    else
      echo "  SKIP  OPERATING-MANUAL.md — cannot derive team/agent from '${HR_NAME}' (expected home-<team>-<agent>)"
      ((SKIPPED++))
    fi
  fi
fi

# --- Home-repo README (operator-facing, all non-trainee roles, PROJ-039/T-095) ---
# Resident, sync-managed root-level artefact (same pattern as OPERATING-MANUAL.md
# above): rendered from scaffold/home-repo-README.template with the repo's own
# {team, agent, role} substituted, so every non-trainee home repo receives updates
# via TOOLING_UPDATE. Byte-identity here means "identical to the freshly rendered
# template". Trainee carries no synced README — trainee-owned, byte-untouched
# (PROJ-011/T-025 R-7) — and this section is skipped for it.

README_TEMPLATE="${AGENT_TOOLING_DIR}/scaffold/home-repo-README.template"
README_DST="${HOME_REPO}/README.md"
README_RENDERED=""
if [[ "$ROLE" != "trainee" ]]; then
  echo ""
  echo "==> Syncing home-repo README → ${README_DST}"
  if [[ ! -f "$README_TEMPLATE" ]]; then
    echo "  SKIP  README.md — template not found (${README_TEMPLATE})"
    ((SKIPPED++))
  else
    R_HR_NAME=""
    R_IDENTITY_FILE="$(find "${HOME_REPO}/workspaces/identity" -name "*.identity.yaml" 2>/dev/null | head -1)"
    if [[ -n "$R_IDENTITY_FILE" ]]; then
      R_HR_NAME="$(grep '^home_repo:' "$R_IDENTITY_FILE" | head -1 | sed 's/^home_repo:[[:space:]]*//' | tr -d '[:space:]')"
    fi
    [[ -z "$R_HR_NAME" ]] && R_HR_NAME="$(basename "$HOME_REPO")"
    if [[ "$R_HR_NAME" =~ ^home-([a-z0-9]+)-([a-z0-9-]+)$ ]]; then
      R_TEAM="${BASH_REMATCH[1]}"
      R_AGENT="${BASH_REMATCH[2]}"
      R_AGENT_CAP="$(echo "${R_AGENT:0:1}" | tr '[:lower:]' '[:upper:]')${R_AGENT:1}"
      case "$ROLE" in
        team-lead)             R_ROLE_TITLE="Team Lead" ;;
        coder)                 R_ROLE_TITLE="Coder" ;;
        archivist)             R_ROLE_TITLE="Archivist" ;;
        trainer)               R_ROLE_TITLE="Trainer" ;;
        cluster-operator)      R_ROLE_TITLE="Cluster Operator" ;;
        curriculum-developer)  R_ROLE_TITLE="Curriculum Developer" ;;
        historian)             R_ROLE_TITLE="Historian" ;;
        strategist)            R_ROLE_TITLE="Strategist" ;;
        *)                     R_ROLE_TITLE="${ROLE}" ;;
      esac
      README_RENDERED="$(mktemp)"
      sed -e "s|__AGENT__|${R_AGENT_CAP}|g" \
          -e "s|__AGENT_LC__|${R_AGENT}|g" \
          -e "s|__TEAM__|${R_TEAM}|g" \
          -e "s|__TEAM_REPO__|${R_TEAM}Team|g" \
          -e "s|__REPO_NAME__|${R_HR_NAME}|g" \
          -e "s|__ROLE_TITLE__|${R_ROLE_TITLE}|g" \
        "$README_TEMPLATE" > "$README_RENDERED"

      if [[ -f "$README_DST" ]] && diff -q "$README_RENDERED" "$README_DST" > /dev/null 2>&1; then
        echo "  OK    README.md — unchanged"
        ((UNCHANGED++))
      else
        if [[ -f "$README_DST" ]]; then
          echo "  DIFF  README.md:"
          diff -u "$README_DST" "$README_RENDERED" || true
          echo ""
        else
          echo "  NEW   README.md — not present in home repo"
        fi
        APPLY=1
        if [[ $YES -eq 0 ]]; then
          printf "  Overwrite %s? [y/N] " "README.md"
          read -r answer </dev/tty
          if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
            echo "  Skipped."
            ((SKIPPED++))
            APPLY=0
          fi
        fi
        if [[ $APPLY -eq 1 ]]; then
          # 644, not cp -p: the rendered source is a 600 mktemp file.
          atomic_install "$README_RENDERED" "$README_DST" 644
          echo "  Updated: README.md"
          ((UPDATED++))
        fi
      fi
    else
      echo "  SKIP  README.md — cannot derive team/agent from '${R_HR_NAME}' (expected home-<team>-<agent>)"
      ((SKIPPED++))
    fi
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
WIRE_CHECK="$(python3 "${TOOLS_SRC}/wire-update-tooling.py" --settings "${HOME_REPO}/.claude/settings.json" --role "$ROLE" --check 2>&1)" \
  || { echo "  DRIFT: settings.json — ${WIRE_CHECK#*CHECK FAIL — }"; DRIFT=1; }
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

# Operating-manual invariant (PROJ-039/T-081): a team-lead home repo's root
# OPERATING-MANUAL.md MUST be byte-identical to the freshly rendered template.
if [[ "$ROLE" == "team-lead" && -n "$MANUAL_RENDERED" ]]; then
  diff -q "$MANUAL_RENDERED" "$MANUAL_DST" > /dev/null 2>&1 \
    || { echo "  DRIFT: OPERATING-MANUAL.md (not byte-identical to rendered template / missing)"; DRIFT=1; }
fi

# Home-repo README invariant (PROJ-039/T-095): a non-trainee home repo's root
# README.md MUST be byte-identical to the freshly rendered template.
if [[ "$ROLE" != "trainee" && -n "$README_RENDERED" ]]; then
  diff -q "$README_RENDERED" "$README_DST" > /dev/null 2>&1 \
    || { echo "  DRIFT: README.md (not byte-identical to rendered template / missing)"; DRIFT=1; }
fi

# Skills invariant (PROJ-039/T-038 + T-106): a home repo with a role skill set
# (team-lead coordination subset; coder/trainer Neon residents) MUST carry exactly
# that set under .claude/skills/, each byte-identical to source; a skill-free role
# MUST have NO .claude/skills/ at all.
if [[ -n "${SUBSET_SET// /}" ]]; then
  for skill in $SUBSET_SET; do
    diff -rq -x '__pycache__' -x '*.pyc' "${SKILLS_SRC}/${skill}" "${SKILLS_DST}/${skill}" > /dev/null 2>&1 \
      || { echo "  DRIFT: skills/${skill} (not byte-identical to source / missing)"; DRIFT=1; }
  done
  while IFS= read -r d; do
    name="$(basename "$d")"
    case " ${SUBSET_SET} " in
      *" ${name} "*) : ;;
      # T-122: an out-of-subset skill WITH a canonical source is recoverable drift
      # and stays a hard DRIFT. An ORPHAN (no canonical source) is retained work,
      # deliberately kept by the pre-prune guard — a WARNING, not a failure, so the
      # rest of the tooling update still lands on the repo carrying it.
      *) if [[ -d "${SKILLS_SRC}/${name}" ]]; then
           echo "  DRIFT: skills/${name} (out of subset — not in the '${ROLE}' role skill set)"; DRIFT=1
         else
           echo "  WARN:  skills/${name} (ORPHAN — kept; no canonical source to sync from)"
         fi ;;
    esac
  done < <(find "$SKILLS_DST" -mindepth 1 -maxdepth 1 -type d 2>/dev/null)
elif [[ -d "$SKILLS_DST" ]]; then
  if [[ -n "${ORPHAN_SKILLS// /}" ]]; then
    echo "  WARN:  .claude/skills/ kept for hooks-only role '${ROLE}' (holds orphan skills:${ORPHAN_SKILLS})"
  else
    echo "  DRIFT: .claude/skills/ present for hooks-only role '${ROLE}' (this role carries no skills)"
    DRIFT=1
  fi
fi
# T-106 resident-skill adjuncts invariant: .agents/skills mirror + skills-lock.json
# byte-identical to canonical for roles carrying the Neon residents.
if [[ -n "${RESIDENT_SKILLS// /}" ]]; then
  for skill in $RESIDENT_SKILLS; do
    diff -rq -x '__pycache__' -x '*.pyc' "${SKILLS_SRC}/${skill}" "${AGENTS_SKILLS_DST}/${skill}" > /dev/null 2>&1 \
      || { echo "  DRIFT: .agents/skills/${skill} (not byte-identical to source / missing)"; DRIFT=1; }
  done
  if [[ -f "$SKILLS_LOCK_SRC" ]]; then
    diff -q "$SKILLS_LOCK_SRC" "$SKILLS_LOCK_DST" > /dev/null 2>&1 \
      || { echo "  DRIFT: skills-lock.json (not byte-identical to canonical / missing)"; DRIFT=1; }
  fi
fi

# --- Orphan-skill report (PROJ-039/T-122, CC-520) ---
# Loud, actionable, and printed even on a clean run: the whole point of the guard is
# that a repo-local skill with no canonical source is never deleted SILENTLY.
if [[ -n "${ORPHAN_SKILLS// /}" ]]; then
  echo ""
  echo "=============================================================================="
  echo "ORPHAN SKILLS RETAINED — not pruned, not sync-managed"
  echo "=============================================================================="
  for name in $ORPHAN_SKILLS; do
    echo "  .claude/skills/${name}  (no agent-tooling/skills/${name})"
  done
  echo ""
  echo "  These exist only in this repo. The role sync would otherwise have deleted"
  echo "  them and no canonical copy exists to restore from, so they were KEPT."
  echo ""
  echo "  Resolve by either:"
  echo "    (a) canonicalise — copy the skill into agent-tooling/skills/<name>/ and"
  echo "        add it to the role's set (role_domain_skills / the team-lead"
  echo "        manifest) so it becomes sync-managed; or"
  echo "    (b) discard — re-run this sync with --prune-orphan-skills to delete it."
  echo "=============================================================================="
  echo ""
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
# Team-lead operating manual (PROJ-039/T-081): root-level, sync-managed, rendered
# per-repo — the hash is of the shipped (rendered) file, same as everything else.
if os.environ["ROLE"] == "team-lead":
    manual = home_repo_dir / "OPERATING-MANUAL.md"
    if manual.is_file():
        root_files["OPERATING-MANUAL.md"] = hashlib.sha256(manual.read_bytes()).hexdigest()
# Home-repo operator README (PROJ-039/T-095): root-level, sync-managed, rendered
# per-repo for every non-trainee role — the hash is of the shipped (rendered) file.
if os.environ["ROLE"] != "trainee":
    readme = home_repo_dir / "README.md"
    if readme.is_file():
        root_files["README.md"] = hashlib.sha256(readme.read_bytes()).hexdigest()

manifest = {
    "version": os.environ["TOOLING_VERSION"],
    "source_commit": os.environ["SOURCE_COMMIT"],
    "role": os.environ["ROLE"],
    "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "files": files,
    "root_files": root_files,
}
# lifecycle_mode (PROJ-039/T-084): branch|trunk, default "branch". This field is
# OPERATOR-set, not sync-derived — sync must carry it forward unchanged rather than
# stomping it back to the default on every re-sync (the same "preserve, don't derive"
# treatment synced_at gets below, just permanent rather than idempotency-scoped).
# Absent on a repo never migrated: every finalise/SessionStart reader treats a
# missing key as "branch" (see lib/lifecycle_mode.py), so this key is only written
# once a prior sync (or T-086 migration) already put it there.
existing_path = claude_dir / "tooling-manifest.json"
existing_lifecycle_mode = None
if existing_path.is_file():
    try:
        existing_lifecycle_mode = json.loads(
            existing_path.read_text(encoding="utf-8")).get("lifecycle_mode")
    except ValueError:
        pass
if existing_lifecycle_mode:
    manifest["lifecycle_mode"] = existing_lifecycle_mode
# Idempotent re-sync (PROJ-039/T-069): an already-current repo must produce NO
# diff — otherwise every TOOLING_UPDATE launch commits a synced_at-only change.
# Keep the existing timestamp when nothing else moved.
if existing_path.is_file():
    try:
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
        if {k: v for k, v in existing.items() if k != "synced_at"} == \
           {k: v for k, v in manifest.items() if k != "synced_at"} and existing.get("synced_at"):
            manifest["synced_at"] = existing["synced_at"]
    except ValueError:
        pass
claude_dir.mkdir(parents=True, exist_ok=True)
# Temp-in-same-dir + atomic rename (T-098): concurrent SessionStart readers
# (drift report, T-069 guard) must never see a half-written manifest.
tmp = claude_dir / "tooling-manifest.json.sync-tmp"
tmp.write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(tmp, claude_dir / "tooling-manifest.json")
print(f"  Wrote {claude_dir / 'tooling-manifest.json'} ({len(files)} files, "
      f"version={manifest['version']}, source_commit={manifest['source_commit'][:8]})")
PYEOF

[[ -n "$MANUAL_RENDERED" ]] && rm -f "$MANUAL_RENDERED" || true
