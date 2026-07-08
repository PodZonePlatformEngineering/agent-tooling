---
name: create-task
description: Guided task creation — adds a new entry to the team task board
---

Guide the operator through creating a new task entry. Routing is determined entirely by
the operator's identity — read `home_repo` and `role_class` from the identity YAML
(same resolution chain as session-start) before doing anything else.

## Step 0 — Identity and authorisation

Read the operator identity YAML. Extract:
- `home_repo` — determines which tasklist and CC counter to use
- `role_class` — must contain `team-lead`, or `operator_mode == system-owner`

If neither condition is met: write a task *proposal* to the outbox (see Step 3b) rather
than editing the tasklist directly.

## Step 1 — Collect Fields

Ask for (or infer from context):

| Field | Prompt | Notes |
|---|---|---|
| **Project** | Which project? | e.g. PROJ-003, PROJ-015; create new if needed |
| **Summary** | One-line task description | Imperative, concise |
| **Agent** | Who will do this? | Claude-Code / Cluster Operator / Team Lead / Archivist / Martin |
| **Status** | Initial status | Default: 🚀 Ready; or ⏸ Pending if blocked |
| **Blocked by** | What must complete first? | Optional |
| **Detail file** | Does this need a brief? | Optional |

**Task ID resolution:**

Resolve the next task ID by reading the tasklist at `{home_repo_path}/planning/team-tasklist.md`
and finding the highest T-NNN in the target project section. Increment by 1.

**CC counter resolution:**

- If `home_repo == podzoneTeam`: read from podzoneTeam's `CLAUDE.md` —
  look for `Next available CC number: CC-XXX`. Increment it in CLAUDE.md after writing.
- If `home_repo != podzoneTeam` (fissioned team): read from the fissioned team's
  `CLAUDE.md` if present; otherwise read from
  `{home_repo_path}/planning/task-counter.md` (create if absent, seeding from the
  highest CC number found in the fissioned tasklist).

## Step 2 — Confirm

Show the proposed row:

```
| T-NNN | CC-NNN | 🚀 Ready | {agent} | {summary} |
Tasklist: {home_repo}/planning/team-tasklist.md
```

Ask for approval before writing.

## Step 3a — Write (Team Lead or system-owner)

Resolve paths against `home_repo`:

```
tasklist:     {home_repo_path}/planning/team-tasklist.md
detail brief: {home_repo_path}/planning/projects/PROJ-NNN/tasks/T-NNN-{slug}.md  (if needed)
CC counter:   podzoneTeam CLAUDE.md  (if home_repo == podzoneTeam)
              {home_repo_path}/planning/task-counter.md  (if fissioned)
```

1. Add the row to the correct project section in the tasklist.
2. Scaffold the detail brief if requested.
3. Increment the CC counter in the resolved location.

Output: `Created {project}/T-{n} (CC-{n}): {summary} — status: {status}`

## Step 3b — Task Proposal (non-Team-Lead agents)

Write a proposal to `team/{agent}/outgoing/task-proposal-YYYY-MM-DD-{slug}.md`.
Do NOT edit `planning/team-tasklist.md` directly.
The home repo's Team Lead reviews and adds the task during the next consolidation pass.

```markdown
# Task Proposal — {summary}

**From:** {agent}
**Date:** YYYY-MM-DD
**Project:** PROJ-NNN
**Suggested ID:** T-NNN (or CC-NNN)
**Suggested Agent:** {agent}
**Initial Status:** 🚀 Ready

## Summary

{one-line description}

## Detail

{context, motivation, acceptance criteria}

## Blocked by

{blocker — or "nothing"}
```
