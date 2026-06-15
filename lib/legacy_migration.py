"""
legacy_migration.py — DT-014 / AC-007 additive migration into `session_substrate`.

Phase C step 2 (PROJ-039/T-011 sub-phase C1). Folds the **active** tasking state
of the six legacy collections into the unified `session_substrate`, discriminated
by `point_type` (DTD § 3.2), **additively** — it only ever issues writes to
`session_substrate`; the source collections are never read-modified or deleted
(that is the C4 drop gate, operator-confirmed separately). C1 is therefore fully
reversible: undo == delete the migrated point ids.

Scope (operator decision 2026-06-15 — "broadest active union"):

  point_type=task    ← tasks ∪ work_items, status ∈ ACTIVE_STATUSES, deduped by
                       {proj_id}/{task_id} (work_items wins — richer hierarchy)
  point_type=session ← sessions, status == "in_progress"
  point_type=event   ← all task_events + all prompt_logs (the activity audit trail)

Audit-trail preservation (AC-007): every migrated point keeps the **entire** source
payload verbatim and adds a ``_migration`` provenance block, so brief dispatch,
status and timeline remain retrievable from the unified point. ``agent`` and
``status`` are *normalised* onto the point (work_items names the owner ``owner``;
``tasks`` writes ``in-progress`` where the substrate query expects ``in_progress``)
so migrated task points are found by :func:`lib.session_substrate.active_work_items`.

Vectorless by design: task/event/session-metadata points are retrieved by payload
filter (point_type/agent/status — F-2-003 full-scan is fine at this cardinality),
not by ANN, so they carry no named vector. (Re-)embedding on-disk briefs/responses
into the `brief`/`response` named vectors is a separate, Thoth-owned ingest.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from . import qdrant_http, session_substrate

COLLECTION = session_substrate.COLLECTION
ACTIVE_STATUSES = session_substrate.ACTIVE_STATUSES  # (ready, in_progress, blocked)

# Source collections and the point_type each contributes to.
TASK_SOURCES = ("work_items", "tasks")   # order = dedup precedence (work_items wins)
SESSION_SOURCE = "sessions"
EVENT_SOURCES = ("task_events", "prompt_logs")

_STATUS_ALIASES = {"in-progress": "in_progress", "inprogress": "in_progress"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_status(status: Optional[str]) -> Optional[str]:
    if not status:
        return status
    return _STATUS_ALIASES.get(status, status)


def _migrated_id(kind: str, key: str) -> str:
    """Deterministic point id for a migrated record (idempotent re-runs)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"proj039-migrate:{kind}:{key}"))


def _scroll_all(
    collection: str, *, qdrant_url: str, api_key: Optional[str], page: int = 1000
) -> list[dict]:
    """Scroll every point of ``collection`` (payload only)."""
    out: list[dict] = []
    next_off = None
    while True:
        body: dict = {"limit": page, "with_payload": True, "with_vector": False}
        if next_off is not None:
            body["offset"] = next_off
        resp = qdrant_http.scroll(
            collection=collection, body=body, qdrant_url=qdrant_url, api_key=api_key
        )
        res = resp.get("result", {})
        out.extend(res.get("points", []))
        next_off = res.get("next_page_offset")
        if not next_off:
            break
    return out


def _task_dedup_key(payload: dict) -> str:
    """Stable, project-qualified key for a task/work_item across both sources."""
    proj = payload.get("proj_id") or ""
    task = payload.get("task_id") or ""
    if proj and task:
        return f"{proj}/{task}"
    return (
        payload.get("programme_label")
        or payload.get("task_slug")
        or payload.get("title")
        or payload.get("summary")
        or ""
    )


@dataclass
class MigrationPoint:
    point_id: str
    point_type: str
    source_collection: str
    dedup_key: str
    payload: dict

    def to_qdrant(self) -> dict:
        # vectorless: an empty named-vector map for a named-vector collection.
        return {"id": self.point_id, "vector": {}, "payload": self.payload}


@dataclass
class Migration:
    points: list = field(default_factory=list)
    # provenance / reconciliation
    source_counts: dict = field(default_factory=dict)      # collection → total
    selected_counts: dict = field(default_factory=dict)    # collection → selected
    type_counts: Counter = field(default_factory=Counter)  # point_type → migrated
    dedup_dropped: int = 0

    def summary(self) -> str:
        lines = ["=== DT-014 migration plan (additive) ==="]
        lines.append("source totals:   " + ", ".join(
            f"{k}={v}" for k, v in sorted(self.source_counts.items())))
        lines.append("selected/active: " + ", ".join(
            f"{k}={v}" for k, v in sorted(self.selected_counts.items())))
        lines.append(f"deduped task overlaps dropped: {self.dedup_dropped}")
        lines.append("migrated by point_type: " + ", ".join(
            f"{k}={v}" for k, v in sorted(self.type_counts.items())))
        lines.append(f"TOTAL points to write: {len(self.points)}")
        return "\n".join(lines)


