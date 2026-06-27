---
name: launch-session
description: Launch an isolated concurrent agent session — VS Code worktree (legacy standard/fissioned) or standalone-terminal materialise flow (migrated home-repo) (Team Lead only)
---

This skill is for **the Team Lead** of each team. It creates an isolated working environment
for an agent session and registers it so the Team Lead can track it during consolidation.

It has **two launch shapes**, selected per-agent by mode (see Step 1):

- **Legacy (standard / fissioned)** — generates a VS Code `.code-workspace`, opens it in a
  new VS Code window, and resolves the agent's `identity_file` there. The engineer switches
  to the new window, runs the agent session, and returns to the planning window when
  convenient. Both sessions run concurrently without sharing repo state.
- **Migrated home-repo** — the unified-substrate flow proven across PROJ-039 C2a→C2-v2.1c.
  No VS Code window: the Team Lead pins a `--session-id`, authors the brief as a
  `session_substrate` **session point** keyed to it, creates worktrees, wires the
  SessionStart materialise + SessionEnd finalise hooks, and emits a **standalone-terminal**
  launch command (`cd {worktree} && claude --session-id {uuid}`). The agent's `.workspace/`
  is **materialised from Qdrant** at SessionStart — there is no `.code-workspace`.

The two paths **coexist** during the C2 migration window — an agent is migrated iff its row
in the registry says so (Step 1).

## Prerequisites

- All required repos already cloned under `~/workspace/`
- `git worktree` supported (standard Git ≥ 2.5)
- The relevant home repo's `main` branch must be clean before launching
- **Legacy only:** VS Code CLI at
  `/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code`
- **Migrated only:**
  - `uuidgen` (pin the `--session-id`) and `python3`
  - `PODZONE_QDRANT_APIKEY` reachable — via the secrets MCP
    (`mcp__secrets__secret_run -k podzone_qdrant_apikey -- …`) or already in the env block
  - Ollama running locally (the `nomic-embed-text` embed for the brief vector; token-safe
    since PROJ-039/T-027)
  - `agent-tooling` cloned at `~/workspace/agent-tooling` (source of `create-session-point.py`
    + `session-materialise.py`)

## Inputs (ask Martin if not provided)

- **agent** — e.g. `hephaestus`, `atlas`, `thoth`
- **task-slug** — short identifier for the session work, e.g. `dev-functional-testing`
- **repos** — which repos the session needs (default: from the agent's identity file)

## Step 1 — Resolve agent identity and repos

Read the agent's identity YAML. Search in this order:
1. `podzoneAgentTeam/workspaces/identity/{agent}.identity.yaml`
2. `podzoneAgentTeam/workspaces/identity/martin-{agent}-*.identity.yaml`
3. `{fissioned_team}/workspaces/identity/{agent}.identity.yaml` (if a fissioned team repo
   is present in the workspace and the agent belongs to it)

Extract:
- `home_repo` — determines standard vs. fissioned vs. migrated mode (see below)
- `repos` list — all repos in scope for this agent
- `workspace` — canonical workspace filename (legacy modes only)
- canonical workspace file: `{home_repo_path}/workspaces/{workspace}.code-workspace`

Filter `repos` to those actually needed for this session's task (ask Martin if unclear;
default to all repos in the identity file).

**Mode determination:**
- If `home_repo` matches `home-*` (e.g. `home-podzone-hephaestus`) → **migrated home-repo mode**
  (PROJ-039/T-011 C2, T-006) — see the migrated note in Step 2 and the migrated subsections below.
- Else if `home_repo == podzoneAgentTeam` → **standard mode**
- Else (`trainingTeam` / `roadmapTeam`) → **fissioned team mode**

The migrated set is authoritative in
`planning/projects/PROJ-032-agent-home-repos/migrated-agents.md`. Agents not listed
there are unaffected by C2 — they keep standard / fissioned mode exactly as before.
When operating on the current session's own agent you may detect migrated directly from
`home_repo` matching `home-*` (equivalent to registry membership, no file read needed).

### Migrated home-repo mode — the ritual at a glance

The migrated launch codifies what was hand-assembled each session across C2a→C2-v2.1c.
The shared Steps 1–2 run first, then the migrated-only steps:

1. **Detect migrated** (this Step) → branch behaviour.
2. **Pre-flight brief check** on the home repo's `origin/main` (Step 2).
3. **Pin a pre-generated `--session-id`** (Step 4) and persist it.
4. **Author the brief as a `session_substrate` session point** keyed to the pinned id
   (Step 5); verify read-back.
