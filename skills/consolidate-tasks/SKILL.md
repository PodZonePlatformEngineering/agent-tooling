---
name: consolidate-tasks
description: Review concluded planning.session rows and apply task-status/decision changes (Team Lead only)
---

This skill is for **Team Leads only** — Hermes (apex) or a fissioned team's own Team Lead.

At skill start, read the operator's identity YAML (same resolution chain as session-start).
Check `home_repo` and `role_class`:

- If `home_repo == podzoneTeam` (Hermes): run the **full mode** described below.
- If `home_repo != podzoneTeam` AND `role_class` contains `team-lead`:
  see the Local Mode section — **currently frozen** (PROJ-029/T-019): no fissioned team has
  its own `planning.*` tenant today, so DB-backed consolidation has nothing to consolidate
  against for one. Falls back to the legacy markdown flow if a fissioned team is ever stood
  up again — see that section for detail.
- If `role_class` does NOT contain `team-lead` and the operator is not the system-owner:
  refuse with: "consolidate-tasks is for Team Leads only. Raise a task proposal via
  your outbox and your Team Lead will consolidate during the next session."

Run at the start of a Hermes session (after reviewing incoming) or when sessions have
concluded since the last consolidation.

> **PROJ-029/T-019 (Phase 4b) — this skill now reads/writes `planning.*` instead of
> `team-tasklist.md`/`planning/sessions/active.md`/`planning/STATUS.md`.** The board and
> session registry moved to Neon (spec-v2-neon-primary.md); this rewrite is the second of the
> two major fleet skills spec §12 Phase 4 named (after `/launch-session`, T-018). See
> `podzoneTeam/planning/projects/PROJ-029-plannerapi/t019-consolidate-tasks-scoping.md` for
> the full design rationale and the two operator-ruled forks this rewrite implements:
> **task-status authority stays with the Team Lead** (no dispatched session gets direct
> `close_task`/`supersede_task`/`conclude_session` authority — those are Team-Lead-only calls,
> made here, after reading what a session actually did) and **the agent "outbox" narrative
> fully collapses into `session.outcome_note`** (no separate write path, no file).

## Step 0a — Sessions ready for consolidation

