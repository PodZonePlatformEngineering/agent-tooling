"""
provenance_substrate.py — operations on the general-purpose `provenance` collection.

PROJ-029/plannerapi §3.3. A provenance point is a first-class, typed / tagged /
timestamped / supersedable claim: a decision (today buried in STATUS.md
prose), roadmap-synthesis evidence, or — designed for but not migrated as
part of this build — a future candidate home for agent memory. ``type`` is a
deliberately OPEN vocabulary (decision, memory-user, memory-feedback,
memory-project, memory-reference, roadmap-evidence, ...); nothing in this
module hardcodes a closed list against it.

Design mirrors :mod:`lib.brief_substrate` deliberately (same team should read
both without a context switch):

  * The point ID is ``uuid5(NAMESPACE_DNS, provenance_id)`` where
    ``provenance_id`` is the human key
    ``f"provenance/{team_slug}/{type}/{slug_or_date}"`` — deterministic and
    re-upsert-convergent. ``team_slug`` is ``"global"`` for a null
    ``team_id``. The STATUS.md migration script (build item 4) depends on
    being able to reconstruct this same id deterministically from source
    markdown, so this construction is the stable contract — do not change it
    without updating that script too.
  * Built on :mod:`lib.qdrant_http` (stdlib-only write path, PROJ-033/T-016)
    so there is no third-party dependency and a missing API key is loud,
    never a silent zero-write.
  * The one `provenance` named vector is **embed-optional** (PROJ-041/T-002),
    matching ``brief_substrate``: embedded from ``title`` + ``body`` only
    when an embed endpoint is explicitly configured (``OLLAMA_HOST`` env /
    ``ollama_host`` argument), else the point is written vector-less
    (``"vector": {}``) with a stderr note. Every later write —
    :func:`supersede_provenance` — uses ``set_payload`` so the named vector
    survives (F-2-008 / SD-3-001).
  * Supersession never deletes: :func:`supersede_provenance` sets
    ``inactive=true`` and ``superseded_by=<new_id>`` on the old point in one
    write, exactly matching the discipline this session's own memory system
    already follows.

The embed-optional helper (:func:`~lib.session_substrate.maybe_embed`) and
``_now_iso`` are reused from ``session_substrate`` so the embed policy is
defined in exactly one place.
"""

from __future__ import annotations

import uuid
from typing import Optional

from . import qdrant_http, session_substrate

COLLECTION = "provenance"


def provenance_id_for(*, team_slug: Optional[str], type: str, slug: str) -> str:
    """Construct the human key ``provenance/{team_slug}/{type}/{slug}``.

    ``team_slug`` of ``None``/empty maps to ``"global"`` (cross-team/apex-level
    provenance, matching a null ``team_id`` payload). This is the stable
    contract the STATUS.md migration script reconstructs against — do not
    change the format without updating that script too.
    """
    return f"provenance/{team_slug or 'global'}/{type}/{slug}"


