---
name: consolidate-tasks
description: Merge agent outbox status files into team-tasklist.md (Team Lead only)
---

This skill is for **Team Leads only** — Hermes (apex) or a fissioned team's own Team Lead.

At skill start, read the operator's identity YAML (same resolution chain as session-start).
Check `home_repo` and `role_class`:

- If `home_repo == podzoneTeam` (Hermes): run the **full mode** described below —
  cross-team scan including fissioned repo Step 1b. All steps apply.
- If `home_repo != podzoneTeam` AND `role_class` contains `team-lead`:
  run **local mode** — see the Local Mode section after Step 1b.
- If `role_class` does NOT contain `team-lead` and the operator is not the system-owner:
  refuse with: "consolidate-tasks is for Team Leads only. Raise a task proposal via
  your outbox and your Team Lead will consolidate during the next session."

Run at the start of a Hermes session (after reviewing incoming) or when agents have
completed sessions since the last consolidation.

## Step 0 — Sessions registry and podzoneTeam PR review

Read `planning/sessions/active.md`.

### 0a — Session status sync

For each session with status `in-flight`:
- Check whether the agent's outbox contains a session file dated on or after the launch date
- If a concluded outbox file exists: update status to `concluded` in `active.md`
- If no outbox file and session launched more than 2 days ago: flag to Martin as lost session

### 0b — podzoneTeam session PR review

For each session with status `concluded`, find the podzoneTeam session PR:
```bash
gh pr list --repo PodZonePlatformEngineering/podzoneTeam \
  --head session/{agent}-{YYYY-MM-DD}-{task-slug} --json number,state,title,files
```

For each open session PR:

