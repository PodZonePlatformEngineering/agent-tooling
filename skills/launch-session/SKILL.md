---
name: launch-session
description: Launch an agent session — migrated home-repo default is serial simple-repo mode (runs in the primary clone on a session branch, guarded; T-045); headless one-shot or interactive (Team Lead only)
---

This skill is for **the Team Lead** of each team. It creates an isolated working environment
for an agent session and registers it so the Team Lead can track it during consolidation.

> **Default path for a headless brief-first build dispatch (PROJ-039/T-108):**
> `agent-tooling/tools/launch.sh <brief-id> --repos repo1,repo2`. Use this; the manual Steps
> 3–8 below are the documented fallback and reference for what it automates. `launch.sh`
> collapses Steps 3–8 into one invocation — preflight-abort, pull, working-repo branch+PR
> pre-creation, lock, then an auto-rotating retry loop across the subscription tokens, all
> before/around a minimal `claude -p "Hi {Agent}. Continue with the brief."` The wrapper owns
> 100% of git ceremony (commit+push every touched repo at every loop-exit boundary and at
> final cleanup) — the inner session runs no git itself, so its prompt drops the "commit
> early and often" clause (see `docs/brief-authoring.md` item 5). `launch.sh` lives only in
> `agent-tooling/tools/` — it has never been scaffolded into home repos. Run it from the
> home repo so its cwd-based `HOME_REPO_DIR` auto-resolve (`git rev-parse --show-toplevel`)
> picks up the right clone, invoking it by path into `agent-tooling`: `cd
> ~/workspace/{home_repo} && ~/workspace/agent-tooling/tools/launch.sh {brief_id} --repos
> {repo1},{repo2}` (or pass `--home-repo ~/workspace/{home_repo}` explicitly from any cwd).
>
> **Scope caveat:** `launch.sh` is **brief-first only** (`BRIEF_ID`, `create-brief.py
> --approve`) — it has no pinned-`--session-id` path. A pinned-`--session-id` launch (rare;
> only when a session must be resumable against a specific id pre-registered elsewhere)
> still uses the manual Steps 4–8 below. It also does not read `lifecycle_mode` from a
> working repo's `.claude/tooling-manifest.json` (T-215) — it branches every named working
> repo unconditionally, so a `trunk`-mode repo launched through `launch.sh` today gets an
> unwanted branch+PR instead of its declared direct-to-main behaviour. Until T-215 lands,
> launch a `trunk`-mode working repo through the manual Steps 3–8 ceremony instead, which
> reads the flag correctly.
>
> **One-time prerequisite:** before the first `launch.sh` dispatch in a subscription
> rotation, the Team Lead runs `resolve-launch-tokens.py` (via the secrets MCP) once to
> produce `~/.claude/launch-tokens.resolved.json` — reusable across dispatches until it
> goes stale. Successive `launch.sh` invocations rotate which subscription they start on
> automatically (a persistent cursor file, T-216, `agent-tooling` v1.29.0); pass
> `--token-index N` to force a specific starting token for a deliberately staggered
> parallel launch.
>
> **Registration prerequisite:** `launch.sh` never calls `planning.register_session()`
> itself — a `launch.sh` dispatch is registered on the board today only if the Team Lead
> runs Step 7 manually, before calling `launch.sh` (see `/consolidate-tasks` Step 0a).
> Register the dispatch first if you want it visible on the plannerapi board.
>
> The manual Steps 3–8 below remain the documented reference for what the wrapper
> automates, and are still the path for **interactive** (team-lead / high-uncertainty)
> launches, which `launch.sh` does not cover (headless one-shot only — no
> queue/scheduler, single-shot per invocation).

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

### Migrated launch mode — interactive vs headless (PROJ-039/T-040)

The migrated home-repo launch has **two modes**, selected per-session at launch (Step 1, the
`mode` input):

- **headless** (default for autonomous / build briefs) — emits a **one-shot** command
  `claude --session-id {uuid} -p "<continue+escalate prompt>"`. The agent runs the brief to
  completion in a single non-interactive pass, commits + pushes its PRs, and exits; a
  subscription-limit halt is the *preferred* failure mode (re-launch **fresh** from committed
  state — never resume). Pairs with the T-041 raise-to-lead-and-exit role convention so a
  headless agent never blocks waiting for an operator who is not on the line.
- **interactive** (default for **team-lead** / high-uncertainty work) — emits the
  standalone-terminal `claude --session-id {uuid}` command (no `-p`); the operator drives the
  session live.

**Why headless is the default for builds (operator principle, 2026-06-29):** an interactive
session *continued* after a subscription-limit pays the full prompt-cache **reload** even when
most of the work is done — evidence: session `45474746` was alive ~67 h but active ~8 h across 4
bursts, the idle gaps expiring the ~5-min cache and driving **83.8 M** cache-creation tokens
(≈82 % of its headline). Headless one-shot avoids the resume-reload tax. Memory:
`feedback_headless_autonomous_sessions`. The only delta between the two modes is the **emitted
command** (Step 8) and, for headless, the **continue+escalate prompt** — pin/author/materialise/
register are identical.

### Brief sizing & model tiering (PROJ-039/T-058 — operator-approved 2026-07-06)

Size and tier every headless dispatch BEFORE authoring the brief. Evidence: the
finalise-hardening bundle (5 tasks + a 7-repo sweep in one brief) consumed two
subscription windows across four runs, each recovery run paying a review-the-bank
re-orientation pass; re-scoped per these rules, the follow-on bundles each closed
in a single window. Full guidance: `agent-tooling/docs/brief-authoring.md`.

1. **Cap a headless build brief at ~2–3 related tasks**, sized to plausibly finish
   inside ONE subscription window. A mid-bundle limit-stop is not free: every
   recovery run re-derives context before resuming. If a bundle needs a "phase 2",
   it is two briefs.
2. **Split mechanical work out of build briefs** — anything "apply a proven pattern
   N times" (fleet byte-identity sync PRs, per-repo rollouts, batch renames) gets
   its own brief dispatched with **`--model sonnet`** (or `haiku`). Proven: the
   T-059 and T-061 fleet sweeps each ran clean on sonnet. Build/reasoning briefs
   keep the default model. Record the tier choice in the launch registration.