5. **Create the home-repo worktree** under `~/sessions/{session-id}/` (the launch cwd) +
   task-repo worktrees (Step 3, migrated subsection; Step 3a still applies).
6. **Wire SessionStart materialise + SessionEnd finalise — conditionally** (Step 6,
   migrated subsection): a no-op once the materialise hook is resident (C4).
7. **Register** the session in the apex `active.md` (Step 7).
8. **Emit a standalone-terminal launch command** + clean-`/exit` reminder (Step 8); then
   optionally **verify the materialise resolves** before handing off (Step 9).

(Steps below are ordered for execution: shared 1–2, worktrees 3, then migrated 4→5→6→7,
launch 8, verify 9. Pin-then-author-then-worktree is also valid — the only hard ordering
is *pin the id before authoring the point*, since the point is keyed by it.)

## Step 2 — Pre-flight brief check

Before creating any worktrees, confirm a commission brief for this session exists on
`origin/main` of the home repo. This prevents agents from starting without proper context
(e.g. when the brief is on an unmerged Team Lead branch).

**Home repo for the check:**
- Standard mode: `podzoneAgentTeam`
- Fissioned mode: the agent's `home_repo`
- **Migrated home-repo mode (T-006):** the agent's `home_repo` (`home-<team>-<agent>`).
  The Team Lead routes the brief by committing it to a **`team-lead`** branch on the home
  repo and raising a PR to the home repo's `main` (not `podzoneAgentTeam/team/{agent}/incoming/`).
  The pre-flight check confirms the brief is merged to the home repo's `origin/main` before
  launch; the SessionStart materialise hook then materialises `.workspace/` from Qdrant
  `session_substrate` (the committed file is the human-/PR-visible record).
  Committed brief path on the migrated home repo: `team-lead/briefs/{date}-{task-slug}.md`.

  > ✅ **Resolved (operator-DECIDED 2026-06-17, PROJ-039/T-011 C2-v2.1):** the canonical
  > committed brief path for a migrated agent is **`team-lead/briefs/{date}-{task-slug}.md`
  > on the agent home repo** (not `podzoneAgentTeam/team/{agent}/incoming/`). The Team Lead
  > PRs the brief there; the migrated-mode pre-flight check below and template §11 are
  > aligned to this path. (`session_substrate` in Qdrant is the runtime source the
  > materialise hook reads; the committed file is the human-/PR-visible record.)

**Brief directory by mode** (call it `{brief-dir}` below):
- Standard / fissioned mode: `team/{agent}/incoming/`
- **Migrated home-repo mode (T-006):** `team-lead/briefs/`

**Check procedure:**

1. List the committed briefs on `origin/main`:
   ```bash
   git -C ~/workspace/{home_repo} show origin/main:{brief-dir}
   ```
2. Scan the listing for a filename matching `*{task-slug}*` (date prefix may vary).
3. **If a matching file exists:** push `origin/main` so the worktree branches from the
   current state, then proceed to Step 3:
   ```bash
   git -C ~/workspace/{home_repo} push origin main
   ```
   Push ensures origin/main is current before the worktree branches, preventing PR diff
   noise from unpushed Team Lead commits.
4. **If no match is found:** abort with:
   ```
   Commission brief not in main — merge the Team Lead session branch first.
   Expected: {brief-dir}{date}-{task-slug}.md
   Check: git show origin/main:{brief-dir}
   ```
   Do not create worktrees, workspace files, or session registry entries.

## Step 3 — Create session directory and worktrees

Session ID format: `{agent}-{YYYY-MM-DD}-{task-slug}`
Session directory: `~/sessions/{session-id}/`

```bash
mkdir -p ~/sessions/{session-id}
```

> **Migrated note:** this human-readable `{session-id}` names the **session directory** only.
> The runtime `--session-id` is a separate **pinned UUID** (Step 4) — the two are distinct.

### Step 3a — Fast-forward local mains before branching

`git worktree add ... -b {branch}` branches from the **local** clone's `main`, not
from origin. If a prior session's PRs merged on GitHub since the last local pull, the
local `main` is stale and the new worktree silently starts **without** that merged
code — the failure mode behind memory `launch-pull-origin-before-worktree` (re-hit
2026-06-14: a PROJ-039 Phase B worktree branched from a main missing the just-merged
Phase A PRs the task depended on).

