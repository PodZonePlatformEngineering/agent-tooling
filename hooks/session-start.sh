#!/usr/bin/env bash
# SessionStart hook — upsert session baseline into claude_session_telemetry.
# Point ID = session_id (stable anchor for this session's chain of events).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIMITIVES="${SCRIPT_DIR}/../primitives"
COLLECTION="claude_session_telemetry"

# Best-effort runtime logging (PROJ-039/T-029). Sourcing failure must not abort.
# shellcheck source=/dev/null
source "${PRIMITIVES}/log_primitive.sh" 2>/dev/null || true
command -v log_primitive >/dev/null 2>&1 || log_primitive() { :; }

STDIN="$(cat)"

# Unfinalised-session guard (PROJ-039/T-030): before doing anything else, recover
# any prior session whose SessionEnd finalise was truncated (killed / timed-out
# mid-sequence). Idempotent + best-effort — never blocks startup.
log_primitive "session-start.sh" "running unfinalised-session guard"
# Guard writes only to stderr + logs/libraries.log; nothing on stdout reaches the
# session context. Best-effort: a guard failure must never block SessionStart.
python3 "${SCRIPT_DIR}/session-end-finalise.py" --guard </dev/null || true

SESSION_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['session_id'])" "${STDIN}")"
CWD="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('cwd','.'))" "${STDIN}")"
# Sid-keyed committed logs (PROJ-039/T-048): every primitive this hook (and its
# children) invokes logs to logs/primitives-{sid8}.log via this export.
export PODZONE_SESSION_ID="${SESSION_ID}"

# Trunk-mode SessionStart pull (PROJ-039/T-084, plan D-2): unconditional
# `git -C {repo} pull --ff-only` for any repo flagged `lifecycle_mode: trunk` in
# its shipped .claude/tooling-manifest.json — BEFORE session-materialise.py runs
# (next in the SessionStart hook chain, scaffold.sh's wiring order). Absent/unset
# flag reads "branch" (lib/lifecycle_mode.py's default) and this is a pure no-op —
# every unmigrated repo is unaffected. Best-effort: a pull failure (offline,
# diverged local main) must not block startup; it's logged and the session
# proceeds against whatever main the clone already has.
LIFECYCLE_MODE="$(python3 -c "
import json, sys
try:
    d = json.load(open(sys.argv[1] + '/.claude/tooling-manifest.json', encoding='utf-8'))
    print(d.get('lifecycle_mode') or 'branch')
except Exception:
    print('branch')
" "${CWD}")"
if [[ "${LIFECYCLE_MODE}" == "trunk" ]]; then
  if git -C "${CWD}" pull --ff-only origin main >/dev/null 2>&1; then
    log_primitive "session-start.sh" "trunk-mode: pulled --ff-only origin main"
  else
    log_primitive "session-start.sh" "trunk-mode: pull --ff-only failed or no-op (offline / diverged local main?)"
  fi
fi

# Silent-failure hardening (PROJ-039/T-052): a brief-first launch REQUIRES the resident
# session-materialise.py to stand up the session point. If BRIEF_ID is set but that hook
# file is missing (a botched sync — the exact Thoth T-022 failure, where the SessionStart
# wiring pointed at a missing file and skipped silently: no session point, session_ids[]
# never appended, finalise ran "not brief-first"), do NOT let work start against an
# unlinked brief. session-start.sh is always resident, so it is the guard: write an
# ok:false materialise sentinel (the orientation ritual refuses on it) and HALT loudly.
if [[ -n "${BRIEF_ID:-}" && ! -f "${SCRIPT_DIR}/session-materialise.py" ]]; then
  WS="${CWD}/.workspace"
  mkdir -p "${WS}" 2>/dev/null || true
  printf '{"ok": false, "reason": "materialise-hook-missing", "brief_id": "%s"}\n' \
    "${BRIEF_ID}" > "${WS}/.materialise-status.json" 2>/dev/null || true
  HALT_MSG="⛔ session-materialise.py MISSING but BRIEF_ID=${BRIEF_ID} is set — the brief-first \
session cannot materialise. HALT: this hook set is incompletely synced \
(session-materialise.py must be resident). Do NOT begin tasking; re-sync and re-launch."
  echo "${HALT_MSG}" >&2
  # stdout is what a SessionStart hook injects into the AGENT'S context (stderr only
  # reaches the operator) — the halt must land in front of the agent, not just the log.
  echo "${HALT_MSG}"
  log_primitive "session-start.sh" "HALT: session-materialise.py missing for brief-first launch (BRIEF_ID=${BRIEF_ID})"
fi

