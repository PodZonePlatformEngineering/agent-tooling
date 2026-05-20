# `sessions` Qdrant collection — payload schema

**Status:** v1 (PROJ-034 Phase 1) — usage capture only, no dollar values.
**Owners:** PROJ-034 / Hephaestus.
**Collection:** `sessions` on cloud Qdrant (`CLOUD_COLLECTIONS` in
`agent-tooling/hooks/stop-heartbeat.py`).
**Point ID:** `uuid5(NAMESPACE_DNS, session_id)`.
**Vector:** 4-dim dummy (filter-only retrieval — no semantic search on this collection).

This document is the contract. Writers (Stop hook, `/session-end`, backfill,
`/consolidate-tasks` gap-fill, and any future producer) must produce payloads
matching this shape. Readers (budi, usage reports) consume against it.

The schema is **additive**: new fields may be appended in a later phase, but
existing fields will not be renamed or have their types changed without a
documented migration.

---

## Fields

### Existing (retained from pre-PROJ-034 schema)

| Field | Type | Required | Notes |
|---|---|---|---|
| `session_id` | UUID string | yes | also drives the Qdrant point ID |
| `last_heartbeat_ts` | ISO 8601 | yes | updated on every Stop hook |
| `status` | enum | yes | `in_progress` \| `idle` \| `ended` \| `abandoned` (see below) |
| `cwd` | string | yes | absolute path to session working dir |

### Added in PROJ-034 Phase 1

