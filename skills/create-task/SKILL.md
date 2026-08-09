---
name: create-task
description: Guided task creation — adds a new row to planning.task via planning.create_task()
---

Guide the operator through creating a new task entry. Routing is determined entirely by
the operator's identity — read `home_repo` and `role_class` from the identity YAML
(same resolution chain as session-start) before doing anything else.

> **PROJ-029/T-020 (Phase 4c) — this skill now writes `planning.task` via the
> `planning.create_task()` RPC instead of appending a row to
> `team-tasklist.md`.** The third and last of spec §12 Phase 4's fleet-skill rewrites
> (after `/launch-session` T-018 and `/consolidate-tasks` T-019). `create_task()` (live
> since 2026-08-09, `planner-db`#6) allocates the task's `ref` race-safely itself — no more
> "read the tasklist, find the highest T-NNN, increment" step, and no more CC-counter
> bookkeeping (`planning.task.cc_ref` is documented as "legacy cross-reference, kept for
> continuity" — a new task simply leaves it null).

## Step 0 — Identity and authorisation

Read the operator identity YAML. Extract:
- `home_repo` — determines the target team (`planning.current_team_id()` resolves this
  server-side off the caller's impersonated identity, not a client-supplied value — same
  posture every other write RPC uses)
- `role_class` — must contain `team-lead`, or `operator_mode == system-owner`

If neither condition is met: write a task *proposal* to the outbox (see Step 3b) rather
than calling `create_task()` directly.

## Step 1 — Collect Fields

Ask for (or infer from context):

| Field | Prompt | Notes |
|---|---|---|
| **Project** | Which project? | e.g. PROJ-003, PROJ-015 — resolved to a `planning.project.id` below |
| **Summary** | One-line task description | Imperative, concise — becomes `create_task`'s `p_summary` |
| **Title** | Short task name | A few words — becomes `p_title` (distinct from `summary`; the old skill only had one field, this RPC wants both) |
| **Agent** | Who will do this? | Maps to `p_owner` — Claude-Code / Cluster Operator / Team Lead / Archivist / Martin |
| **Status** | Initial status | Default `ready`; `create_task` explicitly disallows `complete`/`closed` as an initial status — a task is created then later closed, never born finished |
| **Blocked by** | What must complete first? | **Known gap**: `create_task()` has no `p_blocked_by` param — `planning.task.blocked_by uuid[]` exists on the table but isn't settable at creation. Fold the blocker into `summary`'s prose for now ("blocked on T-XXX until…") and flag it in the Step 2 confirmation; a follow-up `UPDATE planning.task SET blocked_by = …` needs a Team-Lead-run Neon MCP call (no RPC covers this yet) if a structured link is actually needed. |
| **Detail file** | Does this need a brief? | Optional — unchanged, still a markdown file, not a DB concern |

**Project resolution** (replaces the old "find the highest T-NNN" step — this RPC needs a
real `project_id` UUID, not a ref string):

```bash
mcp__secrets__secret_run -k podzone_qdrant_apikey -- python3 -c "
import sys; sys.path.insert(0, '$HOME/workspace/agent-tooling')
from lib import planning_mirror
conn = planning_mirror.connect()
with conn.cursor() as cur:
    cur.execute('SELECT id, title FROM planning.project WHERE ref = %s', ('{PROJ-NNN}',))
    row = cur.fetchone()
print(row)
conn.close()
"
```

If no row: the project doesn't exist in `planning.project` yet — that's a separate,
larger decision (new project = new programme-level scope) than this skill covers; flag
to the Team Lead rather than improvising a project row here.

**`PLANNING_DATABASE_URL` prerequisite**: same operational posture as
`lib/planning_mirror.py`'s own docstring — provision the `planning_automation` service
role's connection string (PROJ-029/T-021) into the session env before Step 3a.

## Step 2 — Confirm

Show the proposed call before making it:

```
create_task(
  project_id = {uuid}  ({PROJ-NNN})
  title      = "{title}"
  summary    = "{summary}"
  owner      = "{agent}"
  status     = "{status}"       -- default 'ready'
)
Blocked by: {note if applicable — see Step 1's known gap}
```

Ask for approval before writing.

## Step 3a — Write (Team Lead or system-owner)

```bash
mcp__secrets__secret_run -k podzone_qdrant_apikey -- python3 -c "
import sys; sys.path.insert(0, '$HOME/workspace/agent-tooling')
from lib import planning_mirror
conn = planning_mirror.connect()
result = planning_mirror.call_rpc(conn, 'create_task', {
    'project_id': '{project-uuid}',
    'title': '{title}',
    'summary': '{summary}',
    'owner': '{agent}',
    'status': '{status}',
})
print(result)
conn.close()
"
```

The returned row includes the allocated `ref` (e.g. `T-247`) — race-safe even under
concurrent callers (`planning.project.next_task_seq`'s row-level lock, not a naive
`MAX(ref)` read).

Scaffold the detail brief if requested (unchanged — still a plain markdown file at
`{home_repo_path}/planning/projects/PROJ-NNN/tasks/T-NNN-{slug}.md`, referencing the
allocated ref).

Output: `Created {project}/{ref}: {title} — status: {status}`

## Step 3b — Task Proposal (non-Team-Lead agents)

**Unchanged** — stays markdown, deliberately (same rationale as `/consolidate-tasks`'
Fork 2: a proposal is upstream of the board, not a board entry yet). Write a proposal to
`team/{agent}/outgoing/task-proposal-YYYY-MM-DD-{slug}.md`. Do NOT call `create_task()`
directly — that authority stays with the Team Lead (same boundary
`/consolidate-tasks` enforces for `close_task`/`supersede_task`/`conclude_session`). The
home repo's Team Lead reviews and creates the task (Step 3a) during the next
consolidation pass.

```markdown
# Task Proposal — {summary}

**From:** {agent}
**Date:** YYYY-MM-DD
**Project:** PROJ-NNN
**Suggested Title:** {title}
**Suggested Agent:** {agent}
**Initial Status:** ready

## Summary

{one-line description}

## Detail

{context, motivation, acceptance criteria}

## Blocked by

{blocker — or "nothing"}
```