Replaces the old `active.md` read + Step 0a cross-referencing entirely: `planning.session`
**is** the live truth of what happened (set by `launch.sh`'s own finalise —
`conclude_session(..., p_task_status => 'ready_for_review')`, unconditionally, at every
exit path — see `/launch-session`'s brief-first section and T-019 Fork 1). There is no
outbox file to scan, no "did the session actually finish" inference to make.

Query for sessions this consolidation pass should review:

```bash
mcp__secrets__secret_run -k podzone_qdrant_apikey -- python3 -c "
import sys; sys.path.insert(0, '$HOME/workspace/agent-tooling')
from lib import planning_mirror
conn = planning_mirror.connect()
with conn.cursor() as cur:
    cur.execute('''
        SELECT id, brief_id, agent, task_ids, home_repo, repos_touched,
               launched_at, outcome_note
        FROM planning.session
        WHERE status = 'concluded'
        ORDER BY launched_at
    ''')
    for row in cur.fetchall():
        print(row)
conn.close()
"
```

If none, print "No sessions ready for consolidation." and skip to Step 6.

**`PLANNING_DATABASE_URL` prerequisite**: same operational posture as
`lib/planning_mirror.py`'s own docstring — provision the `planning_automation` service
role's connection string (PROJ-029/T-021) into the session env before running any query in
this skill.

## Step 0b — podzoneTeam session PR review

For a `launch.sh` dispatch, this PR-review loop runs once **per working repo** named in
the brief's `--repos` list, not just against podzoneTeam — each working repo gets its own
`{assignee}/{brief-slug}` PR (pre-created by `launch.sh` Phase 1), and the home repo has
no PR at all (see the Step 0c callout above).

For each session found in Step 0a, find its working-repo PR(s) — already known from
`planning.task.pr_refs` (populated by `conclude_session`'s own `p_pr_refs` handling, no
`gh pr list` guesswork needed for *discovery*; `gh pr view` below is still needed for the
actual state/CI checks):

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
- Violation: a dispatched (non-Team-Lead) session's PR diff calls any `planning.*` write RPC
  directly — reuse `hooks/planning-postwrite-mirror.py`'s own detection regex
  (`planning\.(close_task|supersede_task|register_session|conclude_session)\s*\(`) against
  the diff content. This is the T-019 Fork 1 authority boundary made concrete: those calls
  are Team-Lead-only, made from *this* skill, never from inside a dispatched session's own
  code. → flag to Martin; do not merge. (Supersedes the old markdown-edit violation check —
  nothing writes `team-tasklist.md`/`STATUS.md`/`active.md` any more, so there is nothing
  left there to protect.)

**Content check:**
- `session.outcome_note` non-empty (Step 2's ground-truth check covers the empty case)?
- Commit message matches `chore: session-close {operator}:{agent}...` (legacy path) or
  `wip: {brief-id} attempt {n}` (`launch.sh` path)?

**Outcome A** — checks pass: merge the PR:
```bash
gh pr merge {number} --merge --repo PodZonePlatformEngineering/podzoneTeam
```

**Outcome B** — path violation or missing content: flag to Martin; do not merge.

## Step 0c — Clone-on-main + local session-branch cleanup (PROJ-039/T-045)

Unchanged — pure git hygiene, orthogonal to where the task board lives.

**Lifecycle-mode fork (PROJ-039/T-084..T-086, codified T-097).** This step is
`branch`-mode-only. Read `lifecycle_mode` from the repo's `.claude/tooling-manifest.json`
(reader: `lib/lifecycle_mode.py`; absent = `branch`, same reader `/launch-session` uses):

- **`branch` (default)** — the sweep below runs as written.

  > **`launch.sh`-dispatched briefs use a different branch shape (T-108/T-210/T-212).**
  > A `launch.sh`-dispatched brief uses a different branch shape than the manual
  > `/launch-session` ceremony: the **home repo is never branched** (it stays on `main`
  > throughout, `launch.sh` pushes its own commits there directly), and each **working
  > repo** is branched `{assignee}/{brief-slug}` (no `session/` prefix, no date segment)
  > — check for a PR under that name on each working repo named in `--repos`, not a
  > `session/…` PAT branch on podzoneTeam. `return-to-main` still applies to any working
  > repo left on that branch after its PR merges.
- **`trunk`** — **skip this step for that repo.** A trunk-mode session never created a
  session branch, so there is nothing for `session_guard.return-to-main` to return.

**Serial simple-repo mode (default, `branch`-mode repos).** Sessions run directly in the
primary clone (`~/workspace/{repo}`) on a session branch — there are no `~/sessions/{sid}/`
worktrees to reap. The session-end finalise guard already returns each touched clone to
`main` and deletes its pushed session branch; this step is the belt-and-suspenders sweep
for a session that crashed before finalise ran (its branch is still checked out).

For each session whose task-repo PRs are all merged, return any clone still on that
session's branch to a fast-forwarded `main` and drop the merged local branch:
```bash
# per touched clone (home repo + task repos):
python3 ~/workspace/agent-tooling/lib/session_guard.py return-to-main \
  --repo ~/workspace/{repo} --branch session/{agent}-{YYYY-MM-DD}-{task-slug}
```
Any clone reported `returned-branch-kept-unpushed` (tip not on a remote) is an anomaly —
surface it to Martin rather than force-deleting.

> **Legacy worktree option.** For a session launched with the retired
> `--legacy-worktree` flag, fall back to the old reap —
> `git -C ~/workspace/{repo} worktree remove ~/sessions/{session-id}/{repo-name} --force`
> then `rmdir ~/sessions/{session-id}` if empty. Default (primary-clone) sessions never
> need this.

## Step 2 — Read and extract from each session's `outcome_note`

For each session from Step 0a, read `outcome_note` (already fetched in the Step 0a query)
and extract:

- **Completed** tasks — `programme:project:task-slug` + suggested status + PR link
- **Started / In Progress** — `programme:project:task-slug` + current state
- **New blockers** — `programme:project:task-slug` + description
- **Decisions** — for a `provenance` `type: decision` point (Step 4)
- **Questions for Martin** — surface these explicitly, written as a `provenance`
  `type: question` point (see Step 4)

Use semantic names throughout — no raw IDs in the report. See
`agenticflows/operations/task-naming.md` for the full programme:project mapping.

**Empty `outcome_note` ⇒ ground-truth verify (F7, the board-lie class).** If a session's
`outcome_note` is empty or null, do **not** accept that at face value as "nothing happened
this session" — the same failure mode the old outbox-based check guarded against (a rich
response that failed to extract, finalise falling back to a stub) can still happen here;
storage backend changed, the risk didn't. Before treating the session as a genuine no-op,
run a ground-truth check against the session's actual repo state:

```bash
gh pr list --repo PodZonePlatformEngineering/{repo} --head {agent}/{date}-{slug} --json number,state,title
gh api repos/PodZonePlatformEngineering/{repo}/commits --jq '.[0].commit.message' 2>/dev/null
```

If a PR or commits exist that an empty `outcome_note` gave no account of, flag it in the
Step 7 report under Protocol violations and route back rather than silently marking the
session unchanged.

### Fissioned team divergence rules

Local Mode is frozen (see header note) — this section does not apply while no fissioned
team has its own `planning.*` tenant.

## Step 2b — Structural PR Review

> **Change-visibility policy cross-reference (OPERATING-MANUAL §2b, 2026-07-10).** Not
> every completed task produces a PR to review here — a home repo's own session (the
> agent's `results/` commit) and, for `trunk`-mode repos, the session's task-repo work
> too, can land as a **direct commit on `origin/main`**, no PR, by design. Before treating
> a task's `pr_refs` as incomplete, check whether the task-repo/home-repo pair is actually
> a home-or-team-repo-on-itself case the policy routes direct-to-main rather than the
> working-repo PR path it governs.

For each PR in the `pr_refs` collected across Step 0a's sessions:

```bash
gh pr view {repo}#{number} --json state,title,headRefName,commits,statusCheckRollup
```

**Check 1 — PR exists and is open** (state == `OPEN`):
- If `MERGED`: already done — note as complete, skip further checks
- If `CLOSED` (not merged): flag to Martin — work may be lost
- If not found: flag as missing PR — route back to agent

**Check 2 — Commits match the task**:
- Read the commit messages from the `commits` field
- Confirm they reference the task slug, ref, or describe the expected work
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
  Leave the task in `ready_for_review` — do not close it (Step 3).

- **Outcome C** — CI failing or merge conflict:
  Note the failure in the consolidation report. Do not close the task (Step 3) until
  CI is diagnosed.

## Step 2c — Drafts reconciliation

Unchanged in mechanism (a cross-team *proposal* channel, upstream of the board — see the
T-019 scoping doc's Fork 2 discussion for why this stays markdown-based rather than moving
into `planning.*`). Source agents raise cross-team work via
`team/{recipient}/incoming/drafts/*.md` (see `agenticflows/operations/cross-team-handoff.md`).
This step promotes those drafts into formal briefs + task rows.

### Discover drafts

```bash
ls team/*/incoming/drafts/*.md 2>/dev/null | grep -v README.md
```

For each draft, parse the frontmatter (`**From:**`, `**To:**`, `**Proposed task:**`,
`**Urgency:**`). If the draft pre-dates this protocol and lacks the frontmatter,
read the first paragraph and infer.

### Decide per draft

For each draft, choose one of three outcomes:

**Promote** — new task for the recipient. **RPC-backed now (T-020's `create_task`, live
since 2026-08-09)** — no more manual ref/CC-counter allocation, no `team-tasklist.md` row:
1. Resolve the recipient's project id, then:
   ```bash
   mcp__secrets__secret_run -k podzone_qdrant_apikey -- python3 -c "
   import sys; sys.path.insert(0, '$HOME/workspace/agent-tooling')
   from lib import planning_mirror
   conn = planning_mirror.connect()
   result = planning_mirror.call_rpc(conn, 'create_task', {
       'project_id': '{project-uuid}',
       'title': '{title from draft}',
       'summary': '{context + asks from draft}',
       'owner': '{recipient}',
       'status': 'ready',
   })
   print(result)
   conn.close()
   "
   ```
2. Rename the file: `team/{recipient}/incoming/drafts/{date}-{slug}.md` →
   `team/{recipient}/incoming/{date}-{slug}.md`.
3. Edit the promoted file: strip the `DRAFT —` / `Draft Brief:` prefix, remove the
   `## Not authorised` block, retain context + asks. Reference the new task's `ref`
   (returned by `create_task`) in the file so a reader can find the board row.

**Merge** — draft aligns with an existing pending task:
1. Append the draft's context as an addendum to the existing brief at
   `team/{recipient}/incoming/{existing-brief}.md`.
2. If the addendum changes scope, update the task's `summary` via a direct
   `UPDATE planning.task SET summary = ...` is **not** available as an RPC — flag to Martin
   rather than hand-editing the row; this is a known gap, not yet decided whether summary
   edits get their own RPC or stay Team-Lead-via-Neon-MCP.
3. Delete the draft file.

**Reject** — out of scope, duplicate, or stale:
1. Move the draft to `team/hermes/incoming/rejected/{date}-{slug}.md` with a
   one-paragraph note explaining why. Never silently delete.

**Applied-via-existing-task** — work already complete or absorbed:
1. Trigger: draft header carries `<!-- applied YYYY-MM-DD —` marker, OR the draft's
   proposed work already has a `complete`/`closed` task row at consolidation time.
2. Move the draft to `team/hermes/incoming/applied/{date}-{slug}.md` (create the
   `applied/` subfolder if missing). Preserve the file untouched for the audit
   trail — do not strip headers or edit content.
3. Note the disposition in the Step 7 report under `Drafts reconciled` with
   reason `→ applied (already complete)`.

**The `applied/` and `rejected/` subfolders MUST be excluded from the drafts scan**
in this step and from `/session-start` outbox reconciliation. The `ls` glob above
targets `incoming/drafts/*.md` directly (not recursive), so the subfolders are
naturally excluded — but any future change must preserve that exclusion.

### Negative-affirmation check

For every session processed in Step 2, verify its PR diff shows no direct edit to
`planning.*` state outside the sanctioned RPC calls this skill itself makes (same check
as Step 0b's structural review — this is defence-in-depth, not a new mechanism).

## Step 3 — Apply task-status changes

For each completed/started/blocked task surfaced in Step 2, call the matching RPC directly
— no row to find by slug, no Status cell to edit, no ref-collision class of bug to hit
(T-013, T-020a-n, T-079, T-010 all cannot recur once there is no markdown row to corrupt):

```bash
mcp__secrets__secret_run -k podzone_qdrant_apikey -- python3 -c "
import sys; sys.path.insert(0, '$HOME/workspace/agent-tooling')
from lib import planning_mirror
conn = planning_mirror.connect()
planning_mirror.call_rpc(conn, 'close_task', {
    'task_id': '{task-uuid}', 'reason': '{one-line reason}', 'status': 'complete',
})
conn.close()
"
```

For a superseded task, use `supersede_task` (`task_id`, `superseded_by`, `reason`) instead.
For the session(s) that closed against reviewed-and-merged work, finish the authoritative
close with `conclude_session(status='cleaned_up', task_status='complete', outcome_note=...)`
— **this is the one place in the whole fleet that calls `conclude_session` with
`p_status='cleaned_up'`**; `launch.sh`'s own finalise only ever uses `'concluded'`
(T-019 Fork 1's authority boundary, made mechanical):

```bash
python3 ~/workspace/agent-tooling/tools/conclude-planning-session.py \
  --brief-id "{brief_id}" --status cleaned_up --task-status complete \
  --outcome-note "{final reviewed note, may restate/trim the session's own outcome_note}"
```

**Rules (unchanged in spirit from the markdown version):**
- Apply changes chronologically (oldest session first)
- If a task referenced in `outcome_note` can't be resolved to a real `planning.task.id`
  (typo'd ref, task never created), flag it — do not guess
- Never delete a task row — `close_task`/`supersede_task` are the only status-change paths

## Step 4 — Provenance writes (decisions + questions)

Replaces the `STATUS.md` rewrite entirely (T-013's original `provenance`-collection design,
not new scope for this rewrite). For each Decision extracted in Step 2:

```bash
mcp__secrets__secret_run -k podzone_qdrant_apikey -- python3 -c "
import sys; sys.path.insert(0, '$HOME/workspace/agent-tooling')
from lib import provenance_substrate
provenance_substrate.upsert_provenance(
    team_slug='podzone-apex', type='decision', slug='{short-slug}',
    title='{decision title}', body='{full decision text}', agent='hermes',
    tags=['{project-ref}'], linked_ref='{PROJ-XXX/T-YYY if applicable}',
)
"
```

**Questions for Martin — `type: question`, Team-Lead-authored (T-019 Fork 1 addendum,
operator: "Questions for the operator are raised by the team lead").** Not a separate
agent write path — this skill reads a session's `outcome_note`, extracts anything the
session flagged, and *the Team Lead* (this skill, running as Hermes) writes the resulting
point:

```bash
mcp__secrets__secret_run -k podzone_qdrant_apikey -- python3 -c "
import sys; sys.path.insert(0, '$HOME/workspace/agent-tooling')
from lib import provenance_substrate
provenance_substrate.upsert_provenance(
    team_slug='podzone-apex', type='question', slug='{short-slug}',
    title='{question, one line}', body='{full context from outcome_note}',
    agent='hermes', tags=['{project-ref}'], linked_ref='{PROJ-XXX/T-YYY if applicable}',
    inactive=False,
)
"
```

**Resolution, once Martin answers**: write the answer as a `type: decision` point, then
supersede the question — `inactive` flips `true`, `superseded_by` points at the decision's
`provenance_id` (the same "supersession never deletes" pattern the collection was designed
around, spec §3.3):

```bash
mcp__secrets__secret_run -k podzone_qdrant_apikey -- python3 -c "
import sys; sys.path.insert(0, '$HOME/workspace/agent-tooling')
from lib import provenance_substrate
provenance_substrate.supersede_provenance(
    provenance_id='{question provenance_id}', superseded_by='{decision provenance_id}',
)
"
```

"What's still open" is one query: `type='question' AND inactive=false` — no STATUS.md
scanning, and the GUI's Current page can surface it as a real panel directly (not yet
built — flag as a plannerapi GUI follow-up if the panel would be useful, out of scope here).

## Step 5 — *(retired)*

Deleted outright — no outbox file, no `<!-- consolidated -->` marker. A session's own
`status` transition (`dispatching → concluded → cleaned_up`, driven by `launch.sh`'s
finalise and this skill's Step 3) already captures "processed"; there is nothing left to
mark.

## Step 6 — Refresh sessions collection (gap-fill)

Unchanged — already Qdrant-based, never touched markdown.

**Path convention:** every `agent-tooling/tools/*.py` invocation below uses the single
`.workspace/agent-tooling` on-demand-clone path — never the `~/workspace/agent-tooling`
hardcode. **Apex exception:** Hermes's primary clone is permanently at
`~/workspace/agent-tooling` (canonical source, not an on-demand checkout); prefer that path
if it exists, falling back to `.workspace/agent-tooling` (clone on demand) otherwise.

```bash
AT="${HOME}/workspace/agent-tooling"; [ -d "$AT" ] || AT=".workspace/agent-tooling"
python3 "${AT}/tools/backfill-sessions.py" 2>&1 | tee /tmp/gapfill-output.txt
```

Capture the output's top-line summary for the Step 7 report. If `backfill-sessions.py` is
not on disk: skip silently and note in Step 7 that the `sessions` collection was not
refreshed this pass.

## Step 6b — Generate usage summary

Unchanged.

```bash
AT="${HOME}/workspace/agent-tooling"; [ -d "$AT" ] || AT=".workspace/agent-tooling"
python3 "${AT}/tools/usage-report.py" --days 7 2>&1 | tee /tmp/usage-report-output.txt
```

Capture the 6-line digest from stdout for the Step 7 report. The markdown file is written
to `team/hermes/outgoing/usage-reports/{today}-usage-summary.md` (same-day overwrite). If
`usage-report.py` is not on disk or Qdrant is unreachable: skip silently and note in Step 7.

## Step 6c — Tooling-drift check (PROJ-039/T-057)

Unchanged.

```bash
AT="${HOME}/workspace/agent-tooling"; [ -d "$AT" ] || AT=".workspace/agent-tooling"
python3 "${AT}/tools/tooling-drift-report.py" \
  --migrated-agents-path planning/projects/PROJ-032-agent-home-repos/migrated-agents.md \
  2>&1 | tee /tmp/tooling-drift-output.txt
```

Capture the counts (N current / N drifted / N flagged) for the Step 7 report, and refresh
the `tooling_version` column in
`planning/projects/PROJ-032-agent-home-repos/migrated-agents.md` from the table. A drifted
or flagged repo is a dispatch signal, not an error to fix here. If the tool is not on disk
or `gh` is unavailable: skip silently and note in Step 7.

## Step 7 — Report

```
Sessions ready for consolidation (Step 0a): N concluded

Sessions refreshed (Step 6):
  Upserted: {N}  ({M} newly transitioned to status: ended)
  Per-workspace totals: see /tmp/gapfill-output.txt

Usage summary — last 7 days (Step 6b):
  {paste the 6-line digest from /tmp/usage-report-output.txt verbatim}
  Report: team/hermes/outgoing/usage-reports/{today}-usage-summary.md

Tooling drift (Step 6c):
  Canonical vX.Y.Z — {N} current, {N} drifted, {N} flagged (see /tmp/tooling-drift-output.txt)
  Registry column refreshed: planning/projects/PROJ-032-agent-home-repos/migrated-agents.md

Task-status changes applied (Step 3):
  {programme}:{project}:{task-ref} → complete
    PR: {repo}#{number} — {title}
  {programme}:{project}:{task-ref} → superseded by {task-ref}

Provenance written (Step 4):
  decision: {slug} — {title}
  question: {slug} — {title}   (open, awaiting Martin)
  question: {slug} — resolved → decision {slug}

Drafts reconciled (N):
  {source} → {recipient}: {slug} → promoted as {programme}:{project}:{task-ref}
  {source} → {recipient}: {slug} → merged into existing brief
  {source} → {recipient}: {slug} → rejected ({reason})
  {source} → {recipient}: {slug} → applied (already complete) — preserved in team/hermes/incoming/applied/

PRs for Martin approval (N):
  ⬆️  {repo}#{number} — {title} ({agent})
     Task: {programme}:{project}:{task-ref}

PR issues routed back (N):
  {agent}: {programme}:{project}:{task-ref} — {gap description}

Protocol violations (N):
  {agent}: {what} — {session id}   ← e.g. direct planning.* RPC call detected in session PR

Questions for Martin (N):
  {agent}: {question}
    provenance: {provenance_id}
```

## Local Mode (fissioned Team Lead) — frozen, PROJ-029/T-019

**No fissioned team has its own `planning.*` tenant today** (roadmapTeam closed/never
operative per PROJ-047; trainingTeam's fission was reversed, Athena is now a podzone
trainer — see `workspace-teamed-directory-reorg` memory). This mode is preserved for when
one is stood up again, but is not exercised or maintained against the current fleet.

If reactivated, it needs a design decision this rewrite deliberately deferred: does a new
fissioned team get its own `team_id` inside the single `podzone-planner` DB (RLS already
scopes by `team_id`, so this may be a small lift), or a fully separate Neon project? Until
that's decided, a reactivated fissioned team should fall back to the **pre-T-019 markdown
flow** (`{team_repo}/planning/team-tasklist.md` etc.) rather than guessing at a DB shape —
see this file's git history (pre-2026-08-09) for the last working version of that flow.
