---
name: consolidate-tasks
description: Merge agent outbox status files into team-tasklist.md (Team Lead only)
---

This skill is for **Team Leads only** — Hermes (apex) or a fissioned team's own Team Lead.

At skill start, read the operator's identity YAML (same resolution chain as session-start).
Check `home_repo` and `role_class`:

- If `home_repo == podzoneAgentTeam` (Hermes): run the **full mode** described below —
  cross-team scan including fissioned repo Step 1b. All steps apply.
- If `home_repo != podzoneAgentTeam` AND `role_class` contains `team-lead`:
  run **local mode** — see the Local Mode section after Step 1b.
- If `role_class` does NOT contain `team-lead` and the operator is not the system-owner:
  refuse with: "consolidate-tasks is for Team Leads only. Raise a task proposal via
  your outbox and your Team Lead will consolidate during the next session."

Run at the start of a Hermes session (after reviewing incoming) or when agents have
completed sessions since the last consolidation.

## Step 0 — Sessions registry and podzoneAgentTeam PR review

Read `planning/sessions/active.md`.

### 0a — Session status sync

For each session with status `in-flight`:
- Check whether the agent's outbox contains a session file dated on or after the launch date
- If a concluded outbox file exists: update status to `concluded` in `active.md`
- If no outbox file and session launched more than 2 days ago: flag to Martin as lost session

### 0b — podzoneAgentTeam session PR review

For each session with status `concluded`, find the podzoneAgentTeam session PR:
```bash
gh pr list --repo PodZonePlatformEngineering/podzoneAgentTeam \
  --head session/{agent}-{YYYY-MM-DD}-{task-slug} --json number,state,title,files
```

For each open session PR:

