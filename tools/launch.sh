#!/usr/bin/env bash
# launch.sh — reusable launch wrapper (PROJ-039/T-108, launch-wrapper-proposal.md Part B)
#
# Collapses /launch-session's Steps 3-8 into one invocation: stages the home
# repo + every named working repo (preflight-abort, pull, branch+PR
# pre-creation, lock) BEFORE Claude ever runs, then runs `claude -p` in an
# auto-rotating retry loop across the four subscription tokens
# (CLAUDE_CODE_OAUTH_TOKEN — subscription auth, never ANTHROPIC_AUTH_TOKEN).
#
# The wrapper owns 100% of git ceremony for both the home repo and every
# working repo: at every loop-exit point AND at final cleanup it commits
# ("wip: {brief-id} attempt {n}") and pushes whatever the inner session
# touched. The inner `claude -p` session should never need to run git itself.
#
# PROJ-039/T-210: this script does NOT call secretctl itself — `secretctl
# run`'s raw CLI has no non-interactive auth path (confirmed live: it always
# tries to open a TTY for the master-password prompt and fails headless).
# Before calling this script, the Team Lead must resolve the four
# subscription tokens via `resolve-launch-tokens.py` run through the secrets
# MCP tool (the one path that IS non-interactive) into a local, gitignored,
# 0600 file (default `~/.claude/launch-tokens.resolved.json`, override with
# `--tokens-file`) — see that script's own docstring. This wrapper then reads
# tokens from that file BY INDEX; it never re-derives a secretctl key name.
#
# Usage:
#   launch.sh <brief-id> [--repos repo1,repo2,...] [--home-repo <path>]
#              [--org <github-org>] [--api-retry-cap N] [--api-retry-backoff-s N]
#              [--tokens-file <path>]
#
# `<brief-id>` must already be an APPROVED brief in the `briefs` Qdrant
# collection (create-brief.py --approve). Everything else the wrapper needs
# (team, assignee) is resolved from that brief.
#
# No queue/scheduler — single-shot invocation only (out of scope, proposal
# §3.3 supersession note). Call this once per dispatch; a "sequence of
# headless sessions" is satisfied by calling it repeatedly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLING_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- tokens (proposal §4 — deterministic order) ---
# PROJ-039/T-210: `secretctl run`'s raw CLI has no non-interactive auth path
# (only `secretctl mcp-server` reads SECRETCTL_PASSWORD) — confirmed live,
# it always tries to open a TTY for the master-password prompt and fails
# under a detached/backgrounded script. Tokens are pre-resolved by the Team
# Lead via `resolve-launch-tokens.py` (run through the secrets MCP tool,
# the one path that IS non-interactive) into a local, gitignored, 0600 file
# this script reads BY INDEX — no secretctl call happens inside this script
# at all any more.
TOKENS_FILE="${HOME}/.claude/launch-tokens.resolved.json"

# PROJ-039/T-216: rotation-fairness cursor. Every invocation used to start at
# index 0 unconditionally (confirmed live: every dispatch this session hit
# 'colleym' first) — harmless for one dispatch at a time, but two invocations
# launched in parallel would both authenticate against the SAME subscription
# concurrently. A persistent cursor file makes successive invocations start on
# the next token round-robin; --token-index overrides it explicitly (the right
# tool for a deliberately staggered parallel launch, vs. relying on the
# auto-advancing cursor's read-then-write, which is not race-proof under true
# simultaneous launches — good enough for this fleet's actual concurrency, not
# a distributed lock).
CURSOR_FILE="${HOME}/.claude/launch-token-cursor"
TOKEN_INDEX_OVERRIDE=""

ORG="PodZonePlatformEngineering"
HOME_REPO_DIR=""
REPOS_CSV=""
API_RETRY_CAP=3
API_RETRY_BACKOFF_S=30
BRIEF_ID=""