Step 2 only pushes the **home repo's** origin. It does not refresh the **task repos'**
local mains. So before creating any worktree, fast-forward the local `main` of every
repo this session will branch from — task repos **and** the home/PAT repo:

```bash
# For each repo in scope (task repos from the identity `repos` list + the home repo):
git -C ~/workspace/{repo} fetch origin
git -C ~/workspace/{repo} checkout main
git -C ~/workspace/{repo} merge --ff-only origin/main
```

- Run this for **each** distinct repo before its `worktree add` below.
- `--ff-only` is deliberate: it refuses to merge if local `main` has diverged
  (unpushed local commits) rather than creating a merge commit. If it fails, **stop
  and surface to Martin** — a diverged task-repo main is an anomaly to resolve before
  launching, not to paper over.
- A repo whose main is already current is a no-op ("Already up to date.").
- `.DS_Store`-only dirt does not block `checkout main`; any other uncommitted change in
  the local clone should be surfaced before proceeding.

### Standard mode (home_repo == podzoneAgentTeam)

#### Task repo worktrees

For each required task repo (excluding podzoneAgentTeam itself):

```bash
git -C ~/workspace/{repo-path} worktree add \
  ~/sessions/{session-id}/{repo-name} \
  -b {agent}/{YYYY-MM-DD}-{task-slug}
```

Branch naming: `{agent}/{YYYY-MM-DD}-{task-slug}` — session-scoped, collision-free.
If a branch with that name already exists (retry same task): append `-2`, `-3`, etc.

#### podzoneAgentTeam PAT worktree

Always create a podzoneAgentTeam worktree — agents write their outbox and memory files
here on a session branch, then raise a PR to main at session end.

Branch naming: `session/{agent}-{YYYY-MM-DD}-{task-slug}`

```bash
git -C ~/workspace/podzoneAgentTeam worktree add \
  ~/sessions/{session-id}/podzoneAgentTeam \
  -b session/{agent}-{YYYY-MM-DD}-{task-slug}
```

### Fissioned team mode (home_repo != podzoneAgentTeam)

Fissioned agents do **not** get a podzoneAgentTeam session branch. Their PAT branch
lives in their own home repo.

#### Fissioned task repo worktrees

For each repo in the identity `repos` list **except** podzoneAgentTeam:

```bash
git -C ~/workspace/{repo-name} worktree add \
  ~/sessions/{session-id}/{repo-name} \
  -b {agent}/{YYYY-MM-DD}-{task-slug}
```

For the `home_repo` (e.g. `trainingTeam`), create the PAT branch instead:

```bash
git -C ~/workspace/{home_repo} worktree add \
  ~/sessions/{session-id}/{home_repo} \
  -b session/{agent}-{YYYY-MM-DD}-{task-slug}
```

#### podzoneAgentTeam — plain clone reference (no worktree)

If podzoneAgentTeam is listed in the agent's repos (for read-only skills/ops doc access),
reference it as a plain folder — do not create a worktree or session branch:

```
podzoneAgentTeam path: ~/workspace/podzoneAgentTeam  (plain clone, read-only)
```

### Migrated home-repo mode (home_repo matches home-*)

The **home repo worktree is the launch cwd** — the agent runs `claude` from inside it,
and the SessionStart materialise hook writes `.workspace/` there. Create it on the PAT
session branch:

```bash
git -C ~/workspace/{home_repo} worktree add \
  ~/sessions/{session-id}/{home_repo} \
  -b session/{agent}-{YYYY-MM-DD}-{task-slug}
```

For each task repo in the identity `repos` list (excluding the home repo and excluding
podzoneAgentTeam) create a task worktree:

```bash
git -C ~/workspace/{repo-name} worktree add \
  ~/sessions/{session-id}/{repo-name} \
  -b {agent}/{YYYY-MM-DD}-{task-slug}
```

If podzoneAgentTeam is in scope, decide **read-only vs. write-target** for this session:

- **Read-only** (the common case — skills/ops-doc/identity access only): reference the
  plain clone — no worktree, no session branch:

  ```
  podzoneAgentTeam path: ~/workspace/podzoneAgentTeam  (plain clone, read-only)
  ```