3. **Order by risk, make the tail deferrable.** Hardest/blocking task first; the
   last step should be explicitly droppable ("if budget tightens, deliver X alone
   with a per-task account") so a limit-stop yields a mergeable partial, not a
   stranded bundle.
4. **In-session subagent fan-out** (PROJ-035/T-011): for parallelisable mechanical
   subtasks *inside* a reasoning brief, the brief SHOULD direct the agent to fan out
   to sonnet/haiku subagents rather than splitting a separate dispatch — the
   expensive model keeps the core reasoning.
5. **Self-banking is mandatory prompt furniture** — every headless prompt carries
   "commit early and often … so a limit-stop self-banks" (template below). The
   Team Lead should never need an emergency WIP bank on a crashed session again.

### Brief-first launch variant (PROJ-039/T-043) — no pinned sid

A **brief is a first-class point, not linked to a specific session** (`briefs` collection). The
brief-first variant kills the pinned-`--session-id` ceremony: the brief is authored **once** and
may be worked across **many** sessions (re-launch-fresh after a limit-stop re-materialises the
*same* brief; the training case authors one brief **per trainee** and works it over a curriculum).
It coexists with the pinned-sid path above — pick per launch (C-003; the pinned-sid path stays for
legacy/one-shot dispatches). The deltas against the migrated pinned-sid steps:

| step | pinned-sid path (above) | **brief-first variant** |
|------|--------------------------|--------------------------|
| Step 3 (lock `$SID`) | `uuidgen`-pinned Step 4 uuid | **`$SID` = the `brief_id` string** — not a fresh `uuidgen` (PROJ-039/T-119: a synthetic sid here can never match T-054's `owned_sids=[brief_id]` release, so finalise always reports the release as failed and the lock never clears) |
| Step 4 (pin sid) | `uuidgen` → `--session-id`, persist | **skip** — no pinned sid; the runtime/sidebar auto-id is safe (materialise keys the session point to it) |
| Step 5 (author point) | `create-session-point.py` (session point, keyed to pinned sid) | **`create-brief.py`** — author/approve the `briefs` point from the committed brief file (keyed to `brief_id`); verify read-back |
| Step 6 (wiring) | materialise resolves by pinned sid | materialise resolves by **`BRIEF_ID`** env var → stands up the session point under the **runtime** sid, appends the sid to `briefs.session_ids[]` |
| Step 8 (launch) | `claude --session-id {uuid}` | `BRIEF_ID={brief_id} claude` (no `--session-id`); headless adds a brief-complete instruction |

**`brief_id` format:** `{team}/{date}-{slug}` (agent form); `training/{date}-{curriculum-slug}-{trainee}`
(trainee form). The `briefs` point id is `uuid5(NAMESPACE_DNS, brief_id)` — re-authoring the same
`brief_id` converges on the same point (idempotent). Author before launch:

```bash
mcp__secrets__secret_run -k podzone_qdrant_apikey -- \
  python3 ~/workspace/agent-tooling/tools/create-brief.py \
    --brief-id "{team}/{date}-{task-slug}" \
    --team {team} --author {team_lead} --assignee {agent} --assignee-type agent \
    --work-item PROJ-XXX/T-YYY \
    --body-file ~/workspace/{home_repo}/team-lead/briefs/{date}-{task-slug}.md \
    --approve      # promotes to `approved` — the ADR-008 D5 execution gate
```

`--approve` (or `--status approved`) is **required before launch**: materialise refuses to stand up
a session for a brief that has not cleared the gate. **Verify read-back** (`get_brief` returns a
body + `status` in {approved, in_progress, complete}) before launching — an unapproved / missing
brief HALTs the agent at SessionStart (SD-3-002, no stale fabrication).

**Wiring (Step 6, brief-first):** the SessionStart materialise hook is **resident + committed-wired**
(PROJ-039/T-052) — you do NOT wire it. You only need to make `BRIEF_ID` visible to it. Pass it
either inline on the launch command (simplest, headless) or via a `settings.local.json` `env` block
(env only — no hook wiring):

```json
{ "env": { "BRIEF_ID": "{team}/{date}-{task-slug}" } }
```

`BRIEF_ID` unset → the resident materialise runs its legacy session-point-keyed path unchanged
(backwards compatible; a no-op when there is no matching point).

**Launch (Step 8, brief-first) — headless:** both of the blocks below (the write-capability
gate and the manual launch-command) are unnecessary when using `launch.sh` — it runs the
gate and assembles/exports `BRIEF_ID` itself. This block is the reference for what
`launch.sh` automates, and the fallback for a hand-emitted launch. First the mandatory
write-capability gate (see Step 8 — it applies identically here, and a failure means **do
not emit the launch command**):

```bash
python3 ~/workspace/agent-tooling/tools/ensure-local-settings.py \
  --check --repo ~/workspace/{home_repo} \
  || { echo "HALT: launch clone lacks headless write capability — fix per the tool's message, then re-run prep"; exit 1; }
```

then the launch command itself:

```bash
cd ~/workspace/{home_repo} && BRIEF_ID="{team}/{date}-{task-slug}" \
  claude -p "Hi {Agent}. Continue with the brief. Work {hardest/blocking task} first{; the last step (X) is deferrable — if budget tightens, deliver the rest alone with a clear per-task account}. Commit early and often so a limit-stop self-banks. Commit and push all PRs when done. If the brief is now FULLY complete, include a line \`Brief-Status: complete\` in your final response; otherwise it stays in_progress for the next session. If anything needs operator direction you cannot resolve, raise it to {team_lead} with progress so far via your session response, and exit — do not wait."
```
(Serial simple-repo default — launch cwd is the primary clone on its session branch, T-045.)

The `Brief-Status: complete` line is the **deterministic completion signal** SessionEnd finalise
reads to stamp the brief `complete` + `completed_at`; absent it, the brief stays `in_progress` so
the next session against the same `BRIEF_ID` accumulates its sid and continues. (Interactive
brief-first is the same launch without `-p`.) **Verify (Step 9)** by simulating materialise with
`BRIEF_ID` set and asserting the sentinel reports `source: brief` and `session_ids[]` grew.

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
  - *(Optional)* an embed endpoint via the `OLLAMA_HOST` env var — authoring embeds the
    brief vector only when explicitly configured (PROJ-041/T-002); with no endpoint the
    brief point is written vector-less and the PROJ-042 enrichment job embeds in retrospect
  - `agent-tooling` cloned at `~/workspace/agent-tooling` (source of `create-session-point.py`
    + `session-materialise.py`)

## Inputs (ask Martin if not provided)

- **agent** — e.g. `hephaestus`, `atlas`, `thoth`
- **task-slug** — short identifier for the session work, e.g. `dev-functional-testing`
- **repos** — which repos the session needs (default: from the agent's identity file)
- **mode** — `headless` | `interactive` (migrated home-repo mode only; PROJ-039/T-040).
  **Default by work type:** autonomous / build briefs → `headless`; **team-lead** or
  high-uncertainty briefs → `interactive`. This is an explicit operator choice at launch —
  ask Martin if the brief's nature is unclear. (See "Migrated launch mode" above and Step 8.)

  > **Mode-selection mechanism (operator-DECIDED simplest option, PROJ-039/T-040, flagged to
  > Hermes to confirm):** the mode is a **launch-time input to this skill** (the Team Lead
  > picks it when launching), not a brief front-matter field. Rationale: the launcher already
  > takes conversational inputs and the Team Lead is the natural decision point, so no new
  > YAML-parse step is added to brief authoring. A brief front-matter `mode:` field can be
  > layered on later as an override without changing this default.

## Step 1 — Resolve agent identity and repos

> **Launching from a migrated team-lead home repo (PROJ-039/T-038).** When the operator
> driving this skill is a *migrated* team lead (their own `home_repo` is `home-<team>-<agent>`,
> e.g. Athena in `home-training-athena`), the team they launch agents for lives in a SEPARATE
> team repo (`<team>Team`), not the home repo. Resolve that team repo from identity —
> `python3 .workspace/agent-tooling/lib/team_repo.py --home-repo "$HOME_REPO" --json` (or decode
> `home-<team>-<agent>` → `<team>Team`) — and clone it into `.workspace/` so the team's
> `workspaces/identity/` and briefs are reachable. The "fissioned team repo" in the identity
> search below IS that resolved `<team>Team`.

Read the agent's identity YAML. Search in this order:
1. `podzoneTeam/workspaces/identity/{agent}.identity.yaml`
2. `podzoneTeam/workspaces/identity/martin-{agent}-*.identity.yaml`
3. `{fissioned_team}/workspaces/identity/{agent}.identity.yaml` (if a fissioned team repo
   is present in the workspace and the agent belongs to it)

Extract:
- `home_repo` — determines standard vs. fissioned vs. migrated mode (see below)
- `repos` list — all repos in scope for this agent
- `workspace` — canonical workspace filename (legacy modes only)
- canonical workspace file: `{home_repo_path}/workspaces/{workspace}.code-workspace`

Filter `repos` to those actually needed for this session's task (ask Martin if unclear;
default to all repos in the identity file).

**Mode determination (F8 — registry first, identity YAML second):** the identity YAML's
`home_repo` is a **hint**, not the source of truth — an agent's YAML can lag a migration
(Hestia's still said fissioned post-migration and would have routed a launch down the
retired path). Always check the registry before trusting the YAML:

1. Check `planning/projects/PROJ-032-agent-home-repos/migrated-agents.md` first. If the
   agent is listed there, their `home_repo` in the registry is authoritative → **migrated
   home-repo mode** (PROJ-039/T-011 C2, T-006) — see the migrated note in Step 2 and the
   migrated subsections below. If the registry's `home_repo` disagrees with the identity
   YAML, trust the registry and flag the stale YAML for a sweep (don't silently proceed
   on the YAML's value).
2. Otherwise, fall back to the identity YAML's `home_repo`:
   - `home_repo == podzoneTeam` → **standard mode**
   - Else (`trainingTeam` / `roadmapTeam`) → **fissioned team mode**

Agents not listed in the registry are unaffected by C2 — they keep standard / fissioned
mode exactly as before. When operating on the current session's own agent (i.e. the
identity YAML unambiguously already reads `home_repo: home-*`) you may skip the registry
read — that value could only be current or migrated, never stale-fissioned.

### Migrated home-repo mode — the ritual at a glance

The migrated launch codifies what was hand-assembled each session across C2a→C2-v2.1c.
The shared Steps 1–2 run first, then the migrated-only steps:

1. **Detect migrated** (this Step) → branch behaviour.
2. **Pre-flight brief check** on the home repo's `origin/main` (Step 2).
3. **Pin a pre-generated `--session-id`** (Step 4) and persist it.
4. **Author the brief as a `session_substrate` session point** keyed to the pinned id
   (Step 5); verify read-back.
5. **Preflight + branch the primary clone** (Step 3, migrated subsection): the session
   runs directly in `~/workspace/{home_repo}` (the launch cwd) on a session branch — **no
   worktree, no `~/sessions/{sid}/` dir**. The `session_guard` preflight asserts the clone
   is clean + on a ff'd `main` (or HALTs), takes a one-session lock, then branches.
   Registration (Step 7) is a DB call independent of clone branch state — F11 no longer
   applies (see the Step 3 callout).
6. **SessionStart materialise + SessionEnd finalise are resident** (Step 6, migrated
   subsection) — both committed-wired (materialise since PROJ-039/T-052). **No manual copy
   / exclude / `settings.local.json` wiring step** — only make `BRIEF_ID` visible (inline
   or a `settings.local.json` env-only block).
7. **Register** the session via `planning.register_session()` (Step 7).
8. **Emit the launch command for the selected mode** (Step 8) — headless one-shot
   (`-p "<continue+escalate prompt>"`) for builds, interactive standalone-terminal for
   team-lead/high-uncertainty work — + clean-`/exit` reminder; then optionally **verify the
   materialise resolves** before handing off (Step 9).

(Steps below are ordered for execution: shared 1–2, preflight+branch 3, then migrated
4→5→6→7, launch 8, verify 9. Pin-then-author-then-branch is also valid — the only hard
ordering is *pin the id before authoring the point*, since the point is keyed by it.)

### Serial simple-repo mode (default, PROJ-039/T-045)

**Parallel same-agent sessions are given up** (operator decision 2026-07-02 — the merge
gate serialises sessions anyway, and per-agent home repos already give the isolation
worktrees used to). A migrated session therefore runs **directly in the primary clones**,
each on a session branch, with **no worktrees and no `~/sessions/{sid}/` directory**. The
migrated launch collapses toward:

```bash
cd ~/workspace/{home_repo} && BRIEF_ID={brief_id} claude --model … -p "…"
```

Worktree isolation is retired. Its safety is replaced by three **main-guards**
(`lib/session_guard.py` — one CLI call each, so this skill carries no worktree bash):

- **Start guard (pre-branch, Step 3).** `session_guard.py preflight --repo ~/workspace/{repo}`
  asserts the clone is clean and on a fast-forwarded `main`; a leftover session branch from a
  prior crash **HALTs with a recovery message** (auto-recovers only if the T-030 finalise
  ledger shows that clone's last session finalised). Do not branch on a `halt`.
- **One-session lock.** `session_guard.py lock --repo ~/workspace/{repo} --sid {sid}` refuses
  a concurrent launch against the same clone (`~/.claude/session-locks/{repo}.lock`); the
  finalise releases it. A `halt`/refuse is a clean stop, not a failure to paper over.
- **End guard (session-end).** The SessionEnd finalise returns each touched clone to a ff'd
  `main` and deletes the pushed session branch (`session_guard.return_to_main`) — resident,
  nothing to wire here. A crash before it leaves the branch in place; the next launch's start
  guard catches it.

The steps below are written for this default — the legacy worktree ceremony (`--legacy-worktree`)
has been removed (PROJ-039/T-049).

## Step 2 — Pre-flight brief check

Before creating any worktrees, confirm a commission brief for this session exists on
`origin/main` of the home repo. This prevents agents from starting without proper context.

**Home repo for the check:**
- Standard mode: `podzoneTeam`
- Fissioned mode: the agent's `home_repo`
- **Migrated home-repo mode (T-006):** the agent's `home_repo` (`home-<team>-<agent>`).
  The Team Lead routes the brief by **pushing it directly to the home repo's default
  branch** — `brief(T-XXX): …` as the commit message, **no branch, no PR** (the
  2026-07-10 change-visibility policy, OPERATING-MANUAL §2b: dispatching a brief to a
  home repo is operational, hook-adjacent territory, not briefed work product — see
  `scaffold/team-lead/OPERATING-MANUAL.template` §2b/§4.1). The committed file is the
  human-/audit record; the commit message is the visibility surface — there is nothing
  to review or merge before the pre-flight check below runs. The SessionStart
  materialise hook then materialises `.workspace/` from Qdrant `session_substrate`
  (or `briefs`, brief-first path) using that committed file as the source of truth.
  Committed brief path on the migrated home repo: `team-lead/briefs/{date}-{task-slug}.md`.

  > This retires the pre-2026-07-10 ceremony (a `team-lead` branch + PR + merge to the
  > home repo's `main`) — that PR-review step is gone fleet-wide, not just for trunk
  > repos; branch-mode and trunk-mode home repos both receive briefs the same way.
  > What still differs by lifecycle mode is the **session's own** launch shape (Step 3),
  > not brief delivery.

**Brief directory by mode** (call it `{brief-dir}` below):
- Standard / fissioned mode: `team/{agent}/incoming/`
- **Migrated home-repo mode (T-006):** `team-lead/briefs/`

**Check procedure:**

1. List the committed briefs on `origin/main`:
   ```bash
   git -C ~/workspace/{home_repo} show origin/main:{brief-dir}
   ```
2. Scan the listing for a filename matching `*{task-slug}*` (date prefix may vary).
3. **If a matching file exists:** pull `origin/main` so the launch proceeds from the
   current state, then continue to Step 3:
   ```bash
   git -C ~/workspace/{home_repo} pull --ff-only origin main
   ```
   (This is now a read-freshness pull, not a push — the brief already landed on
   `origin/main` directly in Step 2's routing above; there is no local Team Lead
   commit left to push here.)
4. **If no match is found:** abort with:
   ```
   Commission brief not on origin/main — push it directly per OPERATING-MANUAL §2b first.
   Expected: {brief-dir}{date}-{task-slug}.md
   Check: git show origin/main:{brief-dir}
   ```
   Do not create worktrees, workspace files, or session registry entries.

## Step 3 — Preflight + branch the primary clones

### Migrated home-repo mode (default) — serial simple-repo, no worktree (PROJ-039/T-045)

**Lifecycle-mode fork (PROJ-039/T-084..T-086, codified T-097).** Per repo in scope, read
`lifecycle_mode` from its `.claude/tooling-manifest.json` (reader: `lib/lifecycle_mode.py`;
absent = `branch`):

```bash
MODE=$(python3 -c "
import sys; sys.path.insert(0, '$HOME/workspace/agent-tooling')
from lib import lifecycle_mode
print(lifecycle_mode.read_lifecycle_mode('$CLONE'))
")
```

- **`branch` (default)** — existing preflight + lock + `checkout -b` below, unchanged.
- **`trunk`** — preflight + lock **only — no branch; the session runs directly on
  `main`**. **Why:** a trunk repo's SessionEnd finalise commits the session result
  straight to `main` and `push`es `origin main` (no result branch, no result PR — see
  the lifecycle-mode table in `scaffold/team-lead/OPERATING-MANUAL.template` §3). If the
  launcher branched the clone anyway, the finalise's `main`-targeted commit+push would
  land on the wrong ref and the session's work would be **stranded on an orphan branch
  nobody merges** — this is the exact failure the T-086 dogfood validated the fix for.
  So for a trunk repo, stop after the lock: skip the `checkout -b` line in the loop
  below for that repo, and the launch cwd stays `~/workspace/{repo}` on `main`.
  The launch registration (Step 7)'s PAT-branch column reads **`(none — trunk)`** for
  such a repo instead of the usual `session/{agent}-{date}-{slug}`.

The session runs **in the primary clones** — there is **no `~/sessions/{sid}/` directory
and no worktree**, on either lifecycle mode. For each repo in scope (the home repo +
each task repo from the identity `repos` list), run the start guard, take the lock, and
— for `branch`-mode repos only — branch the clone itself. The `session_guard` CLI
replaces the old worktree + ff + exclude bash with one call each:

> **F11 retired (PROJ-029/T-018).** Registration (Step 7) is now a
> `planning.register_session()` DB call, not a `planning/sessions/active.md` row — it no
> longer depends on which branch any clone is on, so there is nothing to order relative to
> this loop's `checkout -b` calls. Register whenever convenient in Step 7.

```bash
# Pinned-sid launch: SID is the Step 4 uuid (pinned below, but the value is already
# known at this point in that variant — carry it forward).
# Brief-first launch (T-043): Step 4 is skipped (no pinned runtime id yet — see the
# variant table above), so SID here MUST be the brief_id string itself, not a fresh
# uuidgen throwaway. T-054's release-side owned_sids=[brief_id] only matches a lock
# that was actually taken under brief_id — a generated uuid here permanently wedges
# the lock (PROJ-039/T-119). The lock file is a plain JSON blob keyed on an arbitrary
# string; brief_id round-trips cleanly (session_guard.py has no UUID-shape assumption).
SID=…            # pinned-sid path: the Step 4 uuid · brief-first path: "{brief_id}"
for repo in {home_repo} {task_repos…}; do
  CLONE=~/workspace/$repo
  # Start guard: clone must be clean + on a ff'd main, else HALT (recovers only a
  # leftover branch whose session already finalised). rc!=0 ⇒ do NOT launch.
  python3 ~/workspace/agent-tooling/lib/session_guard.py preflight --repo "$CLONE" || {
    echo "preflight HALT for $CLONE — resolve and re-launch"; exit 1; }
  # One-session lock (finalise releases it). rc 4 ⇒ a session already holds this clone.
  python3 ~/workspace/agent-tooling/lib/session_guard.py lock --repo "$CLONE" --sid "$SID" || {
    echo "clone $CLONE is locked by a live session — refuse"; exit 1; }
  # Lifecycle-mode fork (T-084/T-097): trunk repos stop here — lock only, no branch.
  MODE=$(python3 -c "
import sys; sys.path.insert(0, '$HOME/workspace/agent-tooling')
from lib import lifecycle_mode
print(lifecycle_mode.read_lifecycle_mode('$CLONE'))
")
  if [ "$MODE" = "trunk" ]; then
    echo "$CLONE is lifecycle_mode=trunk — locked, no branch; session runs on main"
    continue
  fi
  # Branch the clone: session/{agent}-{date}-{slug} for the home/PAT repo,
  # {agent}/{date}-{slug} for a task repo.
  git -C "$CLONE" checkout -b {branch}
done
```

Branch naming is unchanged: `session/{agent}-{YYYY-MM-DD}-{task-slug}` for the home/PAT
repo, `{agent}/{YYYY-MM-DD}-{task-slug}` for task repos (append `-2`, `-3` on a same-slug
retry). preflight already fast-forwarded `main`, so the branch starts from current origin —
the old per-worktree fast-forward dance is gone (one fetch+ff per clone, inside
preflight). The launch cwd is `~/workspace/{home_repo}` (Step 8). **podzoneTeam as a
write-target:** branch the **primary** `~/workspace/podzoneTeam` on a `session/…`
branch via the same guard+lock — the T-031 "never branch the apex primary clone" rule is
now the *normal* path, so its end-guard returns it to main at finalise (read-only apex use
still references the plain clone, unbranched). **Register it (Step 7) before this
branch step**, per the callout above — podzoneTeam is exactly the registry host this
guards against.

### Trunk-migration launches — the T-086 runbook

A **migration launch** flips a repo from `branch` to `trunk` lifecycle mode and validates
the flip in the same dispatch. This is a distinct shape from an ordinary trunk-mode launch
above (which just reads an already-`trunk` flag) — codifying it here is what closes the gap
the T-086 dogfood exposed live. Proven across T-086 (`home-podzone-hephaestus`) and its
sibling dispatches; follow this ordering exactly:

1. **Flip on `main` BEFORE the validating session launches.** The PR (or, on a repo
   already trunk-eligible for direct writes, the commit) that sets
   `"lifecycle_mode": "trunk"` in `.claude/tooling-manifest.json` must land on `origin/main`
   first — same commit/PR that carries the migration brief is fine, but it must be *merged*
   before Step 3 of the validating launch runs. Launching against a clone that still reads
   `branch` (or has no `lifecycle_mode` key yet) defeats the point: the launcher would take
   the branch-mode path and the resident hooks wouldn't be trunk-capable regardless.
2. **Dispatch with `TOOLING_UPDATE={tag}`.** The flip alone isn't enough — the repo's
   *resident* `.claude/` hooks (in particular SessionEnd finalise) must also be at a tag
   that understands `lifecycle_mode: trunk` (`lib/lifecycle_mode.py` shipped, finalise's
   trunk branch wired). Setting `TOOLING_UPDATE={tag}` on the launch env (§8 of the
   OPERATING-MANUAL) makes the SessionStart self-update pull that tag before anything
   else runs, so the hooks are trunk-capable **by SessionEnd** even though the session
   itself started on the old resident set.
3. **The validating session runs lock-no-branch**, per the lifecycle-mode fork above —
   preflight + lock only, working directly on `main`, "commit early and often" now means
   commit-and-push-to-`main` throughout the session (not just at finalise).
4. **Abort condition — the self-update did not apply.** Have the session check, early,
   that the SessionStart self-update actually landed (e.g. `.claude/tooling-manifest.json`
   reads the target `TOOLING_UPDATE` tag's version, not the pre-migration one) and that the
   `lifecycle_mode: trunk` field **survived** the manifest rewrite (a sync regression could
   drop it). If either check fails: **do not proceed** — the resident hooks are not
   trunk-aware and a normal exit would take the old branch-mode finalise path (a stray
   result branch/PR at worst, but the validation is void). Flip nothing further, raise to
   the team lead via the session response with what was found, and exit. A subscription
   -limit or operator-blocked exit mid-migration is recoverable (re-check A1–A2 on
   relaunch); silently continuing past a failed self-update is not.

Session ID format: `{agent}-{YYYY-MM-DD}-{task-slug}`
Session directory (standard/fissioned mode only): `~/sessions/{session-id}/`

### Standard mode (home_repo == podzoneTeam)

#### Task repo worktrees

For each required task repo (excluding podzoneTeam itself):

```bash
git -C ~/workspace/{repo-path} worktree add \
  ~/sessions/{session-id}/{repo-name} \
  -b {agent}/{YYYY-MM-DD}-{task-slug}
```

Branch naming: `{agent}/{YYYY-MM-DD}-{task-slug}` — session-scoped, collision-free.
If a branch with that name already exists (retry same task): append `-2`, `-3`, etc.

#### podzoneTeam PAT worktree

Always create a podzoneTeam worktree — agents write their outbox and memory files
here on a session branch, then raise a PR to main at session end.

Branch naming: `session/{agent}-{YYYY-MM-DD}-{task-slug}`

```bash
git -C ~/workspace/podzoneTeam worktree add \
  ~/sessions/{session-id}/podzoneTeam \
  -b session/{agent}-{YYYY-MM-DD}-{task-slug}
```

### Fissioned team mode (home_repo != podzoneTeam)

Fissioned agents do **not** get a podzoneTeam session branch. Their PAT branch
lives in their own home repo.

#### Fissioned task repo worktrees

For each repo in the identity `repos` list **except** podzoneTeam:

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

#### podzoneTeam — plain clone reference (no worktree)

If podzoneTeam is listed in the agent's repos (for read-only skills/ops doc access),
reference it as a plain folder — do not create a worktree or session branch:

```
podzoneTeam path: ~/workspace/podzoneTeam  (plain clone, read-only)
```

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
Embedding is optional (PROJ-041/T-002): the brief vector is set only when `OLLAMA_HOST`
is configured, else the point is written vector-less (a stderr note says so). When
embedding, the embed input is token-safe since T-027 (the full brief is always stored in
the payload; only the embed input is head-truncated):

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
Re-check Qdrant reachability / API key and re-run Step 5.

## Step 6 — Generate session workspace file / wire materialise (mode-split)

### Standard / fissioned mode — generate session workspace file

**Standard mode:** Copy from `podzoneTeam/workspaces/{workspace}.code-workspace`.

**Fissioned team mode:** Copy from `{home_repo_path}/workspaces/{workspace}.code-workspace`
(e.g. `~/workspace/trainingTeam/workspaces/athena.code-workspace`).

Write to: `~/sessions/{session-id}/session.code-workspace`

Rewrite all `path` entries to point to session-local worktree paths:
- `gitopsapi` → `~/sessions/{session-id}/gitopsapi`
- `podzoneTeam` → `~/sessions/{session-id}/podzoneTeam` (standard mode)
- `podzoneTeam` → `~/workspace/podzoneTeam` (fissioned mode — plain clone path)
- `trainingTeam` → `~/sessions/{session-id}/trainingTeam`
- etc.

Rewrite `identity_file:` in `claude.projectInstructions` to resolve against the
session-local worktree path of the home repo:
- Standard: `podzoneTeam/workspaces/identity/{agent}.identity.yaml`
  → `~/sessions/{session-id}/podzoneTeam/workspaces/identity/{agent}.identity.yaml`
- Fissioned: `{home_repo}/workspaces/identity/{agent}.identity.yaml`
  → `~/sessions/{session-id}/{home_repo}/workspaces/identity/{agent}.identity.yaml`

### Migrated home-repo mode — SessionStart materialise + SessionEnd finalise (now fully resident)

No `.code-workspace` is generated — the agent's `.workspace/` is materialised from Qdrant
at SessionStart. Both lifecycle hooks are now **resident** in migrated home repos (committed
`.claude/settings.json`) — **there is NO manual copy/exclude/`settings.local.json` step**:

- **SessionStart materialise** (`session-materialise.py`) — **resident + committed-wired**
  since PROJ-039/T-052. It is in the synced hook set (`scaffold.sh` / `sync-agent-tooling.sh`)
  and its SessionStart wiring is in the committed `settings.json` (`$CLAUDE_PROJECT_DIR`-anchored,
  cwd-independent). The old hand-copy + `settings.local.json` wiring is **deleted** — it was the
  Thoth T-022 silent-failure class (copy omitted → wiring pointed at a missing file → skipped
  silently → no session point). `session-start.sh` now HALTs loudly if `BRIEF_ID` is set but the
  materialise hook file is missing, so an incomplete sync can never fail silently again.
- **SessionEnd finalise** (`session-end-finalise.py`) — resident since C2-v2.1c.

Confirm both are wired (they are, by scaffold/sync); do **not** re-add them and do **not** author
a `settings.local.json` for materialise. The only per-launch env `settings.local.json` may still
carry is `BRIEF_ID` (brief-first) — or pass it inline on the launch command (simplest, headless).
(Independent of any launch, the same file also carries the standing
`permissions.defaultMode: bypassPermissions` block — T-132, owned by scaffold/prep, verified by
the Step 8 headless write-capability gate; it is not per-launch wiring.)

## Step 7 — Register the session (PROJ-029/T-018 — DB-backed, Phase 4)

Registration writes to `planning.session` (the relational board plannerapi's GUI reads)
via the `planning.register_session()` RPC — **not** a `planning/sessions/active.md` row.
This is one call regardless of mode (standard / fissioned / migrated): every team's
sessions live in the same `podzone-planner` DB today (a fissioned team's own `planning.*`
tenant doesn't exist yet — out of scope while roadmapTeam stays closed; revisit this step
if/when one is stood up).

Resolve the brief's work-item ref(s) (`--work-item PROJ-XXX/T-YYY`, carried on the brief
itself — `create-brief.py`'s own `--work-item` input) to real `planning.task.id` values,
then register:

```bash
python3 ~/workspace/agent-tooling/tools/register-planning-session.py \
  --brief-id "{brief_id}" --agent {agent} --home-repo {home_repo}
```

Requires `PLANNING_DATABASE_URL` in the environment — the `planning.session`-write-capable
`planning_automation` service role's connection string (PROJ-029/T-021: role
`planning_automation`, db `neondb`, password held as the `planner-automation-password`
secretctl entry). Same operational-prerequisite posture `planning_mirror.connect()`'s own
docstring already documents for the mirroring hooks — provisioning it into the session
environment (e.g. via a `settings.local.json` env block, same mechanism `BRIEF_ID` already
uses above) is a one-time setup step, not something this script does itself.

Omit `--work-item` to resolve from the brief's own `work_items[]` (the normal case); pass
it explicitly only to override. The script HALTs — does not silently skip registration —
if a work-item ref doesn't resolve to a tracked task (typo, or a brief genuinely not tied
to one): create the task first (`/create-task`) or fix the ref before continuing. Prints
the new `planning.session.id` on stdout; capture it if a manual finalise needs it later
(the migrated pinned-sid path already has this id — the runtime session id and the
`planning.session.id` are logged together, not conflated: `session_substrate`/`briefs`
still key off the runtime sid, `planning.session.id` is a separate board-facing row).

**Re-launch (brief-first, after a subscription-limit halt):** re-running this command
against the same `brief_id` is expected and safe — `planning.session.brief_id` is not
unique (T-018's own prerequisite fix, `009_session_brief_id_not_unique.sql`), so each
dispatch attempt gets its own row rather than colliding on the first one.

> **F11 — if the registry host is a clone this session branches, register before
> branching.** `register_session()` is a DB write, not a file edit in a clone, so F11 no
> longer applies to *this* step the way it did under the markdown registry — nothing here
> depends on which branch a clone is on. The underlying constraint F11 protected (register
> the dispatch before altering the thing being registered) doesn't recur once registration
> moved off the filesystem.

## Step 8 — Launch (mode-split)

### Standard / fissioned mode — launch VS Code window

```bash
"/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" \
  --new-window ~/sessions/{session-id}/session.code-workspace
```

### Migrated home-repo mode — emit launch command (interactive | headless)

Do **not** open a VS Code window. Emit a command for Martin to run in a **standalone
terminal**, launching from the **primary home-repo clone** `~/workspace/{home_repo}` (the cwd
materialise writes into, now on the session branch from Step 3) with the pinned id. The command
shape depends on the `mode` input (Step 1).

#### interactive (team-lead / high-uncertainty — operator drives the session live)

```bash
cd ~/workspace/{home_repo} && claude --session-id {pinned-uuid}
```

#### headless (default for autonomous / build briefs — one-shot, non-interactive)

> **Prefer `agent-tooling/tools/launch.sh {brief_id}` over hand-emitting the command below** (T-108) —
> it runs this section's gate + staging + launch automatically and adds subscription-token
> rotation on a limit-stop. Use the manual steps below only for an interactive launch, or
> when `launch.sh` genuinely doesn't cover the case at hand.

**Write-capability gate (MANDATORY headless prep, PROJ-039/T-132 — fail loud, before
emitting the command).** A headless session has no interactive permission prompt: if the
launch clone's gitignored `.claude/settings.local.json` does not grant write capability,
the whole dispatch burns as a no-op (`Write` never grantable, shell writes
sandbox-blocked, `git commit` stuck at "requires approval" — Athena's PROJ-046/T-001
sid `2932eae9`). Verify **before** emitting the launch command:

```bash
python3 ~/workspace/agent-tooling/tools/ensure-local-settings.py \
  --check --repo ~/workspace/{home_repo} \
  || { echo "HALT: launch clone lacks headless write capability"; exit 1; }
```

On failure, **do not emit the launch command**. Fix at prep (an operator/Team-Lead
action — never have the agent self-elevate its own clone mid-session):
`python3 ~/workspace/agent-tooling/tools/ensure-local-settings.py --repo ~/workspace/{home_repo}`
merges `permissions.defaultMode: bypassPermissions` into the file, preserving existing
`hooks`/`env`/`permissions.allow` keys; then re-run the check. Fresh scaffolds already
carry the block (scaffold.sh emits it, same tool).

> **T-103 reconciliation (recorded 2026-08-01, T-132).** The settings-level
> `permissions.defaultMode: bypassPermissions` in each clone's gitignored
> `settings.local.json` is the **single durable mechanism** for headless write
> capability (T-103 Part A as proposed in `launch-wrapper-proposal.md`, now
> implemented: scaffold emits it, this gate verifies it). The 2026-07-20 interim
> operator-memory rule — append `--dangerously-skip-permissions` to every emitted M5
> headless command — is **retired**: emitted commands carry **no** permission flag.
> One mechanism, not two: the flag was per-command (forgettable — recurrences #2/#3
> were exactly Hermes omitting it), invisible to prep verification, and redundant
> once the settings block is guaranteed. This gate makes the settings path
> prep-verifiable, which the flag never was.

Emit the same launch but with a `-p` one-shot prompt. The agent runs the brief to completion,
commits + pushes its PRs, and exits without an operator on the line:

```bash
cd ~/workspace/{home_repo} && claude --session-id {pinned-uuid} \
  -p "Hi {Agent}. Continue with the brief. Work {hardest/blocking task} first{; the last step (X) is deferrable — if budget tightens, deliver the rest alone with a clear per-task account}. Commit early and often so a limit-stop self-banks. Commit and push all PRs when done. If anything needs operator direction you cannot resolve, raise it to {team_lead} with progress so far via your session response, and exit — do not wait."
```

**Standard continue+escalate prompt (template — the launcher fills the fields):**

> "Hi **{Agent}**. Continue with the brief. Commit and push all PRs when done. If anything needs
> operator direction you cannot resolve, raise it to **{team_lead}** with progress so far via
> your session response, and exit — do not wait."

- `{Agent}` — capitalised agent name (e.g. `Hephaestus`).
- `{team_lead}` — the agent's team lead for escalation (e.g. `Hermes`; for a fissioned team,
  that team's lead). The escalation channel is the substrate **response** — finalise records it
  and the lead picks it up at `/consolidate-tasks` (proven path; no interactive block).
- The launcher MAY append task refs (e.g. "— T-040 then T-041") and a one-line "pick the
  simplest reasonable option and note it for {team_lead}" allowance for self-contained briefs,
  but the raise-to-lead-**and-exit** clause is mandatory and must not be dropped.
- **Headless ⇒ no `AskUserQuestion`-and-wait** (no operator on the line — T-041 role
  convention). The prompt instructs raise-to-lead-and-exit precisely so the agent never blocks.
- **T-058 budget-discipline furniture is mandatory** for every headless build prompt:
  the ordering clause (hardest first), the **deferrable-tail clause** (drop it only for a
  genuinely single-task brief), and **"commit early and often so a limit-stop self-banks"**.
  The `{…}` clauses are filled per brief; see "Brief sizing & model tiering" above +
  `docs/brief-authoring.md`. Mechanical sweep briefs launch with `--model sonnet`.

> ⚠️ **Finish with a clean `/exit`.** SessionEnd finalise (response + rollup + telemetry
> push + push-then-delete + brief-result PR **on a `branch`-mode repo** — a `trunk`-mode
> repo's finalise instead commits the result straight to `main` and pushes, no PR, see the
> lifecycle-mode fork in Step 3) hangs off the SessionEnd hook; a clean `/exit`
> is what fires it reliably. In **headless** mode, `claude -p` exits cleanly when the one-shot
> completes, which fires SessionEnd; a subscription-limit halt is the preferred failure mode
> (re-launch **fresh** from committed state — never resume). Closing the terminal / killing the
> process can skip finalisation. If SessionEnd is missed, finalise manually (primary-clone
> default; note the `cwd` so the end-guard returns the right clone to main):
> `echo '{"session_id":"{pinned-uuid}","cwd":"'$HOME'/workspace/{home_repo}","transcript_path":"<path>"}' | python3 ~/workspace/{home_repo}/.claude/hooks/session-end-finalise.py`

## Step 9 — Verify the materialise resolves (migrated home-repo mode; recommended)

Before handing off, simulate the SessionStart hook against the worktree and assert the
sentinel reports success — this catches a mis-keyed point or an auth gap *before* the
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
launching (re-check Step 5 read-back, Qdrant reachability, API-key propagation).
This is a dry run; the real launch in Step 8 re-materialises at SessionStart.

## Output

### Standard mode
```
Session launched: atlas-2026-04-06-shared-artifact-store

  Mode:             standard (home_repo: podzoneTeam)
  Workspace:        ~/sessions/atlas-2026-04-06-shared-artifact-store/session.code-workspace
  Task branch:      atlas/2026-04-06-shared-artifact-store
  PAT branch:       session/atlas-2026-04-06-shared-artifact-store  (podzoneTeam)
  Worktrees:        agentsonly-infra, agentsonly-apps, podzoneTeam
  Registered:       planning.session (via register_session; id 7f2c…) — plannerapi board

Switch to the new VS Code window to run the agent session.
At session end, the agent commits team/atlas/ changes, pushes the PAT branch, and raises
a PR to podzoneTeam main. Hermes reviews and merges during /consolidate-tasks.
```

### Fissioned team mode
```
Session launched: athena-2026-05-01-curriculum-content

  Mode:             fissioned (home_repo: trainingTeam)
  Workspace:        ~/sessions/athena-2026-05-01-curriculum-content/session.code-workspace
  Task branch:      athena/2026-05-01-curriculum-content  (prompt-engineering-training)
  PAT branch:       session/athena-2026-05-01-curriculum-content  (trainingTeam)
  Worktrees:        prompt-engineering-training, trainingTeam
  podzoneTeam: ~/workspace/podzoneTeam  (plain clone, read-only)
  Registered:       planning.session (via register_session; id 9a1e…) — plannerapi board
                    (single podzone-planner DB; no separate fissioned-team tenant yet)

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
  Worktrees:        home-podzone-hephaestus (launch cwd), agent-tooling, podzoneTeam
  Materialise hook: resident (session-materialise.py, committed settings.json — no
                    settings.local.json wiring; the local block is env +
                    permissions.defaultMode only, T-132 gate passed)
  SessionEnd:       resident (session-end-finalise.py, C2-v2.1c)
  Registered:       planning.session (via register_session; id 3d5f…) — plannerapi board
  Mode:             headless (autonomous build — default; T-040)
  Verify:           materialise OK {'brief': 1, 'tasks': 50}

Run this in a standalone terminal (headless one-shot):

  cd ~/sessions/hephaestus-2026-06-25-proj039-t028/home-podzone-hephaestus && \
    claude --session-id 46a03b8e-c90c-4ce7-9f93-ae839a696b43 \
    -p "Hi Hephaestus. Continue with the brief. Commit and push all PRs when done. If anything needs operator direction you cannot resolve, raise it to Hermes with progress so far via your session response, and exit — do not wait."

  # interactive mode (team-lead / high-uncertainty) would instead be:
  #   cd ~/sessions/.../home-podzone-hephaestus && claude --session-id 46a03b8e-…

⚠️ Finish with a clean /exit — that fires the resident SessionEnd finalise (response +
rollup + telemetry + push-then-delete + brief-result PR). In headless mode `claude -p` exits
cleanly when the one-shot completes, which fires SessionEnd. At session end the agent's
`.workspace/` was materialised from Qdrant at SessionStart; there is no .code-workspace.
```

## Cleanup

**Serial simple-repo mode (default, T-045):** there is **nothing to reap** — no worktree, no
`~/sessions/{sid}/` dir. The SessionEnd finalise end-guard already returned each touched clone
to `main` and deleted its pushed session branch, and released the one-session lock;
`/consolidate-tasks` Step 0c is only the belt-and-suspenders sweep for a session that crashed
before finalise ran. Mark the session `cleaned-up` in `planning/sessions/active.md`.

**Trunk-mode repos (`lifecycle_mode: trunk`): even less to reap.** There was never a
worktree, `~/sessions/{sid}/` dir, *or* session branch — the session ran directly on `main`
throughout (Step 3 lifecycle-mode fork). The SessionEnd finalise commits the result and
`pull --rebase` + pushes to `main` directly; the belt-and-suspenders sweep of
`/consolidate-tasks` Step 0c applies only to `branch`-mode repos (there is no branch to
return-to-main on a trunk repo — the clone was already on `main` the whole time). Mark the
session `cleaned-up` in `planning/sessions/active.md` the same way.
