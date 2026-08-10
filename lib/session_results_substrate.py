"""
session_results_substrate.py — operations on the general-purpose
`session_results` collection.

PROJ-029/T-257 (plannerapi Design view v1, T-250 design doc §3.3/§3.4). A
session result is a whole markdown document (``results/{file}.md`` in an
agent's home repo), written exactly once at session close by
``session_finalise`` — unlike a brief (many sessions, mutable lifecycle
fields), a session result never changes after it is committed, so this module
has no partial-write surface: it is a single idempotent upsert, called both
by the one-time backfill (``tools/session_results_ingest.py``) and by
``session_finalise`` immediately after the result file lands (the "write-time
hook that produces the artefact is also the one that indexes it" shape the
design doc §3.4 argues for, matching ``brief_substrate``'s own posture).

Design mirrors :mod:`lib.brief_substrate` deliberately:

  * The point ID is ``uuid5(NAMESPACE_DNS, "{home_repo}/{filename}")`` —
    deterministic and re-upsert-convergent. ``home_repo`` is folded into the
    key (not just ``filename``) because filenames collide across agent home
    repos (design doc §3.3).
  * Built on :mod:`lib.qdrant_http` (stdlib-only write path) — no third-party
    dependency, loud on a missing API key.
  * The one ``session_result`` named vector is **embed-optional**
    (PROJ-041/T-002 posture, reused via :func:`lib.session_substrate.maybe_embed`):
    embedded from ``title + body`` (matching ``provenance``'s own
    title+body embed-input convention) only when an embed endpoint is
    configured, else the point is written vector-less with a stderr note —
    a home repo without Ollama still gets an indexed (if unsearchable-by-
    vector) point, visible in `list` mode per the design doc §1.3's explicit
    requirement that vector-less points must not be silently dropped from
    browsing.
"""

from __future__ import annotations

import uuid
from typing import Optional

from . import qdrant_http, session_substrate

COLLECTION = "session_results"


def point_id_for(home_repo: str, filename: str) -> str:
    """Deterministic Qdrant point ID for a ``{home_repo}/{filename}`` key
    (re-upsert-convergent) — same ``uuid5(NAMESPACE_DNS, key)`` construction
    as a brief/session point."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{home_repo}/{filename}"))


def project_ref_from_work_item(work_item: Optional[str]) -> Optional[str]:
    """``"PROJ-011/T-044"`` -> ``"PROJ-011"``; ``None``/empty -> ``None``.

    First-class ``project_ref`` derivation (design doc §3.3) so the Design
    view's project filter is a plain payload match, not a client-side
    string split against ``work_item`` on every query.
    """
    if not work_item or "/" not in work_item:
        return work_item.strip() or None if work_item else None
    return work_item.split("/", 1)[0].strip() or None


def upsert_result(
    *,
    home_repo: str,
    filename: str,
    body: str,
    work_item: Optional[str],
    agent: str,
    date: str,
    title: Optional[str] = None,
    created_at: Optional[str] = None,
    api_key: Optional[str] = None,
    ollama_host: Optional[str] = None,
) -> dict:
    """Create (or re-converge) the canonical `session_result` point.

    Full-point upsert — a session result is written exactly once (no
    lifecycle mutation the way a brief has), so there is no separate
    partial-write path the way ``brief_substrate.append_session_id`` /
    ``set_status`` exist. Re-running with the same ``home_repo``/``filename``
    converges on the same point (idempotent — safe for both the backfill and
    a resumed/retried finalise hook).

    ``created_at`` should be the git commit date of the file when known (the
    design doc's own reasoning: frontmatter/author-typed dates can lag or
    predate the actual commit) — callers that don't have it yet (e.g. the
    live finalise-hook write, where "now" IS the commit time) may omit it and
    get the current UTC timestamp.

    Returns ``{"point_id", "project_ref", "ok"}``.
    """
    if not body.strip():
        raise ValueError(f"session result body is empty for {home_repo}/{filename}")

    pid = point_id_for(home_repo, filename)
    if not title:
        title = filename[:-3] if filename.endswith(".md") else filename
    project_ref = project_ref_from_work_item(work_item)
    created_at = created_at or session_substrate._now_iso()
    vec = session_substrate.maybe_embed(
        f"{title}\n\n{body}", label="session_result", ollama_host=ollama_host
    )
    payload = {
        "point_type": "session_result",
        "id": f"{home_repo}/{filename}",
        "home_repo": home_repo,
        "filename": filename,
        "work_item": work_item,
        "project_ref": project_ref,
        "agent": agent,
        "date": date,
        "title": title,
        "body": body,
        "created_at": created_at,
    }
    # Vector-less shape: an EMPTY named-vector map, not an omitted key — Qdrant
    # 400s on a missing `vector` field (same gotcha brief_substrate documents).
    vector = {"session_result": vec} if vec is not None else {}
    qdrant_http.upsert_points(
        [{"id": pid, "vector": vector, "payload": payload}],
        collection=COLLECTION,
        api_key=api_key,
    )
    return {"point_id": pid, "project_ref": project_ref, "ok": True}


def get_result(
    home_repo: str, filename: str, *, api_key: Optional[str] = None
) -> Optional[dict]:
    """Fetch a `session_result` point's payload by its human key, or None."""
    return qdrant_http.get_point(
        point_id_for(home_repo, filename), collection=COLLECTION, api_key=api_key
    )
