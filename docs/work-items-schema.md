# `work_items` Qdrant collection — payload schema

**Status:** v1 (PROJ-033/T-008) — initial ingest from `planning/team-tasklist.md`.
**Owners:** PROJ-033 / Thoth.
**Collection:** `work_items` on cloud Qdrant
(`https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io`).
**Point ID:** `uuid5(NAMESPACE_URL, source_path)` — re-ingest is idempotent.
**Vector:** 768-dim, cosine. Embed source: `title + " — " + description` via
workstation Ollama (`nomic-embed-text`). Embedding-failed points fall back to
a zero vector and are flagged in ingest logs for re-embed.

This document is the contract. Writers (ingest tool, future hooks, plannerapi)
must produce payloads matching this shape. Readers (session-start variants,
plannerapi, Hermes consolidation) consume against it.

The schema is **additive**: new fields may be appended in a later phase, but
existing fields will not be renamed or have their types changed without a
documented migration.

---

## Unified Task Object Model

The current markdown tasklist (114 KB, structurally fragile) is replaced by a
recursive `WorkItem` object. All levels of the existing hierarchy are the same
object class — `type` is a display filter only.

| `type` value | Scope | Source today |
|---|---|---|
| `roadmap` | Full platform direction | Not yet ingested. |
| `programme` | PRG-XXX | Derived from `agenticflows/operations/task-naming.md`. |
| `project` | PROJ-XXX | `## PROJ-NNN:` headings in `planning/team-tasklist.md`. |
| `task` | T-XXX within a project | Table rows under each project section. |
| `subtask` | Inline within a task | Not yet ingested. |
| `session_task` | Steps in a brief's session plan | Not yet ingested. |

Subtask + session_task layers don't have markdown sources yet; only programme,
project, and task points are ingested by `tools/ingest-work-items.py` today.

---

## Fields

### Identity

| Field | Type | Indexed | Notes |
|---|---|---|---|
| `type` | keyword | yes | One of the six type values above. |
| `title` | string | no | Display title (project heading text, task summary, etc.). |
| `description` | string | no | Long-form text. Combined with `title` for the embedding. |
| `status` | keyword | yes | `draft` \| `ready` \| `in_progress` \| `blocked` \| `complete` \| `archived` |
| `owner` | keyword | yes | Canonical agent name (`hermes`, `hephaestus`, `atlas`, `thoth`) or `martin`. `""` if not yet assigned. |

### Hierarchy + graph links

| Field | Type | Indexed | Notes |
|---|---|---|---|
| `parent.id` | keyword (nested) | yes | UUID of the parent WorkItem. Empty for roadmap-level items. |
| `parent.type` | keyword (nested) | no | Type of the parent (`programme`, `project`, etc.). |
| `children` | list[object] | no | `[{id, type, title}]` — populated for project + programme rows by the ingest tool. |
| `depends_on` | list[object] | no | `[{id, type, title, status}]` — gates this item. Not auto-populated in v1. |
| `enables` | list[object] | no | `[{id, type, title}]` — this unblocks. Not auto-populated in v1. |
| `related` | list[object] | no | `[{id, type, title, link_type}]` — cross-reference. Not auto-populated in v1. |

**Graph walks** happen via `parent.id` filters: to list a project's children,
scroll with `filter={"must":[{"key":"parent.id","match":{"value":<proj_uuid>}}]}`.
The `children` array on the parent is a denormalised convenience for read-once
fetches.

### Legacy compatibility (cross-reference with the markdown tasklist)

| Field | Type | Indexed | Notes |
|---|---|---|---|
| `programme_label` | keyword | yes | Free-form legacy id: `PROJ-033`, `PRG-002`, `T-008`. |
| `proj_id` | keyword | yes | E.g. `PROJ-031`. Empty on programme rows. |
| `task_id` | keyword | yes | E.g. `T-001`, `Setup/T-002`, `S1/T-005`. Empty on programme + project rows. |
| `cc_number` | keyword | yes | E.g. `CC-225`. Empty when unassigned. |
| `programme` | keyword | yes | Programme shortform (`communication`, `platform-buildout`, …) resolved via `agenticflows/operations/task-naming.md`. |
| `project_slug` | keyword | yes | Project shortform (`agenticflows-schema`, …) resolved via the same source. |
| `task_slug` | keyword | no | Lowercase hyphenated summary slug, max 30 chars. |

### Provenance + temporal