def collect_migration(
    *, qdrant_url: str = qdrant_http.CLOUD_QDRANT_URL, api_key: Optional[str] = None
) -> Migration:
    """Read the legacy collections and build the (additive) migration set.

    Pure read — issues no writes. Apply with :func:`write_migration`.
    """
    m = Migration()
    stamp = _now_iso()

    # ---- point_type=task : work_items ∪ tasks, active, deduped --------------
    seen_keys: dict[str, str] = {}   # dedup_key → winning source
    for src in TASK_SOURCES:
        pts = _scroll_all(src, qdrant_url=qdrant_url, api_key=api_key)
        m.source_counts[src] = len(pts)
        selected = 0
        for p in pts:
            payload = dict(p.get("payload") or {})
            status = _normalise_status(payload.get("status"))
            if status not in ACTIVE_STATUSES:
                continue
            selected += 1
            key = _task_dedup_key(payload)
            if key and key in seen_keys:
                m.dedup_dropped += 1
                continue
            if key:
                seen_keys[key] = src
            agent = payload.get("agent") or payload.get("owner")
            payload.update({
                "point_type": "task",
                "status": status,
                "agent": agent,
                "_migration": {
                    "source_collection": src,
                    "source_point_id": p.get("id"),
                    "dedup_key": key,
                    "migrated_at": stamp,
                },
            })
            m.points.append(MigrationPoint(
                point_id=_migrated_id("task", key or str(p.get("id"))),
                point_type="task",
                source_collection=src,
                dedup_key=key,
                payload=payload,
            ))
            m.type_counts["task"] += 1
        m.selected_counts[src] = selected

    # ---- point_type=session : in_progress sessions -------------------------
    sess = _scroll_all(SESSION_SOURCE, qdrant_url=qdrant_url, api_key=api_key)
    m.source_counts[SESSION_SOURCE] = len(sess)
    sel = 0
    for p in sess:
        payload = dict(p.get("payload") or {})
        if payload.get("status") != "in_progress":
            continue
        sel += 1
        session_id = payload.get("session_id")
        payload.update({
            "point_type": "session",
            "_migration": {
                "source_collection": SESSION_SOURCE,
                "source_point_id": p.get("id"),
                "migrated_at": stamp,
            },
        })
        # session points keep the substrate-native deterministic id
        pid = (session_substrate.point_id_for(session_id)
               if session_id else _migrated_id("session", str(p.get("id"))))
        m.points.append(MigrationPoint(
            point_id=pid,
            point_type="session",
            source_collection=SESSION_SOURCE,
            dedup_key=session_id or "",
            payload=payload,
        ))
        m.type_counts["session"] += 1
    m.selected_counts[SESSION_SOURCE] = sel

    # ---- point_type=event : all task_events + all prompt_logs --------------
    for src in EVENT_SOURCES:
        pts = _scroll_all(src, qdrant_url=qdrant_url, api_key=api_key)
        m.source_counts[src] = len(pts)
        m.selected_counts[src] = len(pts)
        for p in pts:
            payload = dict(p.get("payload") or {})
            payload.update({
                "point_type": "event",
                "_migration": {
                    "source_collection": src,
                    "source_point_id": p.get("id"),
                    "migrated_at": stamp,
                },
            })
            m.points.append(MigrationPoint(
                point_id=_migrated_id("event", f"{src}:{p.get('id')}"),
                point_type="event",
                source_collection=src,
                dedup_key=str(p.get("id")),
                payload=payload,
            ))
            m.type_counts["event"] += 1

    return m


def write_migration(
    migration: Migration,
    *,
    qdrant_url: str = qdrant_http.CLOUD_QDRANT_URL,
    api_key: Optional[str] = None,
    dry_run: bool = True,
    batch: int = 100,
) -> dict:
    """Upsert the migration points into `session_substrate` (additive only).

    With ``dry_run`` (the default) nothing is written; returns the would-write
    count. Never touches the source collections.
    """
    if dry_run:
        return {"written": 0, "dry_run": True, "total": len(migration.points)}
    written = 0
    pts = [mp.to_qdrant() for mp in migration.points]
    for i in range(0, len(pts), batch):
        chunk = pts[i:i + batch]
        qdrant_http.upsert_points(
            chunk, collection=COLLECTION, qdrant_url=qdrant_url, api_key=api_key
        )
        written += len(chunk)
    return {"written": written, "dry_run": False, "total": len(migration.points)}


def reconcile(
    migration: Migration,
    *,
    qdrant_url: str = qdrant_http.CLOUD_QDRANT_URL,
    api_key: Optional[str] = None,
) -> dict:
    """Compare planned point_type counts against what is live in the substrate.

    Counts live `session_substrate` points per point_type (restricted to the
    migrated ids' types) and checks they match the migration plan. Returns a
    per-type ``{planned, live, ok}`` map plus an overall ``ok``.
    """
    out: dict = {"types": {}, "ok": True}
    for ptype, planned in migration.type_counts.items():
        body = {
            "filter": {"must": [{"key": "point_type", "match": {"value": ptype}}]},
            "limit": 0,
            "exact": True,
        }
        url = f"{qdrant_url}/collections/{COLLECTION}/points/count"
        live = qdrant_http.request_json(
            "POST", url, payload=body, api_key=api_key
        ).get("result", {}).get("count", 0)
        ok = live >= planned
        out["types"][ptype] = {"planned": planned, "live": live, "ok": ok}
        out["ok"] = out["ok"] and ok
    return out