usage() {
  echo "Usage: $0 <brief-id> [--repos repo1,repo2,...] [--home-repo <path>] [--org <org>] [--api-retry-cap N] [--api-retry-backoff-s N] [--tokens-file <path>] [--token-index N]" >&2
  exit 1
}

[[ $# -ge 1 ]] || usage
BRIEF_ID="$1"; shift
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repos)              REPOS_CSV="${2:?}"; shift 2 ;;
    --home-repo)           HOME_REPO_DIR="${2:?}"; shift 2 ;;
    --org)                  ORG="${2:?}"; shift 2 ;;
    --api-retry-cap)        API_RETRY_CAP="${2:?}"; shift 2 ;;
    --api-retry-backoff-s)  API_RETRY_BACKOFF_S="${2:?}"; shift 2 ;;
    --tokens-file)          TOKENS_FILE="${2:?}"; shift 2 ;;
    --token-index)          TOKEN_INDEX_OVERRIDE="${2:?}"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

log()   { echo "[launch.sh] $*"; }
abort() { echo "[launch.sh] ABORT: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Resolve the brief → {team, assignee, status}
# ---------------------------------------------------------------------------
log "resolving brief '${BRIEF_ID}'"
BRIEF_JSON="$(cd "${TOOLING_ROOT}" && python3 -c "
import json, sys
sys.path.insert(0, '.')
from lib import brief_substrate
b = brief_substrate.get_brief('${BRIEF_ID}')
if not b:
    print('{}'); sys.exit(0)
print(json.dumps({
    'team': b.get('team', ''),
    'assignee': b.get('assignee', ''),
    'status': b.get('status', ''),
}))
")" || abort "brief lookup failed (Qdrant unreachable / PODZONE_QDRANT_APIKEY not set)"

TEAM="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('team',''))" "${BRIEF_JSON}")"
ASSIGNEE="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('assignee',''))" "${BRIEF_JSON}")"
BRIEF_STATUS_FIELD="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('status',''))" "${BRIEF_JSON}")"

[[ -n "${TEAM}" && -n "${ASSIGNEE}" ]] || abort "brief '${BRIEF_ID}' not found, or missing team/assignee — author + approve it first"
case "${BRIEF_STATUS_FIELD}" in
  approved|in_progress|complete) : ;;
  *) abort "brief '${BRIEF_ID}' status is '${BRIEF_STATUS_FIELD}' — must be approved before launch" ;;
esac

AGENT_CAP="$(python3 -c "print('${ASSIGNEE}'.capitalize())")"
# brief_id is `{team}/{date}-{slug}` — its last path segment is ALREADY
# date-prefixed, so the branch name is `{agent}/{that segment}` verbatim
# (not `{agent}/{today}-{that segment}`, which would double the date).
SLUG="$(python3 -c "
s = '${BRIEF_ID}'.split('/')[-1]
print(s)
")"
BRANCH_NAME="${ASSIGNEE}/${SLUG}"

if [[ -z "${HOME_REPO_DIR}" ]]; then
  HOME_REPO_DIR="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi
[[ -n "${HOME_REPO_DIR}" && -d "${HOME_REPO_DIR}/.git" ]] || abort "could not resolve home repo — pass --home-repo <path>"
WORKSPACE_DIR="${HOME_REPO_DIR}/.workspace"
mkdir -p "${WORKSPACE_DIR}"

IFS=',' read -r -a REPOS <<< "${REPOS_CSV}"
# Drop empty entries from an empty --repos.
FILTERED_REPOS=()
for r in "${REPOS[@]:-}"; do
  [[ -n "${r}" ]] && FILTERED_REPOS+=("${r}")
done
REPOS=("${FILTERED_REPOS[@]:-}")

log "brief resolved: team=${TEAM} assignee=${ASSIGNEE} home_repo=${HOME_REPO_DIR} repos=[${REPOS_CSV}]"

# ---------------------------------------------------------------------------
# Phase 1 — stage the repos (preflight-abort, pull, branch+PR pre-creation, lock)
# ---------------------------------------------------------------------------