| Field | Type | Required | Notes |
|---|---|---|---|
| `workspace` | string | yes | basename of `cwd` (e.g. `podzoneAgentTeam`) |
| `agent` | string \| null | optional | derived by `lib/session_metadata.resolve()`; may be `null` if no identity file matches |
| `first_message_ts` | ISO 8601 \| null | yes | min `.timestamp` across all JSONL entries; `null` only for an empty file |
| `last_message_ts` | ISO 8601 \| null | yes | max `.timestamp`; same null rule |
| `duration_seconds` | int | yes | `last_message_ts - first_message_ts`, seconds; `0` if either timestamp missing |
| `model_usage` | object | yes | keyed by model name; see [§ model_usage shape](#model_usage-shape) |
| `total_tokens` | object | yes | same shape as one `model_usage` entry, summed across all models |
| `message_counts` | object | yes | counts keyed by `.type` field — see [§ message_counts](#message_counts) |
| `data_source` | enum | yes | which write path produced this payload — see [§ data_source](#data_source) |
| `jsonl_path` | string | yes | absolute path to the source JSONL on disk |
| `jsonl_mtime` | ISO 8601 | yes | `os.stat(path).st_mtime`, ISO-formatted, UTC |
| `updated_at` | ISO 8601 | yes | upsert time (caller sets this; not derived by `scrape()`) |

### Optional / debug fields

| Field | Type | Notes |
|---|---|---|
| `_warnings.malformed_lines` | int | present when the scraper skipped one or more malformed JSON lines; absent otherwise |

---

## `model_usage` shape

Keyed by `message.model` value (e.g. `claude-sonnet-4-6`, `claude-opus-4-7`).
Entries whose `.message.model` is missing are bucketed under `"unknown"`.

```json
{
  "input_tokens": 0,
  "output_tokens": 0,
  "cache_creation_input_tokens": 0,
  "cache_read_input_tokens": 0,
  "cache_creation_5m_input_tokens": 0,
  "cache_creation_1h_input_tokens": 0,
  "web_search_requests": 0,
  "web_fetch_requests": 0,
  "iterations": 0
}
```

**Provenance:**

| Bucket field | JSONL source |
|---|---|
| `input_tokens` | `.message.usage.input_tokens` |
| `output_tokens` | `.message.usage.output_tokens` |
| `cache_creation_input_tokens` | `.message.usage.cache_creation_input_tokens` |
| `cache_read_input_tokens` | `.message.usage.cache_read_input_tokens` |
| `cache_creation_5m_input_tokens` | `.message.usage.cache_creation.ephemeral_5m_input_tokens` |
| `cache_creation_1h_input_tokens` | `.message.usage.cache_creation.ephemeral_1h_input_tokens` |
| `web_search_requests` | `.message.usage.server_tool_use.web_search_requests` |
| `web_fetch_requests` | `.message.usage.server_tool_use.web_fetch_requests` |
| `iterations` | count of assistant entries for this model whose `.message.usage` is present |

### D1 — the only usage filter

Sum every JSONL entry where:

- `entry.type == "assistant"`, AND
- `entry.message` is a dict, AND
- `"usage" in entry.message`.

**Do not filter on `stop_reason`.** In a representative session 352/365 usage
entries carry `stop_reason: tool_use` (intra-turn tool pauses); filtering on
`end_turn` would discard ~96% of token usage.

---

## `total_tokens`

Same shape as a single `model_usage` bucket. Values are the per-field sum across
every model bucket. Consumers should treat `total_tokens` as a denormalised
convenience, not as a source of truth — the per-model breakdown is authoritative.

---

## `message_counts`

Counts of JSONL entries keyed by the `.type` field. Known keys today:

```json
{
  "user": 0,
  "assistant": 0,
  "system": 0,
  "attachment": 0,
  "file-history-snapshot": 0,
  "queue-operation": 0,
  "last-prompt": 0,
  "ai-title": 0,
  "pr-link": 0
}
```

Unknown types are added as new keys; the scraper must not raise on novel
entry types. Counts include every entry of that type (e.g. `assistant`
counts assistant turns with **and** without `.message.usage`).

---

## `data_source`

Identifies the write path that produced this payload. Multiple writers will
upsert against the same `session_id` over a session's lifetime — the
most-recent writer wins (Qdrant upsert overwrites payload).

| Value | Owner | When |
|---|---|---|
| `stop_hook` | T-005 — `hooks/stop-heartbeat.py` | every Stop hook firing during a session |
| `session_end_skill` | T-006 — `/session-end` skill | once, at the close of a session |
| `backfill` | T-004 — `tools/backfill-sessions.py` | one-shot/batch over `~/.claude/projects/*.jsonl` |
| `consolidate_fill` | T-007 — `/consolidate-tasks` skill | best-effort gap-fill for sessions that have no `stop_hook` or `session_end_skill` write yet |

---

## Status semantics

Derived from `jsonl_mtime` (the source JSONL's filesystem mtime, not
`last_heartbeat_ts`). Transitions are implemented by the writers (T-005, T-007);
T-001 only documents the enum.

| Value | Condition |
|---|---|
| `in_progress` | mtime within the last 30 minutes |
| `idle` | mtime between 30 minutes and 6 hours ago |
| `ended` | mtime older than 6 hours |
| `abandoned` | `ended` AND no `session-end` outbox file exists in the agent home |

Pre-PROJ-034 writers used a single literal `"active"` value; T-005 will replace
that with the enum above as part of the Stop hook upgrade.

---

## Worked example

Synthesised payload for a short session that used both Opus and Sonnet:

```json
{
  "session_id": "8e100f96-ce4c-48a8-a872-b85ced0d5b54",
  "workspace": "agent-tooling",
  "agent": "hephaestus",
  "cwd": "/Users/martincolley/sessions/hephaestus-2026-05-20-proj034-foundation/agent-tooling",
  "first_message_ts": "2026-05-20T10:00:00.000Z",
  "last_message_ts": "2026-05-20T10:42:11.000Z",
  "last_heartbeat_ts": "2026-05-20T10:42:12.471Z",
  "duration_seconds": 2531,
  "status": "in_progress",
  "model_usage": {
    "claude-opus-4-7": {
      "input_tokens": 1240,
      "output_tokens": 8132,
      "cache_creation_input_tokens": 4280,
      "cache_read_input_tokens": 91500,
      "cache_creation_5m_input_tokens": 4280,
      "cache_creation_1h_input_tokens": 0,
      "web_search_requests": 0,
      "web_fetch_requests": 0,
      "iterations": 17
    },
    "claude-sonnet-4-6": {
      "input_tokens": 380,
      "output_tokens": 1102,
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 12480,
      "cache_creation_5m_input_tokens": 0,
      "cache_creation_1h_input_tokens": 0,
      "web_search_requests": 0,
      "web_fetch_requests": 0,
      "iterations": 4
    }
  },
  "total_tokens": {
    "input_tokens": 1620,
    "output_tokens": 9234,
    "cache_creation_input_tokens": 4280,
    "cache_read_input_tokens": 103980,
    "cache_creation_5m_input_tokens": 4280,
    "cache_creation_1h_input_tokens": 0,
    "web_search_requests": 0,
    "web_fetch_requests": 0,
    "iterations": 21
  },
  "message_counts": {
    "user": 12,
    "assistant": 21,
    "system": 1,
    "attachment": 3,
    "file-history-snapshot": 4,
    "queue-operation": 2,
    "last-prompt": 5
  },
  "data_source": "stop_hook",
  "jsonl_path": "/Users/martincolley/.claude/projects/-Users-martincolley-sessions-hephaestus-2026-05-20-proj034-foundation-agent-tooling/8e100f96-ce4c-48a8-a872-b85ced0d5b54.jsonl",
  "jsonl_mtime": "2026-05-20T10:42:12.150000+00:00",
  "updated_at": "2026-05-20T10:42:12.500000+00:00"
}
```

---

## Out of scope (later phases)

- **Per-message dollar cost** — Phase 3 (`T-013`), gated on embedded agents
  starting to consume the Anthropic API. Claude Code itself runs on a
  subscription, so Phase 1 / Phase 2 store usage only.
- **Project / programme rollups** — Phase 2 (`T-010`–`T-012`); will be derived
  from this collection rather than stored on it.
- **Multi-currency conversion** — Phase 3 (`T-014`).

---

## References

- Proposal: `planning/projects/PROJ-034-session-cost-observability/proposal.md`
- Spike: Hermes 2026-05-19 — recorded in proposal §Supporting context
- Producers (Phase 1):
  - `agent-tooling/lib/jsonl_scrape.py` (this PR — T-003)
  - `agent-tooling/lib/session_metadata.py` (this PR — T-002)
  - `agent-tooling/hooks/stop-heartbeat.py` (existing minimal writer; T-005 upgrade pending)