| Field | Type | Indexed | Notes |
|---|---|---|---|
| `raised_at` | datetime \| null | no | ISO-8601 when the item was first raised. |
| `raised_by` | keyword | no | Agent or operator. |
| `completed_at` | datetime \| null | no | Set when status transitions to `complete` or `archived`. |
| `updated_at` | datetime | no | Ingest timestamp. |

### Traceability

| Field | Type | Indexed | Notes |
|---|---|---|---|
| `brief_path` | string | no | Relative path to the incoming brief, when one exists. |
| `outbox_ref` | string | no | Session outbox path that produced this item. |
| `pr_refs` | list[keyword] | no | E.g. `["agent-tooling#15", "podzoneAgentTeam#76"]`. |

### Source tracking

| Field | Type | Indexed | Notes |
|---|---|---|---|
| `source_path` | string | no | Origin of the point — e.g. `planning/team-tasklist.md#PROJ-035/T-002`. Drives the point UUID via `uuid5(NAMESPACE_URL, source_path)`. |
| `source_kind` | keyword | yes | `tasklist_row` \| `tasklist_project_header` \| `programme_map` \| `proposal_section` \| `brief_file`. |

---

## Status enum

The WorkItem enum is narrower than the legacy `tasks` collection. Mapping
applied by `tools/ingest-work-items.py`:

| Legacy (`tasks` collection) | WorkItem (`work_items`) |
|---|---|
| `ready` | `ready` |
| `in-progress` | `in_progress` |
| `blocked` | `blocked` |
| `paused` | `blocked` |
| `complete` | `complete` |
| `superseded` | `archived` |
| (unknown) | `draft` |

---

## Indexes

Created at collection-creation time (via `create_payload_index`):

- `type`, `status`, `owner`, `parent.id` — the four filters used by every
  session-start build variant.
- `programme_label`, `proj_id`, `task_id`, `cc_number` — legacy cross-reference
  (Hermes consolidation, markdown reconciliation).
- `programme`, `project_slug` — programme/project rollups.
- `source_kind` — distinguishes tasklist-derived points from future writers.

---

## Worked example — a task point

```json
{
  "id": "8a7f5b50-7c9c-50a4-9b3f-3f5f9c1e2d44",
  "type": "task",
  "title": "Define work_items Qdrant collection + initial ingest",
  "description": "T-008 gates three programmes... (full row summary)",
  "status": "ready",
  "owner": "thoth",
  "parent": { "id": "fce8…", "type": "project" },
  "children": [],
  "depends_on": [],
  "enables": [],
  "related": [],
  "programme_label": "PROJ-033/T-008",
  "proj_id": "PROJ-033",
  "task_id": "T-008",
  "cc_number": "",
  "programme": "communication",
  "project_slug": "materialised-context",
  "task_slug": "define-work_items-qdrant-collection",
  "raised_at": null,
  "raised_by": "",
  "completed_at": null,
  "updated_at": "2026-05-22T13:30:00+00:00",
  "brief_path": "team/thoth/incoming/2026-05-22-proj033-t008-work-items-collection.md",
  "outbox_ref": "",
  "pr_refs": [],
  "source_path": "planning/team-tasklist.md#PROJ-033/T-008",
  "source_kind": "tasklist_row"
}
```

---

## Out of scope (v1)

- **Subtask / session_task ingest** — no markdown source yet; will be
  populated by plannerapi (PROJ-029) when briefs are decomposed.
- **`depends_on` / `enables` / `related` auto-population** — the markdown
  tasklist does not encode these as machine-readable links. Hermes will
  layer them in by hand (or via PROJ-035 Phase 3 rewrite).
- **Live sync** with the markdown tasklist — re-ingest is manual at
  consolidation time. A `PostToolUse` hook can be added later.
- **Roadmap-level point** — single placeholder, deferred until roadmapTeam
  publishes a structured roadmap document.

---

## References

- Proposal: `planning/projects/PROJ-033-materialised-context/proposal.md`
  §Unified Task Object Model (lines 242–310).
- Programme map: `agenticflows/operations/task-naming.md`.
- Sibling schema (sessions): `agent-tooling/docs/sessions-schema.md`.
- Ingest tool: `podzoneAgentTeam/team/thoth/tools/ingest-work-items.py`.
- Canonical ingest path: `team/thoth/vector-catalog.md` — workstation Ollama
  embed → cloud Qdrant.