repo_dir_for() { # $1 = repo name
  echo "${WORKSPACE_DIR}/$1"
}

# Preflight-abort (proposal §3.2 / brief item 2): any dirty/unpushed/untracked
# state in the home repo OR any named working repo aborts the WHOLE launch —
# no Claude run, no cleanup attempt. An absent working-repo clone is not dirt
# (it will be cloned fresh below) and does not abort.
log "preflight-abort: checking home repo"
PRE_HOME="$(python3 "${TOOLING_ROOT}/lib/session_guard.py" preflight --repo "${HOME_REPO_DIR}" 2>&1)" \
  || abort "home repo ${HOME_REPO_DIR} failed preflight (dirty / unpushed / off-main) — operator attention needed:
${PRE_HOME}"
log "home repo preflight ok"

for r in "${REPOS[@]:-}"; do
  [[ -n "${r}" ]] || continue
  d="$(repo_dir_for "${r}")"
  if [[ -d "${d}/.git" ]]; then
    log "preflight-abort: checking working repo ${r}"
    PRE_OUT="$(python3 "${TOOLING_ROOT}/lib/session_guard.py" preflight --repo "${d}" 2>&1)" \
      || abort "working repo ${r} (${d}) failed preflight (dirty / unpushed / off-main) — operator attention needed:
${PRE_OUT}"
  else
    log "working repo ${r} not yet cloned — will clone fresh (not a preflight condition)"
  fi
done
log "preflight-abort: all repos clear"

# Pull: home repo trunk lifecycle (checkout main + pull --ff-only); each named
# working repo cloned-or-pulled.
log "pulling home repo to a ff'd main"
git -C "${HOME_REPO_DIR}" checkout main >/dev/null 2>&1 || abort "home repo: could not checkout main"
git -C "${HOME_REPO_DIR}" pull --ff-only origin main || abort "home repo: pull --ff-only failed — diverged main, resolve before launching"

for r in "${REPOS[@]:-}"; do
  [[ -n "${r}" ]] || continue
  d="$(repo_dir_for "${r}")"
  if [[ -d "${d}/.git" ]]; then
    log "pulling working repo ${r}"
    git -C "${d}" checkout main >/dev/null 2>&1 || abort "${r}: could not checkout main"
    git -C "${d}" pull --ff-only origin main || abort "${r}: pull --ff-only failed — diverged main, resolve before launching"
  else
    log "cloning working repo ${r}"
    git clone --quiet "https://github.com/${ORG}/${r}.git" "${d}" || abort "clone of ${r} failed"
  fi
done

# Stage working repos: session branch + first push + initial/empty-shell PR,
# BEFORE Claude ever runs — so the inner session only ever pushes commits onto
# an existing branch/PR, and a limit-stop relaunch has nothing left to set up.
for r in "${REPOS[@]:-}"; do
  [[ -n "${r}" ]] || continue
  d="$(repo_dir_for "${r}")"
  log "staging working repo ${r} on branch ${BRANCH_NAME}"
  if git -C "${d}" rev-parse --verify "${BRANCH_NAME}" >/dev/null 2>&1; then
    git -C "${d}" checkout "${BRANCH_NAME}"
  else
    git -C "${d}" checkout -b "${BRANCH_NAME}"
  fi
  # Empty-shell commit: GitHub refuses a PR with no diff against base, and the
  # inner session should have nothing to set up itself.
  if [[ -z "$(git -C "${d}" log origin/main.."${BRANCH_NAME}" 2>/dev/null)" ]]; then
    git -C "${d}" commit --allow-empty -m "chore: open session branch for ${BRIEF_ID}" >/dev/null
  fi
  git -C "${d}" push -u origin "${BRANCH_NAME}" 2>&1 | sed 's/^/    /'
  EXISTING_PR="$(gh pr list --repo "${ORG}/${r}" --head "${BRANCH_NAME}" --json number --jq '.[0].number' 2>/dev/null || true)"
  if [[ -z "${EXISTING_PR}" ]]; then
    gh pr create --repo "${ORG}/${r}" --draft --base main --head "${BRANCH_NAME}" \
      --title "brief(${BRIEF_ID}): ${SLUG}" \
      --body "Auto-staged by launch.sh for brief \`${BRIEF_ID}\` (assignee: ${ASSIGNEE}). Empty shell PR pre-created before the session ran — commits land here as the wrapper pushes each loop-exit boundary." \
      >/dev/null || log "WARNING: gh pr create failed for ${r} — branch is pushed, PR needs manual creation"
  else
    log "PR #${EXISTING_PR} already open for ${r}@${BRANCH_NAME} — reusing"
  fi
