#!/usr/bin/env bash
# sync-skills.sh — distribute the canonical substrate skills from the agent-tooling
# source (`skills/`) to the team-repo mirrors, keeping each mirror byte-identical to
# the source for every skill the source defines.
#
# Why this exists (PROJ-039/T-034, CC-333): unlike hooks / primitives / lib (covered
# by sync-agent-tooling.sh's byte-identity invariant), skills had NO enforced parity,
# and drifted across the four copies three times (T-028 reconcile → re-drift at T-031).
# This is the skills analogue of sync-agent-tooling.sh: source is canonical, mirrors
# are byte-identical, and the sync ends by asserting the invariant (fails loudly on
# any residual drift).
#
# Canonical direction (see docs/skills-sync.md):
#   SOURCE  : agent-tooling/skills/                  (the one editable copy)
#   MIRRORS : podzoneAgentTeam/.claude/skills/       (apex)
#             trainingTeam/.claude/skills/           (fissioned team)
#             roadmapTeam/.claude/skills/            (fissioned team)
#
# Scope rule (mirrors the lib home-runtime-lib.manifest closure model): the source
# skill set is the unit of parity. For every skill that EXISTS in the source, each
# mirror's copy MUST be byte-identical. A mirror MAY carry ADDITIONAL skills not in
# the source (apex management skills like push-images / stand-up-team; the teams'
# usage-report) — those are out of scope and never touched. A mirror skill that must
# legitimately differ for env reasons is named in the allowlist and exempted.
#
# Usage:
#   bash sync-skills.sh                 # sync source -> all default mirrors, assert
#   bash sync-skills.sh --check         # assert parity only; make NO changes (CI / guard)
#   bash sync-skills.sh --yes           # non-interactive (overwrite without prompting)
#
# Flags:
#   --source /path        Default: <script-dir>/skills
#   --mirror /path        Add a mirror skills dir (repeatable). If any --mirror is
#                         given, the built-in defaults are NOT used.
#   --allowlist /path     Default: <script-dir>/skills-sync-allowlist
#   --check               Verify only; never write. Exit non-zero on drift.
#   --yes                 Skip per-skill overwrite prompts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SOURCE="${SCRIPT_DIR}/skills"
ALLOWLIST="${SCRIPT_DIR}/skills-sync-allowlist"
CHECK=0
YES=0
MIRRORS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)    SOURCE="$2"; shift 2 ;;
    --mirror)    MIRRORS+=("$2"); shift 2 ;;
    --allowlist) ALLOWLIST="$2"; shift 2 ;;
    --check)     CHECK=1; shift ;;
    --yes)       YES=1; shift ;;
    --help|-h)
      sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Default mirrors: sibling team repos alongside agent-tooling's parent.