- **Write-target** (the session must commit apex planning artefacts to podzoneAgentTeam —
  e.g. registry flips in `planning/projects/PROJ-032-agent-home-repos/migrated-agents.md`,
  reconciliation reports, or an apex outbox): create a **proper podzoneAgentTeam worktree**
  under the session directory on a `session/…` branch. **NEVER** branch the shared primary
  clone `~/workspace/podzoneAgentTeam` — doing so leaves the apex clone off `main` and trips
  the next Hermes consolidation push (the PROJ-039/T-031 / C2b defect: a `session/…` branch
  was checked out in the primary clone and PR'd from there, #102).

  ```bash
  git -C ~/workspace/podzoneAgentTeam worktree add \
    ~/sessions/{session-id}/podzoneAgentTeam \
    -b session/{agent}-{YYYY-MM-DD}-{task-slug}
  ```

  The session writes apex artefacts in `~/sessions/{session-id}/podzoneAgentTeam` and PRs
  from that branch to podzoneAgentTeam `main`. `git worktree add` does not move the primary
  clone's HEAD, so the apex clone stays on `main`.

#### Apex-clone-on-main guard (migrated write-target)

Whenever a migrated session takes podzoneAgentTeam as a write-target, assert the shared
primary clone is on `main` **before** creating the worktree and **after** it (and again at
consolidation). This catches an accidental primary-clone branching before it propagates:

```bash
APEX_BRANCH=$(git -C ~/workspace/podzoneAgentTeam rev-parse --abbrev-ref HEAD)
if [ "$APEX_BRANCH" != "main" ]; then
  echo "ABORT: ~/workspace/podzoneAgentTeam is on '$APEX_BRANCH', expected 'main'." >&2
  echo "The apex clone must stay on main; session work belongs in a worktree under" >&2
  echo "~/sessions/{session-id}/podzoneAgentTeam. Restore with:" >&2
  echo "  git -C ~/workspace/podzoneAgentTeam checkout main" >&2
  exit 1
fi
```

(`/consolidate-tasks` should run the same guard before its apex push — see that skill.)

**Note — git remote config is not worktree-isolated:** `git remote set-url` modifies
`.git/config` which is shared across all worktrees of the same repo. URL updates persist
to the main clone — this is intentional. Only file-level changes are branch-isolated.

## Step 4 — Pin the session-id (migrated home-repo mode only)

> Legacy (standard / fissioned) modes: **skip** — there is no pinned id; jump to Step 6.

Materialise resolves the brief point by the **runtime `session_id`** — the point id is
`uuid5(NAMESPACE_DNS, session_id)`. The Team Lead authors the brief point *before* the
session exists, so the runtime id must be **pinned in advance** and passed at launch with
`claude --session-id <uuid>`. A VS Code sidebar auto-generated id would NOT match the point
(finding #1, `agent-tooling/docs/proj039-substrate-wiring.md`) — which is exactly why
migrated launches use a standalone terminal with an explicit id.

Generate the UUID, surface it, and persist it next to the session directory:

```bash
SID=$(uuidgen | tr 'A-Z' 'a-z')
echo "$SID" > ~/sessions/{session-id}/.pinned-session-id
echo "pinned --session-id: $SID"
```

Carry `$SID` (the pinned UUID) through Steps 5 and 8. `.pinned-session-id` is the durable
record if you need to recover it later (e.g. for a manual finalise).

## Step 5 — Author the substrate brief point (migrated home-repo mode only)

> Legacy modes: **skip.**

Create the canonical `session_substrate` **session point** carrying the committed brief,
keyed to the pinned UUID, so SessionStart materialise can resolve it. Use the committed
brief file confirmed in Step 2 (`team-lead/briefs/{date}-{task-slug}.md` on the home repo).
The embed input is token-safe since T-027 (the full brief is always stored in the payload;
only the embed input is head-truncated):

```bash
mcp__secrets__secret_run -k podzone_qdrant_apikey -- \
  python3 ~/workspace/agent-tooling/tools/create-session-point.py \
    --session-id "$SID" \
    --agent {agent} \
    --work-item PROJ-XXX/T-YYY \
    --brief-file ~/workspace/{home_repo}/team-lead/briefs/{date}-{task-slug}.md
```

(If `PODZONE_QDRANT_APIKEY` is already in the env you can drop the `secret_run` wrapper.)

**Verify read-back** before proceeding — confirm the point exists and carries the brief:

```bash
mcp__secrets__secret_run -k podzone_qdrant_apikey -- \
  python3 -c "import sys; sys.path.insert(0,'$HOME/workspace/agent-tooling'); \
from lib import session_substrate as s; \
p=s.get_session_point('$SID'); \
print('ok' if p and p.get('brief',{}).get('text') else 'MISSING')"
```

If this prints anything other than `ok`, **stop** — the agent would HALT at SessionStart
(materialise refuses to fabricate `.workspace/` from a stale team-repo read, SD-3-002).
Re-check Qdrant reachability / API key / Ollama embed and re-run Step 5.

## Step 6 — Generate session workspace file / wire materialise (mode-split)

### Standard / fissioned mode — generate session workspace file

**Standard mode:** Copy from `podzoneAgentTeam/workspaces/{workspace}.code-workspace`.

**Fissioned team mode:** Copy from `{home_repo_path}/workspaces/{workspace}.code-workspace`
(e.g. `~/workspace/trainingTeam/workspaces/athena.code-workspace`).

Write to: `~/sessions/{session-id}/session.code-workspace`

Rewrite all `path` entries to point to session-local worktree paths:
- `gitopsapi` → `~/sessions/{session-id}/gitopsapi`
- `podzoneAgentTeam` → `~/sessions/{session-id}/podzoneAgentTeam` (standard mode)
- `podzoneAgentTeam` → `~/workspace/podzoneAgentTeam` (fissioned mode — plain clone path)
- `trainingTeam` → `~/sessions/{session-id}/trainingTeam`
- etc.

Rewrite `identity_file:` in `claude.projectInstructions` to resolve against the
session-local worktree path of the home repo:
- Standard: `podzoneAgentTeam/workspaces/identity/{agent}.identity.yaml`
  → `~/sessions/{session-id}/podzoneAgentTeam/workspaces/identity/{agent}.identity.yaml`
- Fissioned: `{home_repo}/workspaces/identity/{agent}.identity.yaml`
  → `~/sessions/{session-id}/{home_repo}/workspaces/identity/{agent}.identity.yaml`

### Migrated home-repo mode — wire SessionStart materialise + SessionEnd finalise (conditional)

No `.code-workspace` is generated — the agent's `.workspace/` is materialised from Qdrant
at SessionStart. Two hooks must be active in the home-repo worktree
(`~/sessions/{session-id}/{home_repo}`):

- **SessionEnd finalise** (`session-end-finalise.py`) — **resident** in migrated home
  repos since C2-v2.1c (committed `.claude/settings.json`). Confirm it is wired; do not
  re-add it.
- **SessionStart materialise** (`session-materialise.py`) — **not yet resident** (lands at
  C4). Until then the skill wires it via the gitignored `.claude/settings.local.json`.

**Make the materialise wiring conditional — detect first, never double-wire:**

```bash
WT=~/sessions/{session-id}/{home_repo}

# Already resident? (C4 committed it into settings.json) → no-op.
if grep -q 'session-materialise.py' "$WT/.claude/settings.json" 2>/dev/null; then
  echo "materialise hook is resident — no local wiring needed (C4 reached)"
else
  # 1) Copy the hook into the worktree's .claude/hooks/
  cp ~/workspace/agent-tooling/hooks/session-materialise.py "$WT/.claude/hooks/"
  chmod +x "$WT/.claude/hooks/session-materialise.py"

  # 2) Exclude the copied hook from git (per-worktree exclude is shared via the main clone).
  #    Use `--git-path info/exclude`, NOT `--git-dir`/info/exclude: in a worktree the latter
  #    resolves to `.git/worktrees/<name>/info/exclude` (no `info/` subdir there) → "No such
  #    file or directory", which aborts the launch under set -e. `--git-path info/exclude`
  #    resolves to the shared common `…/.git/info/exclude` in every worktree (PROJ-039/T-031).
  EXCLUDE=$(git -C "$WT" rev-parse --git-path info/exclude)
  grep -qxF '.claude/hooks/session-materialise.py' "$EXCLUDE" 2>/dev/null \
    || echo '.claude/hooks/session-materialise.py' >> "$EXCLUDE"

  # 3) Emit the gitignored settings.local.json (SessionStart materialise alongside resident CST)
  cat > "$WT/.claude/settings.local.json" <<'JSON'
{
  "_comment": "Migrated-launch wiring. SessionStart materialise alongside resident CST session-start.sh; SessionEnd finalise resident (C2-v2.1c). Gitignored. Auth (PODZONE_QDRANT_APIKEY) rides in from the apex env block.",
  "hooks": {
    "SessionStart": [
      { "matcher": "startup|resume", "hooks": [ { "type": "command", "command": "python3 .claude/hooks/session-materialise.py" } ] }
    ]
  }
}
JSON
  echo "materialise wired (local) — settings.local.json + hook copy + git exclude"
fi
```

Notes:
- `.claude/settings.local.json` is already gitignored by the committed `.gitignore` in
  migrated home repos; the copied hook is excluded per-worktree (the exclude lives in the
  main clone's `.git/.../info/exclude`, shared across worktrees).
- `PODZONE_QDRANT_APIKEY` is **never** embedded here — it rides in from the apex env block
  (PROJ-033/T-016). Never put the key in a project/local settings file.
- Once C4 commits the materialise hook into the resident `settings.json`, the `grep` guard
  makes this whole step a no-op — the skill keeps working unchanged.

## Step 7 — Register the session

### Standard mode

Append a row to the **main clone** of `podzoneAgentTeam/planning/sessions/active.md`
(not the session worktree — this file is Team Lead-managed on main):

```markdown
| {session-id} | {agent} | {task-slug} | {YYYY-MM-DD} | in-flight | ~/sessions/{session-id} | session/{agent}-{YYYY-MM-DD}-{task-slug} |
```

### Fissioned team mode

1. Append a full row to `~/workspace/{home_repo}/planning/sessions/active.md`
   (the fissioned team's own session registry):

   ```markdown
   | {session-id} | {agent} | {task-slug} | {YYYY-MM-DD} | in-flight | ~/sessions/{session-id} | session/{agent}-{YYYY-MM-DD}-{task-slug} |
   ```

2. Also append a one-line summary to `~/workspace/podzoneAgentTeam/planning/sessions/active.md`
   for apex visibility (status column = `fissioned — see {home_repo}`):

   ```markdown
   | {session-id} | {agent} | {task-slug} | {YYYY-MM-DD} | fissioned — see {home_repo} | ~/sessions/{session-id} | — |
   ```

### Migrated home-repo mode

Append a row to the **main clone** of `podzoneAgentTeam/planning/sessions/active.md` (apex
registry — Team Lead-managed on main). Record the pinned UUID so the session is recoverable
for a manual finalise:

```markdown
| {session-id} | {agent} | {task-slug} | {YYYY-MM-DD} | in-flight (migrated; sid {pinned-uuid}) | ~/sessions/{session-id} | session/{agent}-{YYYY-MM-DD}-{task-slug} |
```

## Step 8 — Launch (mode-split)

### Standard / fissioned mode — launch VS Code window

```bash
"/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" \
  --new-window ~/sessions/{session-id}/session.code-workspace
```

### Migrated home-repo mode — emit standalone-terminal launch command

Do **not** open a VS Code window. Emit a command for Martin to run in a **standalone
terminal**, launching from the home-repo worktree (the cwd materialise writes into) with
the pinned id:

```bash
cd ~/sessions/{session-id}/{home_repo} && claude --session-id {pinned-uuid}
```

> ⚠️ **Finish with a clean `/exit`.** SessionEnd finalise (response + rollup + telemetry
> push + push-then-delete + brief-result PR) hangs off the SessionEnd hook; a clean `/exit`
> is what fires it reliably. Closing the terminal / killing the process can skip
> finalisation. If SessionEnd is missed, finalise manually:
> `echo '{"session_id":"{pinned-uuid}","transcript_path":"<path>"}' | python3 ~/sessions/{session-id}/{home_repo}/.claude/hooks/session-end-finalise.py`

## Step 9 — Verify the materialise resolves (migrated home-repo mode; recommended)

Before handing off, simulate the SessionStart hook against the worktree and assert the
sentinel reports success — this catches a mis-keyed point or an auth/embed gap *before* the
agent hits the HALT:

```bash
WT=~/sessions/{session-id}/{home_repo}
mcp__secrets__secret_run -k podzone_qdrant_apikey -- bash -c \
  "echo '{\"session_id\":\"{pinned-uuid}\",\"cwd\":\"$WT\",\"transcript_path\":\"\"}' \
   | python3 $WT/.claude/hooks/session-materialise.py >/dev/null; \
   python3 -c \"import json; d=json.load(open('$WT/.workspace/.materialise-status.json')); \
   print('materialise', 'OK' if d.get('ok') else 'FAILED', d.get('counts'))\""
```

Expect `materialise OK {'brief': 1, 'tasks': ...}`. If it reports `FAILED`, resolve before
launching (re-check Step 5 read-back, Qdrant reachability, API-key propagation, Ollama).
This is a dry run; the real launch in Step 8 re-materialises at SessionStart.

## Output

### Standard mode
```
Session launched: atlas-2026-04-06-shared-artifact-store

  Mode:             standard (home_repo: podzoneAgentTeam)
  Workspace:        ~/sessions/atlas-2026-04-06-shared-artifact-store/session.code-workspace
  Task branch:      atlas/2026-04-06-shared-artifact-store
  PAT branch:       session/atlas-2026-04-06-shared-artifact-store  (podzoneAgentTeam)
  Worktrees:        agentsonly-infra, agentsonly-apps, podzoneAgentTeam
  Registered:       planning/sessions/active.md (podzoneAgentTeam)

Switch to the new VS Code window to run the agent session.
At session end, the agent commits team/atlas/ changes, pushes the PAT branch, and raises
a PR to podzoneAgentTeam main. Hermes reviews and merges during /consolidate-tasks.
```

### Fissioned team mode
```
Session launched: athena-2026-05-01-curriculum-content

  Mode:             fissioned (home_repo: trainingTeam)
  Workspace:        ~/sessions/athena-2026-05-01-curriculum-content/session.code-workspace
  Task branch:      athena/2026-05-01-curriculum-content  (prompt-engineering-training)
  PAT branch:       session/athena-2026-05-01-curriculum-content  (trainingTeam)
  Worktrees:        prompt-engineering-training, trainingTeam
  podzoneAgentTeam: ~/workspace/podzoneAgentTeam  (plain clone, read-only)
  Registered:       trainingTeam/planning/sessions/active.md
                    podzoneAgentTeam/planning/sessions/active.md (apex summary line)

Switch to the new VS Code window to run the agent session.
At session end, the agent commits team/athena/ changes, pushes the PAT branch, and raises
a PR to trainingTeam main. trainingTeam Team Lead reviews during /consolidate-tasks.
```

### Migrated home-repo mode — worked example (C2-style migrated launch)
```
Session prepared: hephaestus-2026-06-25-proj039-t028

  Mode:             migrated home-repo (home_repo: home-podzone-hephaestus)
  Pinned --session-id: 46a03b8e-c90c-4ce7-9f93-ae839a696b43
                       (persisted at ~/sessions/{session-id}/.pinned-session-id)
  Brief point:      session_substrate `session` point keyed to the pinned id — read-back ok
                    (from team-lead/briefs/2026-06-25-proj039-t028.md)
  PAT branch:       session/hephaestus-2026-06-25-proj039-t028  (home-podzone-hephaestus)
  Worktrees:        home-podzone-hephaestus (launch cwd), agent-tooling, podzoneAgentTeam
  Materialise hook: wired locally (settings.local.json + hook copy + git exclude)
                    [or "resident — no-op" once C4 lands]
  SessionEnd:       resident (session-end-finalise.py, C2-v2.1c)
  Registered:       planning/sessions/active.md (podzoneAgentTeam, apex)
  Verify:           materialise OK {'brief': 1, 'tasks': 50}

Run this in a standalone terminal:

  cd ~/sessions/hephaestus-2026-06-25-proj039-t028/home-podzone-hephaestus && \
    claude --session-id 46a03b8e-c90c-4ce7-9f93-ae839a696b43

⚠️ Finish with a clean /exit — that fires the resident SessionEnd finalise (response +
rollup + telemetry + push-then-delete + brief-result PR). At session end the agent's
`.workspace/` was materialised from Qdrant at SessionStart; there is no .code-workspace.
```

## Cleanup (done by consolidate-tasks, not here)

After all PRs (task repos + the PAT/home repo) are merged:

```bash
git -C ~/workspace/{repo} worktree remove ~/sessions/{session-id}/{repo-name}
git -C ~/workspace/{home_repo} worktree remove ~/sessions/{session-id}/{home_repo}
rmdir ~/sessions/{session-id}   # only if empty
```

(For standard mode the PAT worktree is `~/sessions/{session-id}/podzoneAgentTeam`.)

Mark the session `cleaned-up` in `planning/sessions/active.md`.