done

# Write-capability gate (MANDATORY headless prep, PROJ-039/T-132 — fail loud).
# A headless `claude -p` has no interactive prompt to grant Write/Bash; without
# this, every dispatch through the wrapper would be silently denied by the
# auto-mode classifier, which defeats the whole point of this script.
log "checking headless write-capability gate on home repo"
python3 "${TOOLING_ROOT}/tools/ensure-local-settings.py" --check --repo "${HOME_REPO_DIR}" \
  || abort "home repo ${HOME_REPO_DIR} does not grant headless write capability — operator/Team Lead action required (see message above), not something this wrapper can fix in-session"

# Pre-resolved tokens gate (PROJ-039/T-210 — fail loud, same philosophy as the
# write-capability gate above). This script never calls secretctl itself; the
# Team Lead must have already run resolve-launch-tokens.py via the secrets MCP
# tool. A stale-but-present file is not re-validated here (freshness is the
# Team Lead's call, per the tool's own reusable-while-valid design) — only
# existence and shape.
log "checking pre-resolved tokens file"
TOKEN_COUNT="$(python3 -c "
import json, sys
try:
    d = json.load(open('${TOKENS_FILE}'))
except FileNotFoundError:
    sys.exit('missing')
except json.JSONDecodeError:
    sys.exit('malformed')
if not isinstance(d, list) or not d:
    sys.exit('empty')
print(len(d))
")" || abort "tokens file ${TOKENS_FILE} is ${TOKEN_COUNT:-missing} — run resolve-launch-tokens.py via the secrets MCP tool first (see tools/resolve-launch-tokens.py's own docstring), this wrapper cannot resolve secrets itself"
log "tokens file ok — ${TOKEN_COUNT} token(s) available"

# Lock (home repo), held across all retry attempts, released only at final exit.
log "acquiring session lock on home repo"
python3 "${TOOLING_ROOT}/lib/session_guard.py" lock --repo "${HOME_REPO_DIR}" --sid "${BRIEF_ID}" \
  || abort "home repo is locked by a live session — refuse to launch"

release_lock() {
  python3 "${TOOLING_ROOT}/lib/session_guard.py" unlock --repo "${HOME_REPO_DIR}" --sid "${BRIEF_ID}" >/dev/null 2>&1 || true
}
trap release_lock EXIT

# ---------------------------------------------------------------------------
# wrapper-owned git ceremony: commit + push every touched repo (home + each
# working repo). Called at every loop-exit point and at final cleanup — this
# is THE mechanism that removes the inner session's self-banking dependency.
# ---------------------------------------------------------------------------
ALL_REPO_DIRS=("${HOME_REPO_DIR}")
for r in "${REPOS[@]:-}"; do
  [[ -n "${r}" ]] && ALL_REPO_DIRS+=("$(repo_dir_for "${r}")")
done

bank_all_repos() { # $1 = attempt number
  local attempt="$1"
  local d
  for d in "${ALL_REPO_DIRS[@]}"; do
    [[ -d "${d}/.git" ]] || continue
    if [[ -n "$(git -C "${d}" status --porcelain)" ]]; then
      git -C "${d}" add -A
      git -C "${d}" commit -m "wip: ${BRIEF_ID} attempt ${attempt}" >/dev/null
      log "banked changes in $(basename "${d}") (attempt ${attempt})"
    fi
    git -C "${d}" push 2>&1 | sed 's/^/    /' || log "WARNING: push failed for $(basename "${d}") — will retry at next boundary"
  done
}

