# Training collections — schema & semantics

**Status:** v1 (PROJ-011/T-031, CC-416). Collections live on cloud Qdrant since 2026-07-12.
**Owners:** PROJ-011 / training team.
**Consumers:** T-030 trainee-repo template v3 hooks (soft-gated on this schema).
**Design record:** `podzoneTeam/planning/projects/PROJ-011-academy/trainee-repo-takeon-flow.md` Revision 2 (R2-2/R2-3).
**Machine-readable schemas:** `collections/training_briefs.yaml`, `collections/training_session_telemetry.yaml`.
**Setup (idempotent, indexes-before-ingest):** `hooks/setup-training-collections.sh`.
**Helpers (the one implementation of these shapes):** `lib/training_substrate.py`.

Trainee repos route **all** Qdrant traffic to these collections — never the
fleet substrate (`briefs` / `session_substrate` / `claude_session_telemetry`).
Trainee credentials are scoped to exactly these two collections, so the
isolation holds by construction. Schema is training-requirements-driven and
**deliberately diverges from the agenticflows `briefs` shape**
(operator-explicit, R2-3).

---

## `training_briefs`

One collection carries **both directions** of the trainer↔trainee channel,
discriminated by `point_type` + `direction` (point-type/direction fields
rather than separate collections — extensible later to training inter-agent
communication by adding a direction value, no new collection).

| point_type | direction | author | what it is |
|---|---|---|---|
| `brief` | `to_trainee` | trainer | recurring/reusable brief — personalised curriculum brief (`channel: training`) or the repo's tools/repo-update channel (`channel: operational`) |
| `message` | `from_trainee` | trainee's agent | write-back: `progress`, `question`, `issue`, or an operational-brief `ack` |

Write ownership is convention, not JWT-enforced (a trainee token has
collection-wide rw): trainers own `brief` points, trainee agents own
`message` points.

### Recurring-brief lifecycle (the training Brief-Status model)

A training brief is **never "complete" mid-programme** — it is a standing
document the trainer re-authors and the trainee re-materialises:

- **`active`** — materialised into the trainee's next session.
- **`paused`** — kept, not materialised (holiday, remediation detour).
- **`retired`** — end of programme / superseded; kept for the record.

There is no draft→approved→in_progress→complete pipeline (that is the
agenticflows model and does not apply). Re-authoring is an **upsert of the
same point**: the point id is `uuid5(NAMESPACE_DNS, brief_id)`, so the
trainer's re-write converges on the point with a bumped `revision` and fresh
`updated_at`; the trainee's next SessionStart materialises the latest body
and appends its runtime sid to `session_ids[]`.

### brief_id key (no date component)

Recurring briefs are re-authored under the **same id** — dating the id would
fork the thread:

```
training/{trainee}/{curriculum-slug}    personalised training brief
training/{trainee}/operational          the repo's operational brief
```

Message points carry the `brief_id` of the thread they belong to, with a
retry-idempotent id `uuid5("{brief_id}/msg/{session_id}/{seq}")`.

### The operational channel round-trip (R2-1)

1. Trainer re-authors `training/{trainee}/operational` (revision N) with the
   instruction (tooling update, config change, credential rotation…).
2. SessionStart surfaces it; the **trainee approves in-session** (no team
   pushes to trainee repos).
3. The `ack` lands as a `message` point (`message_type: ack,
   ack_of_revision: N`) — the trainer's confirmation the instruction landed.

