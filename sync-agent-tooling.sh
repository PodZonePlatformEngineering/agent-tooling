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
# user-prompt-submit + pre/post-tool-use + post-compact + stop, with
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
SUBSTRATE_BASE="session-start.sh user-prompt-submit.sh pre-tool-use.sh post-tool-use.sh post-compact.sh stop.sh append-session-stop.py session-end-finalise.py"
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
    trainee)               echo "${SUBSTRATE_BASE} session-materialise.py first-prompt-brief.py trainee-session-branch.py" ;;
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
  echo "Byte-identity invariant: PASS — hooks + ${DEP_DIRS} + lib closure + skills match agent-tooling."
else
  echo "Byte-identity invariant: FAIL — drift listed above (re-run without --skip, or investigate)."
  exit 1
fi