# Silent-stale hardening (PROJ-039/T-069, T-065 F1/F12) — symmetrical to the T-052
# materialise guard above. TOOLING_UPDATE set means this launch REQUESTED a tooling
# self-update, and update-tooling.py — wired as the FIRST SessionStart command — must
# have already run THIS startup and written its outcome sentinel keyed to this
# session_id. No sentinel (wiring absent / tool file missing / hook killed) or
# ok:false (refusal) → HALT loudly. Proceeding on stale tooling while the brief's
# audit field claims the new version was delivered is the exact silent-no-op class
# the T-065 shakedown caught live. The short poll only covers a platform that runs
# SessionStart commands concurrently instead of in listed order; in the sequential
# case the sentinel is already on disk and the first probe returns.
if [[ -n "${TOOLING_UPDATE:-}" ]]; then
  TU_SENTINEL="${CWD}/.workspace/.tooling-update-status.json"
  TU_STATE="missing"
  TU_REASON=""
  for _ in $(seq 1 "${TOOLING_GUARD_WAIT_SECS:-20}"); do
    TU_PROBE="$(python3 - "${TU_SENTINEL}" "${SESSION_ID}" <<'PYEOF' 2>/dev/null || echo missing
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("missing"); raise SystemExit
if str(d.get("session_id") or "") != sys.argv[2]:
    print("missing")  # stale sentinel from an earlier startup / manual run
elif d.get("ok"):
    print("ok")
else:
    print("refused\t" + str(d.get("reason") or ""))
PYEOF
)"
    TU_STATE="${TU_PROBE%%$'\t'*}"
    TU_REASON="${TU_PROBE#*$'\t'}"
    [[ "${TU_STATE}" == "missing" ]] || break
    sleep 1
  done
  if [[ "${TU_STATE}" != "ok" ]]; then
    if [[ "${TU_STATE}" == "missing" ]]; then
      mkdir -p "${CWD}/.workspace" 2>/dev/null || true
      python3 - "${TU_SENTINEL}" "${SESSION_ID}" "${TOOLING_UPDATE}" <<'PYEOF' 2>/dev/null || true
import json, sys
json.dump({"ok": False, "reason": "updater-never-ran: TOOLING_UPDATE set but no sentinel this startup",
           "requested": sys.argv[3], "session_id": sys.argv[2]},
          open(sys.argv[1], "w", encoding="utf-8"), indent=2, sort_keys=True)
PYEOF
      TU_REASON="update-tooling.py never ran this startup (wiring absent, tool file missing, or hook killed)"
    fi
    HALT_MSG="⛔ TOOLING_UPDATE=${TOOLING_UPDATE} was requested but the tooling self-update DID NOT COMPLETE: \
${TU_REASON}. HALT: do NOT begin tasking on stale tooling — the brief's audit field claims this \
version was delivered. Fix the update path (see .workspace/.tooling-update-status.json) and re-launch."
    echo "${HALT_MSG}" >&2
    # stdout lands in the agent's context (stderr only reaches the operator).
    echo "${HALT_MSG}"
    log_primitive "session-start.sh" "HALT: TOOLING_UPDATE=${TOOLING_UPDATE} set but updater state=${TU_STATE} (${TU_REASON})"
  fi
fi
TRANSCRIPT_PATH="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('transcript_path',''))" "${STDIN}")"
TIMESTAMP="$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())")"
REPO_NAME="$(basename "${CWD}")"
GIT_BRANCH="$(git -C "${CWD}" branch --show-current 2>/dev/null || echo 'unknown')"
COMMIT_SHA="$(git -C "${CWD}" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
OS_USER="${USER:-$(id -un)}"
PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')"

# Session telemetry is best-effort and MUST NOT wall the session (PROJ-011/T-025 R-13):
# an unconfigured workstation (no PODZONE_QDRANT_APIKEY) degrades to "no telemetry
# recorded" rather than a session-blocking hook error. The trainee preflight hook
# owns the single "not configured yet — see the setup guide" pointer; this hook
# stays silent on skip so it is never a wall of errors for any role.
#
# Payload-only (PROJ-041/T-002): hooks never embed — an EMPTY named-vector map
# (an omitted `vector` field is a Qdrant 400); the PROJ-042 enrichment job
# embeds in retrospect.
VECTOR_JSON="{}"

PAYLOAD="$(python3 -c "
import json, sys
print(json.dumps({
  'event_type': 'SessionStart',
  'session_id': sys.argv[1],
  'timestamp': sys.argv[2],
  'repository': {'name': sys.argv[3], 'git_branch': sys.argv[4], 'commit_sha': sys.argv[5]},
  'environment': {'user': sys.argv[6], 'platform': sys.argv[7]},
  'transcript_path': sys.argv[8],
}))" "${SESSION_ID}" "${TIMESTAMP}" "${REPO_NAME}" "${GIT_BRANCH}" "${COMMIT_SHA}" "${OS_USER}" "${PLATFORM}" "${TRANSCRIPT_PATH}")"

"${PRIMITIVES}/qdrant/add-qdrant-point.sh" "${COLLECTION}" "${SESSION_ID}" "${VECTOR_JSON}" "${PAYLOAD}" >/dev/null 2>&1 \
  || log_primitive "session-start.sh" "telemetry upsert skipped (substrate unreachable / unconfigured)"
exit 0