**Apex-clone-on-main guard (PROJ-039/T-031) — assert BEFORE the push.** A migrated session
that took podzoneAgentTeam as a write-target should have branched a worktree under
`~/sessions/{sid}/podzoneAgentTeam`, never the shared primary clone. If the primary clone was
branched by mistake (the C2b defect, #102), `push origin main` below would push the wrong ref
or fail. Refuse to push unless the apex clone is on `main`:
```bash
APEX_BRANCH=$(git -C ~/workspace/podzoneAgentTeam rev-parse --abbrev-ref HEAD)
if [ "$APEX_BRANCH" != "main" ]; then
  echo "ABORT: ~/workspace/podzoneAgentTeam is on '$APEX_BRANCH', expected 'main'." >&2
  echo "A session branch was checked out in the primary clone (should be a worktree under" >&2
  echo "~/sessions/{sid}/podzoneAgentTeam). Restore before consolidating:" >&2
  echo "  git -C ~/workspace/podzoneAgentTeam checkout main" >&2
  exit 1
fi
```

**Push before diff — prevents Hermes unpushed commits appearing as agent changes:**
```bash
# Push any unpushed Hermes commits so gh pr diff uses the correct merge-base
git -C ~/workspace/podzoneAgentTeam push origin main 2>/dev/null || true
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
gh pr merge {number} --merge --repo PodZonePlatformEngineering/podzoneAgentTeam
```
Mark session `concluded-merged` in `active.md`.

**Outcome B** — path violation or missing outbox: flag to Martin; do not merge.

### 0c — Worktree cleanup

For each session `concluded-merged` where all task-repo PRs are also merged:
```bash
git -C ~/workspace/{repo} worktree remove ~/sessions/{session-id}/{repo-name} --force
git -C ~/workspace/podzoneAgentTeam worktree remove ~/sessions/{session-id}/podzoneAgentTeam --force
rmdir ~/sessions/{session-id}   # only if empty
```
Mark session `cleaned-up` in `active.md`.

## Step 1 — Discover unprocessed outbox files

Scan all agent outboxes for session status files:

```bash
ls team/*/outgoing/session-*.md 2>/dev/null
```

This covers all current podzoneAgentTeam agents (Hermes, Hephaestus, Atlas, Thoth) plus
any fissioned-team stub agents (Clio, Alex, Norma, Eben) whose stubs land here.

List the files found. If none, print "No pending outbox files." and stop.

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
file to `podzoneAgentTeam/team/{agent}/outgoing/` for visibility. If a stub exists in
podzoneAgentTeam for the same agent/date/slug as a full file found in the fissioned repo,
prefer the full file and skip the stub. Mark the stub as superseded in the Step 6 report.

If a fissioned team's local path does not exist or is unreachable, flag it in the
Step 6 report and continue.

## Local Mode (fissioned Team Lead)

Activated when `home_repo != podzoneAgentTeam` and `role_class` contains `team-lead`.
The fissioned Team Lead consolidates their own team without depending on Hermes.

**Step substitutions for local mode:**

| Full mode step | Local mode behaviour |
|---|---|
| Step 0 — sessions registry + podzoneAgentTeam PR review | **Skip** — fissioned session PRs are in own repo (handled in Step 0b equivalent below) |
| Step 1 — podzoneAgentTeam outbox scan | **Skip** — not applicable |
| Step 1b — fissioned repo scan | **Run for own repo only**: `ls team/*/outgoing/session-*.md 2>/dev/null` in the fissioned repo worktree |
| Step 2 — read and parse | Same as full mode |
| Step 2b — PR review | Look up session PRs in the **fissioned team repo** (e.g. `PodZonePlatformEngineering/trainingTeam`) |
| Step 2c — drafts reconciliation | Scan `team/*/incoming/drafts/*.md` in own repo only |
| Step 3 — apply to tasklist | Update **own** `planning/team-tasklist.md`, not podzoneAgentTeam's |
| Step 4 — update STATUS.md | Update **own** `planning/STATUS.md` |
| Step 5 — mark outbox files | Write `<!-- consolidated YYYY-MM-DD -->` in own repo outbox files |
| Step 6 — report | Same format with header `[LOCAL MODE — {TeamName}]` |

**Optional upward-sync step (after Step 5):**
If cross-team tasks or apex programme changes surfaced during consolidation (e.g. a task
that affects podzoneAgentTeam agents, a blocker requiring Martin, or a new decision),
write a draft to the plain podzoneAgentTeam clone:
`~/workspace/podzoneAgentTeam/team/hermes/incoming/drafts/{date}-{team}-sync.md`

Include only meaningful upward content — do not write a sync draft if there is nothing
new for the apex.

**Local mode Step 0b equivalent — fissioned session PR review:**

```bash
gh pr list --repo PodZonePlatformEngineering/{home_repo} \
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
  podzoneAgentTeam's `planning/team-tasklist.md`.
  - If the fissioned team has no `planning/team-tasklist.md`, list all tasks in the
    Step 6 report and flag to Hermes. Do not create the file.
- **Step 4:** Do NOT write fissioned team task details into podzoneAgentTeam `STATUS.md`.
  Add one summary line per team only:
  `{TeamName}: N tasks consolidated — see {team}/planning/team-tasklist.md`
- **Step 5:** Write `<!-- consolidated YYYY-MM-DD -->` into the outbox file in the
  fissioned repo (not in podzoneAgentTeam).

## Step 2b — Structural PR Review

For each PR listed in `## PRs Raised` across all outbox files:

```bash
gh pr view {repo}#{number} --json state,title,headRefName,commits,statusCheckRollup
```

**Fissioned team session PRs:** When checking the session PR (not task-repo PRs) for a
fissioned team agent, look it up in the repo from the Step 1b config table
(e.g. `PodZonePlatformEngineering/trainingTeam`), not `podzoneAgentTeam`.

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

## Step 4 — Update STATUS.md

Rewrite `planning/STATUS.md` incorporating decisions and blocker changes from all
processed outbox files. Follow session-end STATUS.md format rules.

## Step 5 — Mark outbox files processed

Add `<!-- consolidated YYYY-MM-DD -->` as the first line of each processed file
to prevent double-processing in future passes.

## Step 6 — Report

```
Sessions registry: N in-flight, N concluded, N cleaned-up
  ⚠️  Lost session (no outbox): {session-id}   ← if applicable

Consolidated: N files ({agent} {date}, ...)

Changes applied:
  {programme}:{project}:{task-slug} ✅ Complete
    PR: {repo}#{number} — {title}
  {programme}:{project}:{task-slug} 🔄 still in progress

Drafts reconciled (N):
  {source} → {recipient}: {slug} → promoted as {programme}:{project}:{task-slug}
  {source} → {recipient}: {slug} → merged into existing brief
  {source} → {recipient}: {slug} → rejected ({reason})

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