if [[ ${#MIRRORS[@]} -eq 0 ]]; then
  PARENT="$(cd "${SCRIPT_DIR}/.." && pwd)"
  MIRRORS=(
    "${PARENT}/podzoneAgentTeam/.claude/skills"
    "${PARENT}/trainingTeam/.claude/skills"
    "${PARENT}/roadmapTeam/.claude/skills"
  )
fi

if [[ ! -d "$SOURCE" ]]; then
  echo "Error: source skills dir not found: ${SOURCE}" >&2
  exit 1
fi

# --- Allowlist ---
# Format: one `mirror-basename:skill-name` per line; `#` comments and blanks ignored.
# `mirror-basename` is the basename of the mirror repo dir (e.g. trainingTeam), so the
# allowlist is independent of absolute paths. A matching skill is exempt from both the
# sync and the invariant for that mirror.
ALLOWED=""
if [[ -f "$ALLOWLIST" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    entry="${line%%#*}"; entry="${entry//[[:space:]]/}"
    [[ -z "$entry" ]] && continue
    ALLOWED="${ALLOWED} ${entry}"
  done < "$ALLOWLIST"
fi

# mirror_key /abs/path/to/<repo>/.claude/skills -> "<repo>"
mirror_key() {
  local p="$1"
  # strip a trailing /.claude/skills (or /skills) to get the repo dir, then basename
  p="${p%/}"
  p="${p%/skills}"
  p="${p%/.claude}"
  basename "$p"
}

is_allowed() {  # is_allowed <mirror-key> <skill>
  case " ${ALLOWED} " in
    *" ${1}:${2} "*) return 0 ;;
    *) return 1 ;;
  esac
}

# Source skill set = immediate subdirectories of $SOURCE.
SKILLS=()
while IFS= read -r d; do
  SKILLS+=("$(basename "$d")")
done < <(find "$SOURCE" -mindepth 1 -maxdepth 1 -type d | sort)

if [[ ${#SKILLS[@]} -eq 0 ]]; then
  echo "Error: source has no skills: ${SOURCE}" >&2
  exit 1
fi

echo "==> Source: ${SOURCE}"
echo "==> Skills in scope: ${SKILLS[*]}"
echo ""

UPDATED=0
UNCHANGED=0
SKIPPED=0

if [[ $CHECK -eq 0 ]]; then
  for mirror in "${MIRRORS[@]}"; do
    key="$(mirror_key "$mirror")"
    if [[ ! -d "$mirror" ]]; then
      echo "  SKIP  mirror absent: ${mirror}"
      ((SKIPPED++)) || true
      continue
    fi
    echo "==> Mirror ${key} (${mirror})"
    for skill in "${SKILLS[@]}"; do
      src="${SOURCE}/${skill}"
      dst="${mirror}/${skill}"
      if is_allowed "$key" "$skill"; then
        echo "  ALLOW ${skill} — allowlisted exception, not synced"
        ((SKIPPED++)) || true
        continue
      fi
      if [[ -d "$dst" ]] && diff -rq -x '__pycache__' -x '*.pyc' "$src" "$dst" >/dev/null 2>&1; then
        echo "  OK    ${skill} — unchanged"
        ((UNCHANGED++)) || true
        continue
      fi
      if [[ -d "$dst" ]]; then
        echo "  DIFF  ${skill}:"
        diff -rq -x '__pycache__' -x '*.pyc' "$dst" "$src" 2>&1 | sed 's/^/        /' || true
      else
        echo "  NEW   ${skill} — not present in mirror"
      fi
      if [[ $YES -eq 0 ]]; then
        printf "  Overwrite %s/%s? [y/N] " "$key" "$skill"
        read -r answer </dev/tty
        if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
          echo "  Skipped."
          ((SKIPPED++)) || true
          continue
        fi
      fi
      rm -rf "$dst"
      cp -R "$src" "$dst"
      find "$dst" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
      echo "  Updated: ${skill}"
      ((UPDATED++)) || true
    done
    echo ""
  done
  echo "Sync complete: ${UPDATED} updated, ${UNCHANGED} unchanged, ${SKIPPED} skipped."
  echo ""
fi

# --- Byte-identity invariant (the regression guard) ---
# For every source skill, every present mirror MUST be byte-identical (allowlisted
# skills exempt). Any residual diff is drift — fail loudly. This block also runs
# standalone under --check (no prior sync), making it the CI / pre-consolidate guard.

echo "==> Byte-identity invariant"
DRIFT=0
CHECKED=0
for mirror in "${MIRRORS[@]}"; do
  key="$(mirror_key "$mirror")"
  [[ -d "$mirror" ]] || { echo "  SKIP  mirror absent: ${key}"; continue; }
  for skill in "${SKILLS[@]}"; do
    if is_allowed "$key" "$skill"; then
      echo "  ALLOW ${key}/${skill} (exempt)"
      continue
    fi
    src="${SOURCE}/${skill}"
    dst="${mirror}/${skill}"
    ((CHECKED++)) || true
    if [[ ! -d "$dst" ]]; then
      echo "  DRIFT ${key}/${skill} — missing in mirror"
      DRIFT=1
      continue
    fi
    if ! diff -rq -x '__pycache__' -x '*.pyc' "$src" "$dst" >/dev/null 2>&1; then
      echo "  DRIFT ${key}/${skill}:"
      diff -rq -x '__pycache__' -x '*.pyc' "$src" "$dst" 2>&1 | sed 's/^/        /' || true
      DRIFT=1
    fi
  done
done

if [[ $DRIFT -eq 0 ]]; then
  echo "Byte-identity invariant: PASS — ${CHECKED} mirror-skill pairs match source."
else
  echo "Byte-identity invariant: FAIL — drift listed above (run without --check to fix)." >&2
  exit 1
fi