# ---------------------------------------------------------------------------
# Phase 2 — the run loop (proposal §3.3, classify_exit per §5)
# ---------------------------------------------------------------------------
if [[ -n "${TOKEN_INDEX_OVERRIDE}" ]]; then
  i="${TOKEN_INDEX_OVERRIDE}"
  log "starting token index ${i} (explicit --token-index override, cursor untouched)"
else
  i="$(cat "${CURSOR_FILE}" 2>/dev/null || echo 0)"
  [[ "${i}" =~ ^[0-9]+$ ]] || i=0
  i=$(( i % TOKEN_COUNT ))
  log "starting token index ${i} (rotation cursor)"
  echo "$(( (i + 1) % TOKEN_COUNT ))" > "${CURSOR_FILE}"
fi
attempt=0
api_retries=0
LAST_STDOUT=""

while true; do
  attempt=$((attempt + 1))
  if [[ ${i} -ge ${TOKEN_COUNT} ]]; then
    log "all ${TOKEN_COUNT} resolved subscription(s) exhausted — exiting"
    echo "${LAST_STDOUT}"
    exit 3
  fi
  token_name="$(python3 -c "import json; print(json.load(open('${TOKENS_FILE}'))[${i}]['name'])")"
  log "attempt ${attempt}: launching with subscription '${token_name}' (index ${i})"

  # Read by INDEX from the pre-resolved file (PROJ-039/T-210) — no secretctl
  # call in this script at all. The value briefly exists in this subshell's
  # own environment for the `claude` child process only, the same exposure
  # surface any env-var secret injection already carries; never echoed.
  set +e
  LAST_STDOUT="$(cd "${HOME_REPO_DIR}" && BRIEF_ID="${BRIEF_ID}" CLAUDE_CODE_OAUTH_TOKEN="$(python3 -c "import json; print(json.load(open('${TOKENS_FILE}'))[${i}]['token'])")" \
    claude -p "Hi ${AGENT_CAP}. Continue with the brief." 2>&1)"
  EXIT_CODE=$?
  set -e

  echo "${LAST_STDOUT}" | tail -20 | sed 's/^/    /'

  bank_all_repos "${attempt}"

  CLASS="$(cd "${TOOLING_ROOT}" && python3 -c "
import sys
sys.path.insert(0, '.')
from lib.classify_exit import classify_exit
print(classify_exit(sys.stdin.read(), exit_code=${EXIT_CODE}))
" <<< "${LAST_STDOUT}")"
  log "classify_exit → ${CLASS}"

  case "${CLASS}" in
    session_limit)
      i=$((i + 1))
      if [[ ${i} -ge ${TOKEN_COUNT} ]]; then
        log "all ${TOKEN_COUNT} resolved subscription(s) limited — exiting"
        echo "${LAST_STDOUT}"
        exit 3
      fi
      api_retries=0
      continue
      ;;
    api_error)
      api_retries=$((api_retries + 1))
      if [[ ${api_retries} -gt ${API_RETRY_CAP} ]]; then
        log "api-retry cap (${API_RETRY_CAP}) exceeded on token '${token_name}' — falling through to other-failure"
        log "OTHER FAILURE — exiting non-zero"
        echo "${LAST_STDOUT}"
        exit 1
      fi
      log "API error — retrying same token in ${API_RETRY_BACKOFF_S}s (attempt ${api_retries}/${API_RETRY_CAP})"
      sleep "${API_RETRY_BACKOFF_S}"
      continue
      ;;
    complete)
      log "COMPLETE — exiting 0"
      git -C "${HOME_REPO_DIR}" checkout main >/dev/null 2>&1 || true
      exit 0
      ;;
    other|*)
      log "OTHER FAILURE — exiting non-zero"
      echo "${LAST_STDOUT}"
      exit 1
      ;;
  esac
done