**Who performs the write (PROJ-011/T-121 #14).** A brief may carry an optional
`files: [{path, content}]` payload. When it does, the **hook** writes those
files — `python3 .claude/hooks/trainee-materialise.py --apply N` applies them
*and* acks in one step. The tutor's whole part is a one-sentence summary and a
yes/no question: a beginner is never shown Alex reading an operational
instruction and rewriting its own persona/protocol files mid-conversation.

Paths are repo-relative only (no absolute, no `..`, no backslash, nothing under
`.git/`, 256KB cap) — `training_substrate.normalise_brief_files` enforces this
when the trainer authors, and the hook re-checks the resolved path against the
repo root before writing. A brief with no `files` is the legacy prose form: the
agent applies it by hand and closes with `--ack N`. Recurring briefs never
expire, so the hook compares content on disk and stays silent once a revision
has been applied — the trainee is asked exactly once.

### The continuity checkpoint (`message_type: progress`, PROJ-011/T-122 #15)

A dedicated `channel: training` thread, `training/{trainee}/continuity`,
distinct from the trainee's curriculum brief thread(s) — a rolling record of
"where the trainee is", written by the trainee's agent and read back at the
*next* SessionStart, so a session that crashes before its local
`Trainee/training-state.md` write still leaves a durable position record.

- **Write** — `python3 .claude/hooks/trainee-materialise.py --checkpoint
  "<summary>"`, called by Alex at the same ~10-prompt cadence `AGENTS.md`
  already specifies for the local `training-state.md` checkpoint. The hook
  builds the point (never the tutor — same T-121 #14 precedent); `seq` is
  the count of this session's checkpoint messages already on the thread, so
  a failed write retried with identical args converges on the same point.
- **Read** — `trainee-materialise.py`'s `SessionStart` query: latest point
  where `point_type=message`, `channel=training`, `message_type=progress`,
  `trainee={trainee}`, `order_by created_at desc`, `limit 1` — across the
  whole thread, not scoped to one session_id, since the point that matters
  is the *last* one written regardless of which session wrote it.
- **Reconciliation** — last-write-wins by timestamp against the local
  `Trainee/training-state.md` `**Last session:**` date. DB point newer (or
  no local date recorded yet) → surfaced as `SessionStart` context,
  explicitly labelled a database record. Local newer, or DB
  unreachable/absent → today's behavior, unchanged — including for any
  trainee device with no credentials at all, where both the write and the
  read are permanent no-ops from session one.
- **Closes PROJ-011/T-120 #2/#3 only jointly with the per-`Stop` transcript
  mirror (T-122 Build A)** — this closes the *position-drift* half; the
  mirror closes the *content-loss* half. Neither alone is the full fix.

### Vectors & embedding split

Single named vector `brief` (768, cosine, nomic-embed-text over `body`).
**Trainer-side** authoring tools embed (workstation ollama). **Trainee-side**
writes are payload-only per the fleet-wide T-002 hooks-never-embed
convention: `"vector": {}` — an empty named-vector map, never an omitted key
(Qdrant 400s on a missing `vector` field). PROJ-042 vectorises payload-only
points in retrospect.

### Indexes (created before any ingest)

keyword: `point_type`, `direction`, `brief_id`, `trainee`, `channel`,
`status`, `message_type` · datetime: `created_at`, `updated_at`.

Query patterns they serve: trainee SessionStart materialise
(`trainee` + `point_type=brief` + `status=active`), trainer message sweep
(`point_type=message` + `status=open`, order_by `created_at`), thread view
(`brief_id`), per-channel filters.

---

## `training_session_telemetry`

The training sibling of `claude_session_telemetry` (CST). Trainee
observability is these points **only** — no git push of logs, no
agent-telemetry repo push from trainee machines (R2-5).

The point shape **mirrors CST** so the PROJ-042 enrichment job and CST
readers work over both collections unchanged:

- same three named vectors: `intent_vector` / `action_vector` /
  `response_vector` (768, cosine);
- writes are payload-only (`"vector": {}`, T-002) — trainee hosts have no
  embed endpoint at all;
- payload = the CST event payload for the event class (e.g. the enriched
  Stop payload from `lib/stop_telemetry.build_payload`) **plus the additive
  indexed `trainee` field** (`lib/training_substrate.build_telemetry_point`
  does the wrap);
- point ids follow the CST construction for the event class (e.g. Stop =
  `uuid5("stop/{session_id}/{turn_uuid}")`) so retried hooks converge.

Indexes: keyword `session_id`, `event_type`, `trainee` · datetime `timestamp`.

---

## `training_token_registry` (utility — not trainee-facing)

Training-team-owned credential registry backing
`tools/training-jwt.py` (see `docs/training-jwt-runbook.md`). Payload-only
collection (`"vectors": {}`); one point per issued trainee credential, point
id = `token_id` (plain uuid4). **Trainee tokens get no claim on this
collection** — revocation must be out of trainee reach — which is why it is
a third collection rather than points inside `training_briefs`.

Payload: `token_id`, `trainee`, `kind` (`self_signed` | `cloud_key`),
`token_fingerprint` (sha256/16 — the credential itself is never stored),
`claims_summary`, `active`, `minted_at`, `expires_at`, `minted_by`.
Indexes: keyword `token_id`, `trainee`, `active` · datetime `minted_at`.

---

## Additivity

All three schemas are additive: new fields may be appended (readers must
tolerate them); existing fields will not be renamed or change type without a
documented migration.