def point_id_for(provenance_id: str) -> str:
    """Deterministic Qdrant point ID for a ``provenance_id`` (re-upsert-convergent).

    Same construction family as a brief point (``uuid5(NAMESPACE_DNS, key)``).
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, provenance_id))


# --------------------------------------------------------------------------- #
# Creation (full upsert — the one legitimate PUT …/points, SD-3-001)
# --------------------------------------------------------------------------- #

def upsert_provenance(
    *,
    team_slug: Optional[str],
    type: str,
    slug: str,
    title: str,
    body: str,
    agent: str,
    tags: Optional[list[str]] = None,
    team_id: Optional[str] = None,
    linked_ref: Optional[str] = None,
    inactive: bool = False,
    superseded_by: Optional[str] = None,
    created_at: Optional[str] = None,
    api_key: Optional[str] = None,
    ollama_host: Optional[str] = None,
) -> dict:
    """Create (or re-converge) a `provenance` point.

    Full-point upsert — creation only. **Embed-optional** (PROJ-041/T-002):
    the `provenance` named vector is set from ``title`` + ``body`` only when
    an embed endpoint is explicitly configured (``ollama_host`` argument or
    ``OLLAMA_HOST`` env), else the point is written vector-less. Re-running
    with the same ``(team_slug, type, slug)`` converges on the same point.

    ``created_at`` defaults to the existing point's value on re-converge (if
    any), else now — re-authoring must not silently rewind provenance to
    "just created". ``type`` is intentionally unvalidated against any
    enum — this collection's whole point is an open vocabulary.

    Returns ``{"point_id", "provenance_id", "ok"}``.
    """
    if not title.strip():
        raise ValueError("provenance title is empty")
    if not body.strip():
        raise ValueError("provenance body is empty")

    provenance_id = provenance_id_for(team_slug=team_slug, type=type, slug=slug)
    pid = point_id_for(provenance_id)
    existing = qdrant_http.get_point(pid, collection=COLLECTION, api_key=api_key) or {}
    created_at = created_at or existing.get("created_at") or session_substrate._now_iso()
    updated_at = session_substrate._now_iso()

    embed_input = f"{title}\n\n{body}"
    vec = session_substrate.maybe_embed(embed_input, label="provenance", ollama_host=ollama_host)

    payload = {
        "point_type": "provenance",
        "id": provenance_id,
        "type": type,
        "tags": list(tags or []),
        "team_id": team_id,
        "title": title,
        "body": body,
        "agent": agent,
        "linked_ref": linked_ref,
        "inactive": inactive,
        "superseded_by": superseded_by,
        "created_at": created_at,
        "updated_at": updated_at,
    }
    # Vector-less shape: an EMPTY named-vector map, not an omitted key — Qdrant
    # 400s on a missing `vector` field.
    vector = {"provenance": vec} if vec is not None else {}
    qdrant_http.upsert_points(
        [{"id": pid, "vector": vector, "payload": payload}],
        collection=COLLECTION,
        api_key=api_key,
    )
    return {"point_id": pid, "provenance_id": provenance_id, "ok": True}


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

def get_provenance(
    provenance_id: str, *, api_key: Optional[str] = None, with_vector: bool = False
) -> Optional[dict]:
    """Fetch a `provenance` point payload (or full result with ``with_vector``), or None."""
    return qdrant_http.get_point(
        point_id_for(provenance_id),
        collection=COLLECTION,
        api_key=api_key,
        with_vector=with_vector,
    )


# --------------------------------------------------------------------------- #
# Partial writes (set_payload — never a full upsert; SD-3-001 / F-2-008)
# --------------------------------------------------------------------------- #

def supersede_provenance(
    provenance_id: str,
    new_provenance_id: str,
    *,
    api_key: Optional[str] = None,
) -> dict:
    """Mark ``provenance_id`` inactive, pointing at its replacement — never a delete.

    Sets ``inactive=true`` and ``superseded_by=<point id of new_provenance_id>``
    in the same ``set_payload`` write (preserves the named vector). The
    superseded point stays retrievable by id — this is the discipline this
    session's own memory system already follows: "remove" means
    inactive+superseded_by, not delete.

    Returns ``{"point_id", "superseded_by", "ok"}``.
    """
    pid = point_id_for(provenance_id)
    new_pid = point_id_for(new_provenance_id)
    now = session_substrate._now_iso()
    qdrant_http.set_payload(
        {"inactive": True, "superseded_by": new_pid, "updated_at": now},
        [pid],
        collection=COLLECTION,
        api_key=api_key,
    )
    return {"point_id": pid, "superseded_by": new_pid, "ok": True}


# --------------------------------------------------------------------------- #
# Semantic search
# --------------------------------------------------------------------------- #

def search_provenance(
    query: str,
    *,
    type: Optional[str] = None,
    tags: Optional[list[str]] = None,
    team_id: Optional[str] = None,
    include_inactive: bool = False,
    limit: int = 10,
    api_key: Optional[str] = None,
    ollama_host: Optional[str] = None,
) -> list:
    """Semantic search over `provenance`, filtered then ranked by similarity.

    Excludes ``inactive=true`` points by default (the "current state" view);
    pass ``include_inactive=True`` for a "show me the history of this
    decision" query, which drops the ``inactive`` filter clause entirely so
    both active and superseded points are returned, ranked together.

    ``type`` / ``tags`` / ``team_id`` filters are ANDed with the inactive
    clause (when present) — the same filter-then-rank shape ``briefs`` /
    ``session_substrate`` already use. Raises if no embed endpoint is
    configured (``ollama_host`` argument or ``OLLAMA_HOST`` env) — a search
    with no vector to search against is a caller error, not a silent no-op.

    Returns the raw Qdrant scored-point list (``[{"id", "score", "payload"}, ...]``).
    """
    host = ollama_host or session_substrate.embed_endpoint()
    if not host:
        raise RuntimeError(
            "no embed endpoint configured (set OLLAMA_HOST or pass ollama_host) "
            "— cannot semantically search provenance without one"
        )
    vector = session_substrate.embed_text(query, ollama_host=host)

    must: list = []
    if type is not None:
        must.append({"key": "type", "match": {"value": type}})
    if tags:
        must.append({"key": "tags", "match": {"any": list(tags)}})
    if team_id is not None:
        must.append({"key": "team_id", "match": {"value": team_id}})
    if not include_inactive:
        must.append({"key": "inactive", "match": {"value": False}})

    query_filter = {"must": must} if must else None
    return qdrant_http.search(
        vector,
        collection=COLLECTION,
        using="provenance",
        limit=limit,
        query_filter=query_filter,
        api_key=api_key,
    )