**Apex-clone-on-main guard (PROJ-039/T-031) — assert BEFORE the push.** A migrated session
that took podzoneTeam as a write-target should have branched a worktree under
`~/sessions/{sid}/podzoneTeam`, never the shared primary clone. If the primary clone was
branched by mistake (the C2b defect, #102), `push origin main` below would push the wrong ref
or fail. Refuse to push unless the apex clone is on `main`:
```bash
APEX_BRANCH=$(git -C ~/workspace/podzoneTeam rev-parse --abbrev-ref HEAD)
if [ "$APEX_BRANCH" != "main" ]; then
  echo "ABORT: ~/workspace/podzoneTeam is on '$APEX_BRANCH', expected 'main'." >&2
  echo "A session branch was checked out in the primary clone (should be a worktree under" >&2
  echo "~/sessions/{sid}/podzoneTeam). Restore before consolidating:" >&2
  echo "  git -C ~/workspace/podzoneTeam checkout main" >&2
  exit 1
fi
```

**Push before diff — prevents Hermes unpushed commits appearing as agent changes:**
```bash
# Push any unpushed Hermes commits so gh pr diff uses the correct merge-base
git -C ~/workspace/podzoneTeam push origin main 2>/dev/null || true
```

**Structural check — diff must only touch permitted paths:**
```bash
gh pr diff {number} --name-only
```
- Permitted: `team/{agent}/outgoing/`, `team/{agent}/memory/`, `team/{agent}/incoming/`
- Permitted: `team/{other-agent}/incoming/drafts/` — cross-team handoff channel
  (see `agenticflows/operations/cross-team-handoff.md`)
- Violation: any file in `team/{other-agent}/` outside `drafts/` → flag to Martin; do not merge
- Violation: any edit to `planning/team-tasklist.md`, `planning/STATUS.md`, or
  `planning/sessions/active.md` from a non-Hermes session → flag to Martin; do not merge

**Content check:**
- Outbox file present (`team/{agent}/outgoing/session-{date}-*.md`)?
- Commit message matches `chore: session-close {operator}:{agent}...`?

**Outcome A** — checks pass: merge the PR:
```bash
gh pr merge {number} --merge --repo PodZonePlatformEngineering/podzoneTeam
```
Mark session `concluded-merged` in `active.md`.

**Outcome B** — path violation or missing outbox: flag to Martin; do not merge.

### 0c — Clone-on-main + local session-branch cleanup (PROJ-039/T-045)

**Serial simple-repo mode (default).** Sessions now run directly in the primary clone
(`~/workspace/{repo}`) on a session branch — there are no `~/sessions/{sid}/` worktrees
to reap. The session-end finalise guard already returns each touched clone to `main` and
deletes its pushed session branch; this step is the belt-and-suspenders sweep for a
session that crashed before finalise ran (its branch is still checked out).

For each session `concluded-merged` where all task-repo PRs are also merged, return any
clone still on that session's branch to a fast-forwarded `main` and drop the merged local
branch. The `session_guard` helper does the safe thing (skips a clone already on main,
never deletes an unpushed branch):
```bash
# per touched clone (home repo + task repos):
python3 ~/workspace/agent-tooling/lib/session_guard.py return-to-main \
  --repo ~/workspace/{repo} --branch session/{agent}-{YYYY-MM-DD}-{task-slug}
```
Any clone reported `returned-branch-kept-unpushed` (tip not on a remote) is an anomaly —
surface it to Martin rather than force-deleting. Mark the session `cleaned-up` in
`active.md`.

> **Legacy worktree option.** For a session launched with the retired
> `--legacy-worktree` flag (see `/launch-session`), fall back to the old reap —
> `git -C ~/workspace/{repo} worktree remove ~/sessions/{session-id}/{repo-name} --force`
> then `rmdir ~/sessions/{session-id}` if empty. Default (primary-clone) sessions never
> need this.

## Step 1 — Discover unprocessed outbox files

Scan all agent outboxes for session status files:

```bash
ls team/*/outgoing/session-*.md 2>/dev/null
```

This covers all current podzoneTeam agents (Hermes, Hephaestus, Atlas, Thoth) plus
any fissioned-team stub agents (Clio, Alex, Norma, Eben) whose stubs land here.

### Step 1a — Migrated home-repo scan (PROJ-039/T-011 C2, T-007)

Agents listed as `status: migrated` in
`planning/projects/PROJ-032-agent-home-repos/migrated-agents.md` no longer write
`team/{agent}/outgoing/` — their session results live in their home repo's `results/`.
For each migrated agent, scan the home repo instead of (not in addition to) the legacy path:

```bash
# per migrated agent, from the home-repo clone (origin/main):
git -C ~/workspace/{home_repo} show origin/main:results/ 2>/dev/null   # list session results
# or, if a session PR is open on the home repo:
gh pr list --repo PodZonePlatformEngineering/{home_repo} --search "session:" --json number,title
```

Treat each `results/session-{date}-{slug}-{sid}.md` exactly as a legacy outbox file for parsing
(Step 2 onwards). **Coexistence:** a migrated agent is scanned via its home repo *only* —
do not double-count any residual `team/{agent}/outgoing/` file for that agent. Agents not
in the registry are scanned the legacy way, unchanged.

List the files found (legacy + migrated). If none, print "No pending outbox files." and stop.

For each file, note: agent, date, and whether it appears already processed
(look for a `<!-- consolidated -->` comment at the top).

## Step 1b — Fissioned team repo scan

Fissioned teams maintain their own repos with their own outboxes. Scan each team's
local path using this config table (update as new teams are stood up):

| Team | Local path | Outbox agents | Tasklist | Session PR repo |
|---|---|---|---|---|
| trainingTeam | ~/workspace/trainingTeam | athena, hestia | planning/team-tasklist.md | PodZonePlatformEngineering/trainingTeam |
| roadmapTeam | ~/workspace/roadmapTeam | clio, kronos | (none yet — flag to Hermes) | PodZonePlatformEngineering/roadmapTeam |

For each team in the table:

```bash
ls {local_path}/team/*/outgoing/session-*.md 2>/dev/null
```

**Stub deduplication rule:** Some fissioned-team agents (e.g. Clio) also write a stub
file to `podzoneTeam/team/{agent}/outgoing/` for visibility. If a stub exists in
podzoneTeam for the same agent/date/slug as a full file found in the fissioned repo,
prefer the full file and skip the stub. Mark the stub as superseded in the Step 6 report.

If a fissioned team's local path does not exist or is unreachable, flag it in the
Step 6 report and continue.

## Local Mode (fissioned Team Lead)

Activated when `home_repo != podzoneTeam` and `role_class` contains `team-lead`.
The fissioned Team Lead consolidates their own team without depending on Hermes.

### Resolve the TEAM REPO first — `home_repo` may NOT be the team repo (PROJ-039/T-038)

**Load-bearing for a migrated team lead.** Local mode used to assume the current repo
*was* the team repo (true for the legacy fissioned layout where `home_repo == trainingTeam`).
A **migrated** team lead's `home_repo` is `home-<team>-<agent>` (e.g. `home-training-athena`)
— a hooks+coordination-skills home repo that is **not** the team repo and has no
`team/*/outgoing/`, `planning/team-tasklist.md`, or `planning/STATUS.md`. Before any step,
resolve the separate team repo from identity:

```bash
# canonical resolver (agent-tooling cloned to .workspace/ on demand):
python3 .workspace/agent-tooling/lib/team_repo.py --home-repo "$HOME_REPO" --json
# equivalently, decode by hand: home-<team>-<agent> -> <team>Team
#   home-training-athena -> trainingTeam ; home-roadmap-X -> roadmapTeam
```

Use the resolved values for every local-mode step below — call the team repo's checkout
`{team_repo}` (e.g. `~/workspace/trainingTeam`) and its GitHub repo `{github_repo}`
(e.g. `PodZonePlatformEngineering/trainingTeam`). Clone `{team_repo}` into `.workspace/`
if it is not already present. **Never** read/write the home repo for tasklist/STATUS/outbox
— those live in `{team_repo}`. (Legacy fissioned lead: `{team_repo}` == `home_repo`, so the
table below is unchanged for them.)

**Step substitutions for local mode** ("own repo" = the resolved `{team_repo}`, which for a
migrated lead is a DIFFERENT checkout from `home_repo`):

| Full mode step | Local mode behaviour |
|---|---|
| Step 0 — sessions registry + podzoneTeam PR review | **Skip** — fissioned session PRs are in `{team_repo}` (handled in Step 0b equivalent below) |
| Step 1 — podzoneTeam outbox scan | **Skip** — not applicable |
| Step 1b — fissioned repo scan | **Run for `{team_repo}` only**: `ls {team_repo}/team/*/outgoing/session-*.md 2>/dev/null` (plus the migrated-home-repo `results/` scan of Step 1a for any migrated team members) |
| Step 2 — read and parse | Same as full mode |
| Step 2b — PR review | Look up session PRs in `{github_repo}` (e.g. `PodZonePlatformEngineering/trainingTeam`) |
| Step 2c — drafts reconciliation | Scan `{team_repo}/team/*/incoming/drafts/*.md` only |
| Step 3 — apply to tasklist | Update `{team_repo}/planning/team-tasklist.md`, not podzoneTeam's and not the home repo's |
| Step 4 — update STATUS.md | Update `{team_repo}/planning/STATUS.md` |
| Step 5 — mark outbox files | Write `<!-- consolidated YYYY-MM-DD -->` in `{team_repo}` outbox files |
| Step 6 — report | Same format with header `[LOCAL MODE — {TeamName}]` |

**Optional upward-sync step (after Step 5):**
If cross-team tasks or apex programme changes surfaced during consolidation (e.g. a task
that affects podzoneTeam agents, a blocker requiring Martin, or a new decision),
write a draft to the plain podzoneTeam clone:
`~/workspace/podzoneTeam/team/hermes/incoming/drafts/{date}-{team}-sync.md`

Include only meaningful upward content — do not write a sync draft if there is nothing
new for the apex.

**Local mode Step 0b equivalent — fissioned session PR review:**

Session PRs for the team's own work live in the **team repo** (`{github_repo}`), not the
lead's home repo. For a migrated team member the session-result PR lands on that member's
*home* repo (`PodZonePlatformEngineering/home-<team>-<member>`) — review those via the
Step 1a migrated scan. The team-repo session PRs:

```bash
gh pr list --repo {github_repo} \
  --head session/{agent}-{YYYY-MM-DD}-{task-slug} --json number,state,title,files
```

Apply the same structural check (permitted paths: `team/{agent}/`) and merge on pass.

## Step 2 — Read and parse each outbox file

For each unprocessed file, extract:

- **Completed** tasks — `programme:project:task-slug` + suggested status + PR link
- **Started / In Progress** — `programme:project:task-slug` + current state + spec link
- **New blockers** — `programme:project:task-slug` + description
- **Decisions** — for KEY DECISIONS section in STATUS.md
- **PRs Raised** — repo#number with GitHub URL
- **Questions for Martin** — surface these explicitly

Use semantic names throughout — no raw IDs in the report.
See `agenticflows/operations/task-naming.md` for the full programme:project mapping.

### Fissioned team divergence rules

When the outbox being parsed belongs to a fissioned team agent (i.e. the file came from
a fissioned repo path in Step 1b), the downstream steps differ from the standard flow:

- **Step 3:** Update `{fissioned_team_path}/planning/team-tasklist.md`, NOT
  podzoneTeam's `planning/team-tasklist.md`.
  - If the fissioned team has no `planning/team-tasklist.md`, list all tasks in the
    Step 6 report and flag to Hermes. Do not create the file.
- **Step 4:** Do NOT write fissioned team task details into podzoneTeam `STATUS.md`.
  Add one summary line per team only:
  `{TeamName}: N tasks consolidated — see {team}/planning/team-tasklist.md`
- **Step 5:** Write `<!-- consolidated YYYY-MM-DD -->` into the outbox file in the
  fissioned repo (not in podzoneTeam).

## Step 2b — Structural PR Review

For each PR listed in `## PRs Raised` across all outbox files:

```bash
gh pr view {repo}#{number} --json state,title,headRefName,commits,statusCheckRollup
```

**Fissioned team session PRs:** When checking the session PR (not task-repo PRs) for a
fissioned team agent, look it up in the repo from the Step 1b config table
(e.g. `PodZonePlatformEngineering/trainingTeam`), not `podzoneTeam`.

**Check 1 — PR exists and is open** (state == `OPEN`):
- If `MERGED`: already done — note as complete, skip further checks
- If `CLOSED` (not merged): flag to Martin — work may be lost
- If not found: flag as missing PR — route back to agent

**Check 2 — Commits match the task**:
- Read the commit messages from the `commits` field
- Confirm they reference the task slug, CC number, or describe the expected work
- If commit messages are unrelated or clearly wrong: Outcome B

**Check 3 — CI status**:
- Read `statusCheckRollup` — look for any `FAILURE` or `PENDING` state
- CI failing → Outcome C; CI pending → note but do not block

**Outcomes:**

- **Outcome A** — PR open, commits match task, CI passing (or no CI):
  Mark `⬆️ Ready for approval` in the consolidation report.
  Martin reviews in GitHub and approves/merges.

- **Outcome B** — PR missing, commits don't match, or PR abandoned:
  Create `team/{agent}/incoming/{date}-pr-rework-{slug}.md` with the specific gap noted.
  Add a blocker entry to the task in team-tasklist.md.

- **Outcome C** — CI failing or merge conflict:
  Add a blocker entry to the task. Do not route back to agent until CI is diagnosed.
  Note the failure in the consolidation report.

## Step 2c — Drafts reconciliation

Source agents raise cross-team work via `team/{recipient}/incoming/drafts/*.md`
(see `agenticflows/operations/cross-team-handoff.md`). This step promotes those drafts
into formal briefs + tasklist rows.

### Discover drafts

```bash
ls team/*/incoming/drafts/*.md 2>/dev/null | grep -v README.md
```

For each draft, parse the frontmatter (`**From:**`, `**To:**`, `**Proposed task:**`,
`**Urgency:**`). If the draft pre-dates this protocol and lacks the frontmatter,
read the first paragraph and infer.

### Decide per draft

For each draft, choose one of three outcomes:

**Promote** — new task for the recipient:
1. Assign a task ID (next available T-xxx in the relevant project) and CC number
   (next available CC-xxx — see STATUS.md counter line).
2. Rename the file: `team/{recipient}/incoming/drafts/{date}-{slug}.md` →
   `team/{recipient}/incoming/{date}-{slug}.md`.
3. Edit the promoted file: strip the `DRAFT —` / `Draft Brief:` prefix, remove the
   `## Not authorised` block, retain context + asks.
4. Add a row to `planning/team-tasklist.md` in the recipient's project section
   (status `🚀 Ready` unless draft flags a dependency).
5. Update the CC counter in STATUS.md.

**Merge** — draft aligns with an existing pending task:
1. Append the draft's context as an addendum to the existing brief at
   `team/{recipient}/incoming/{existing-brief}.md`.
2. Update the existing tasklist row (scope note) if needed.
3. Delete the draft file.

**Reject** — out of scope, duplicate, or stale:
1. Move the draft to `team/hermes/incoming/rejected/{date}-{slug}.md` with a
   one-paragraph note explaining why. Never silently delete.

**Applied-via-existing-task** — work already complete or absorbed:
1. Trigger: draft header carries `<!-- applied YYYY-MM-DD —` marker, OR the draft's
   proposed work is already complete on the tasklist (✅) at consolidation time.
2. Move the draft to `team/hermes/incoming/applied/{date}-{slug}.md` (create the
   `applied/` subfolder if missing). Preserve the file untouched for the audit
   trail — do not strip headers or edit content.
3. Note the disposition in the Step 8 report under `Drafts reconciled` with
   reason `→ applied (already complete)`.

**The `applied/` and `rejected/` subfolders MUST be excluded from the drafts scan**
in this step and from `/session-start` outbox reconciliation. The `ls` glob above
targets `incoming/drafts/*.md` directly (not recursive), so the subfolders are
naturally excluded — but any future change must preserve that exclusion.

### Mark source outboxes reconciled

For each outbox file that produced a draft, add `<!-- drafts reconciled YYYY-MM-DD -->`
just after the `<!-- consolidated YYYY-MM-DD -->` marker, so the Cross-team handoff
section is not re-processed.

### Negative-affirmation check

For every outbox processed in Step 2, verify the `## Cross-team handoff` section
contains `### Tasklist edits made this session` with `(none)` (or an explicit
explanation referencing this protocol). Missing or contradicting this line is a
protocol violation:

- Cross-check: did the session PR diff include `planning/team-tasklist.md` or
  `planning/STATUS.md` changes?
- If yes AND agent is not Hermes: flag to Martin. The PR has already been
  structurally reviewed in Step 0b, so this is a defence-in-depth check.

## Step 3 — Apply to team-tasklist.md

For each completed/started/blocked task:

1. Find the matching row by project + task ID or slug
2. Update the Status cell only (do not rewrite summaries)
3. Append completion date where appropriate (`✅ Complete YYYY-MM-DD`)
4. For new blockers not yet in the tasklist, add a blocker note to the task row

**Rules:**
- Apply changes chronologically (oldest file first)
- If a task cannot be found by slug, flag it — do not guess
- Do not delete tasks

## Step 3b — STATUS.md `### Martin` block reaper

Before rewriting STATUS.md, sweep the existing `### Martin` block for stale items
where the underlying artefact is now resolved. Verify each line that names a
checkable artefact and strip the resolved ones.

Apply this table:

| Reference pattern | Verification command | Action if resolved |
|---|---|---|
| `{repo}#{n}` PR ref | `gh pr view {n} -R PodZonePlatformEngineering/{repo} --json state` | Strip line if `state` is `MERGED` or `CLOSED` |
| `v{x.y.z}` tag ref | `gh release view {tag} -R PodZonePlatformEngineering/{repo}` (fallback: `git tag -l {tag}` in the repo) | Strip line if the tag exists in the PPE repo |
| `transfer repos to ... org` (PROJ-028 close-out) | `gh repo list PodZonePlatformEngineering --limit 50` | Strip line if expected repo present |
| Free-text decision (e.g. "Option A or B") | No automated check | Leave as-is; if older than 30 days, surface for explicit triage in the Step 8 report |

**Failure handling:** `gh pr view` on a missing PR number returns non-zero — treat
"not found" as a strip-and-log condition (the PR ref was likely typo'd or the repo
was renamed; either way the item is no longer actionable).

**Verbosity:** Make the reaper output verbose for the first 3 consolidation runs
so misfires are visible. Surface every strip in the Step 8 report with the
artefact age (e.g. "merged 41 d ago"). Silence after the protocol is stable.

**Apex-only:** This step runs in full mode (Hermes consolidating podzoneTeam).
Fissioned teams typically have no `### Martin` block in their own STATUS.md — if
the block is absent in local mode, skip this step silently.

## Step 4 — Update STATUS.md

Rewrite `planning/STATUS.md` incorporating decisions and blocker changes from all
processed outbox files. Follow session-end STATUS.md format rules.

## Step 5 — Mark outbox files processed

Add `<!-- consolidated YYYY-MM-DD -->` as the first line of each processed file
to prevent double-processing in future passes.

## Step 7 — Refresh sessions collection (gap-fill)

After outbox files are marked processed, refresh the cloud Qdrant `sessions`
collection so the Team Lead has current per-workspace usage data when writing
the consolidation report.

Run the existing backfill walker — it is idempotent and applies the §D6 status
rules automatically (`in_progress` < 30 min, `idle` < 6 h, `ended` else):

```bash
python3 ~/workspace/agent-tooling/tools/backfill-sessions.py 2>&1 | tee /tmp/gapfill-output.txt
```

Capture the output's top-line summary for the Step 8 report. Specifically:

- Total `Upserted` count
- Per-workspace token totals (raw counts, no dollar values — Phase 1)
- Any multi-agent attribution warnings

If `backfill-sessions.py` is not on disk (agent-tooling not present): skip
silently and note in Step 8 that the `sessions` collection was not refreshed
this pass.

Sessions whose JSONL mtime is > 6 h old will transition to `status: ended`
on this run if they were still flagged `in_progress` from the Stop hook
(window-close case — the gap this step closes).

## Step 7b — Generate usage summary

After the gap-fill, render a 7-day usage summary so the consolidation report
carries current workspace/model/outlier figures. Step 0 of the tool runs a
zombie-cleanup pass against the `sessions` collection (pre-T-005 heartbeat-only
points missing `data_source`) — leave it on.

```bash
python3 ~/workspace/agent-tooling/tools/usage-report.py --days 7 2>&1 | tee /tmp/usage-report-output.txt
```

Capture the 6-line digest from stdout for the Step 8 report. The markdown
file is written to `team/hermes/outgoing/usage-reports/{today}-usage-summary.md`
(same-day overwrite — last consolidation of the day wins).

If `usage-report.py` is not on disk or Qdrant is unreachable: skip silently
and note in Step 8 that no usage summary was generated this pass.

## Step 7c — Tooling-drift check (PROJ-039/T-057)

Render the fleet tooling-version table so the consolidation report surfaces drift
against canonical `VERSION`. Reads each home repo `main`'s shipped
`.claude/tooling-manifest.json` via `gh api` raw-content — apex stays read-only to
agents; this derived view is the only catalog write path (Hermes-side).

```bash
python3 ~/workspace/agent-tooling/tools/tooling-drift-report.py \
  --migrated-agents-path planning/projects/PROJ-032-agent-home-repos/migrated-agents.md \
  2>&1 | tee /tmp/tooling-drift-output.txt
```

Capture the counts (N current / N drifted / N flagged) for the Step 8 report, and
refresh the `tooling_version` column in
`planning/projects/PROJ-032-agent-home-repos/migrated-agents.md` from the table
(a `--json` run gives machine-readable values). A drifted or flagged repo is not an
error to fix here — it is a dispatch signal: the repo picks up canonical on its next
brief via `TOOLING_UPDATE` (T-056) or a T-034 sweep task.

If the tool is not on disk or `gh` is unavailable: skip silently and note in Step 8
that no drift check ran this pass.

## Step 8 — Report

```
Sessions registry: N in-flight, N concluded, N cleaned-up
  ⚠️  Lost session (no outbox): {session-id}   ← if applicable

Sessions refreshed (Step 7):
  Upserted: {N}  ({M} newly transitioned to status: ended)
  Per-workspace totals: see /tmp/gapfill-output.txt

Usage summary — last 7 days (Step 7b):
  {paste the 6-line digest from /tmp/usage-report-output.txt verbatim}
  Report: team/hermes/outgoing/usage-reports/{today}-usage-summary.md

Tooling drift (Step 7c):
  Canonical vX.Y.Z — {N} current, {N} drifted, {N} flagged (see /tmp/tooling-drift-output.txt)
  Registry column refreshed: planning/projects/PROJ-032-agent-home-repos/migrated-agents.md

Consolidated: N files ({agent} {date}, ...)

Changes applied:
  {programme}:{project}:{task-slug} ✅ Complete
    PR: {repo}#{number} — {title}
  {programme}:{project}:{task-slug} 🔄 still in progress

Drafts reconciled (N):
  {source} → {recipient}: {slug} → promoted as {programme}:{project}:{task-slug}
  {source} → {recipient}: {slug} → merged into existing brief
  {source} → {recipient}: {slug} → rejected ({reason})
  {source} → {recipient}: {slug} → applied (already complete) — preserved in team/hermes/incoming/applied/

### Martin block reaper (Step 3b):
  Stripped: {ref} ({state} {YYYY-MM-DD}) — was {N} d old
  Surfaced for triage: {ref} — {N} d old, no automated check

PRs for Martin approval (N):
  ⬆️  {repo}#{number} — {title} ({agent})
     Task: {programme}:{project}:{task-slug}
     Spec: {link if relevant}

PR issues routed back (N):
  {agent}: {programme}:{project}:{task-slug} — {gap description}

Protocol violations (N):
  {agent}: {what} — {outbox ref}   ← e.g. tasklist edit detected in session PR

Questions for Martin (N):
  {agent}: {question}
    Context: {brief or spec link}

Fissioned teams consolidated (N):
  trainingTeam: N outboxes — N tasks updated in trainingTeam/planning/team-tasklist.md
  roadmapTeam: N outboxes — no tasklist; tasks listed:
    {agent}: {task-slug} — {status}
  ⚠️  {team}: local path unreachable — skipped   ← if applicable
  ℹ️  Stubs superseded (N): {agent}/{date}/{slug} — full file used from {team} repo

STATUS.md updated.
```