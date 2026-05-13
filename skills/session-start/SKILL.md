---
name: session-start
description: Load programme context and orient for this session
---

Read `planning/STATUS.md` for the current programme digest, and `planning/team-tasklist.md`
for any detail needed on active tasks.

## Identity Resolution

Resolve identity in this priority order:

1. **Identity file** — check `claude.projectInstructions` in the workspace settings for a
   line matching `identity_file: <path>`. If found, read that YAML file from the
   podzoneAgentTeam repo root. Use `operator`, `agent`, `scope`, `task_filter` from it.

2. **READMEFIRST fallback** — if Step 1 did not resolve identity, scan
   `team/*/READMEFIRST.md` for an `## Identity` section containing a fenced YAML block.
   If exactly one match is found: use its fields (`agent`, `operator`, `scope`,
   `task_filter`, `home_repo`). If the block includes `identity_file:`, load that file
   and merge — fields in the identity file win over the READMEFIRST block. If multiple
   matches are found: prefer the READMEFIRST nearest the CWD by path depth; if still
   ambiguous, fall through to Step 3.

   When identity is resolved via this step, append `(via READMEFIRST)` to the scope line.

3. **Workspace filename** — if no identity file, derive from the workspace filename using
   the pattern `{engineer}-{agent}-{project}.code-workspace` or `{agent}.code-workspace`.

4. **Fallback** — if scope cannot be resolved, show all tasks assigned to `Claude-Code`.

Print scope line at the top of the briefing:
`Session: {operator}:{agent}:{scope}` — e.g. `Session: martin:hephaestus:gitopsapi`

If an identity file was found, also print the `home_repo` (if set) as the primary working
directory hint.

## Task Filtering

Use `task_filter` from the identity file (or inferred agent name) to filter the tasklist:

- Atlas / Cluster Operator: show only `Cluster Operator` and `Atlas` tasks
- Hephaestus / Claude-Code: show only `Claude-Code` and `Hephaestus` tasks
- Hermes / Team Lead: show only `Team Lead` and `Hermes` tasks
- Thoth / Archivist: show only `Archivist` and `Thoth` tasks
- Fallback (no filter): show all Claude-Code tasks

## Task Reporting — Semantic Names

**Never use raw IDs (PROJ-XXX, T-XXX, PRG-XXX, CC-XXX) in output.** Always substitute
semantic names. See `agenticflows/operations/task-naming.md` for the full mapping table.

**Format:** `programme:project:task-slug {status-emoji}`

- Programme shortform: from `task-naming.md` (e.g. PROJ-003 → programme `gitops-product`)
- Project shortform: from `task-naming.md` (e.g. PROJ-003 → `gitopsapi`)
- Task slug: strip leading articles/agent names from task summary, 3–4 words, hyphenated,
  max 30 chars (e.g. "Dev functional testing" → `dev-functional-testing`)

**Reference links:** For each task listed, include inline links to relevant material:
- Task brief: `team/{agent}/incoming/{date}-{slug}.md` (if one exists)
- Spec or design doc: `planning/projects/{proj}/spec.md` or architecture doc
- Open PR: `{repo}#{number}` or full GitHub URL
- Prior session outbox: `team/{agent}/outgoing/session-{date}-status.md`

Example output format:

```
Session: martin:hephaestus:gitopsapi

Ready / In Progress
  gitops-product:gitopsapi:dev-functional-testing 🚀
    Brief: team/hephaestus/incoming/2026-03-28-dev-functional-testing.md
  gitops-product:gitopsapi:ete-ext-hardening 🚀
  platform-buildout:collab-infra:remote-session-setup 🚀

Blockers
  platform-buildout:network-ingress:nexus-cf-dns ⚠️ — CF token invalid (Martin)

Decisions for Martin
  gitops-product:gitopsapi:credential-objects — storage confirmed K8s CM/Secrets

Recommended focus: gitops-product:gitopsapi:ete-ext-hardening
  Spec: planning/projects/PROJ-003-gitopsapi-product/
```

Keep total output to 10–15 lines. This is orientation, not a full status report.

## Repo State Check

Run this **after** identity resolution, **before** printing the task list. Check the git
state of all repos in the identity file's `repos:` list (or home_repo if no list).

For each repo, run `git status --short` and `git branch --show-current`:

- If **clean**: no output needed (suppress from briefing)
- If **dirty** (uncommitted changes, untracked files): print prominently under a
  `⚠️  Repo State` heading — list each repo and the file count / branch name
- If on a **non-main/non-feature branch** unexpectedly: flag it

### Agent-specific checks

**Atlas** — after git status, also check `cluster-repos/` repos:
- For any branch that is not `main`, run `git log --oneline {branch}..main` to detect if
  those changes already landed via another PR (see Atlas memory:
  `team/atlas/memory/cluster_charts_pr_pattern.md`). If yes: flag the stale branch.
- Stale branches should be closed/deleted, not rebased.

**Hephaestus** — if active branch has dirty files:
- Identify which feature branch is dirty and surface it as the first item in the briefing
  (the task `gitopsapi:workspace-cleanup` exists precisely for this pattern).

### Output format (when issues found)

```
⚠️  Repo State
  gitopsapi (feat/proj020-t003): 9 dirty files — resolve before starting new work
  management-infra: clean
```

If all repos are clean, omit the section entirely.

## Outbox Reconciliation

Run this **after** task filtering, **before** printing the final briefing. This detects
tasks completed or updated since the last `/consolidate-tasks` run.

### Find the consolidation baseline

Identify the last consolidation commit:
```
git log -1 --format=%H --grep='consolidate' -- planning/team-tasklist.md
```
If no commit is found, fall back to a 7-day window from today.

### Scan outboxes

Read `team/*/outgoing/session-*.md` files modified after the baseline commit (or within
the 7-day window). For the current agent, read their outbox files. For other agents, scan
all `team/*/outgoing/` directories — useful so sessions see fresh cross-agent state.

### Reconcile against filtered tasklist

For each task in the briefing:
- If the same task has a **✅** entry in a post-consolidation outbox: suppress it from
  "Ready / In Progress" and move it to a new **"Completed since last consolidation"**
  section with the outbox file link.
- If the task appears **🔄** (in progress) in an outbox: mark it 🔄 in the briefing
  regardless of tasklist status.
- If the outbox reports a **blocker** against a task: surface it in the Blockers section
  with the outbox file link.

Match tasks between outbox and tasklist using the `programme:project:task-slug` format.

### Output section

Insert between "Ready / In Progress" and "Blockers":

```
Completed since last consolidation
  platform-buildout:collab-infra:example-task ✅
    Outbox: team/hermes/outgoing/session-2026-04-06-status.md
```

If nothing to show: omit the section entirely.

### Edge cases

- **Outbox older than the last consolidation commit:** skip it (already absorbed).
- **Outbox contradicts tasklist** (task 🚀 in tasklist, ✅ in outbox): trust the outbox
  and add a footer line:
  `ℹ️  STATUS/tasklist may be stale — N outbox entries post-date last consolidation.`
- **No consolidation marker commit found:** fall back to the 7-day window.
