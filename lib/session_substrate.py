"""
session_substrate.py — operations on the unified `session_substrate` collection.

PROJ-039 Stage 5 (CC-318). The single place that knows the shape of the canonical
`session` point (DTD § 3.1) and the upsert discipline that protects its named
vectors (SD-3-001). Built on :mod:`lib.qdrant_http` (the stdlib write path from
PROJ-033/T-016) so there is no third-party dependency and a missing API key is
loud, never a silent zero-write.

Upsert discipline (the load-bearing invariant, repeated at every call site):
  - **Creation only** → full upsert (:func:`create_session_point`). This is the
    one legitimate ``PUT …/points`` on a `session` point (R-007, Team-Lead side).
  - **Every later write** → ``set_payload`` (per-Stop append, response, rollups,
    event_refs) or ``update-vectors`` (the `response` vector). A full upsert on an
    existing point would null the `brief`/`response` named vectors (F-2-008).

The point ID is ``uuid5(NAMESPACE_DNS, session_id)`` — identical to
``lib/sessions_upsert.point_id_for`` — so the per-Stop / session-end target is
deterministically addressable without a lookup (DTD § 2.3).
"""

from __future__ import annotations

import json
import os
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import jsonl_scrape, qdrant_http

COLLECTION = "session_substrate"

EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768


def point_id_for(session_id: str) -> str:
    """Deterministic Qdrant point ID for a session_id (matches sessions_upsert)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, session_id))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def embed_text(text: str, *, ollama_host: Optional[str] = None,
               timeout: float = 30.0) -> list[float]:
    """Embed ``text`` with nomic-embed-text via Ollama; returns a 768-dim vector.

    Stdlib-only (urllib) to match qdrant_http — no `requests` dependency. Raises
    on transport/decoding failure; callers in best-effort hooks should catch.
    """
    host = ollama_host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    body = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(
        url=f"{host}/api/embeddings",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        parsed = json.loads(resp.read().decode("utf-8"))
    vec = parsed.get("embedding")
    if not isinstance(vec, list) or not vec:
        raise ValueError("Ollama returned no embedding")
    return vec


# --------------------------------------------------------------------------- #
# Creation (full upsert — the one legitimate PUT …/points, R-007 / SD-3-001)
# --------------------------------------------------------------------------- #

def create_session_point(
    *,
    session_id: str,
    agent: str,
    work_item: str,
    brief_text: str,
    target_agent: Optional[str] = None,
    dispatch_ts: Optional[str] = None,
    api_key: Optional[str] = None,
    ollama_host: Optional[str] = None,
) -> dict:
    """Create the canonical `session` point carrying the brief (Team-Lead, dispatch).

    Full-point upsert — creation only. Sets the `brief` named vector from
    ``brief_text``; the `response` vector is added later at session-end via
    update-vectors. Returns ``{"point_id", "ok"}``.
    """
    pid = point_id_for(session_id)
    dispatch_ts = dispatch_ts or _now_iso()
    brief_vector = embed_text(brief_text, ollama_host=ollama_host)
    payload = {
        "point_type": "session",
        "session_id": session_id,
        "agent": agent,
        "work_item": work_item,
        "brief": {
            "text": brief_text,
            "dispatch_ts": dispatch_ts,
            "target_agent": target_agent or agent,
        },
        "session_stop": [],
        "response": None,
        "rollup": None,
    }
    qdrant_http.upsert_points(
        [{"id": pid, "vector": {"brief": brief_vector}, "payload": payload}],
        collection=COLLECTION,
        api_key=api_key,
    )
    return {"point_id": pid, "ok": True}


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

def get_session_point(
    session_id: str, *, api_key: Optional[str] = None, with_vector: bool = False
) -> Optional[dict]:
    """Fetch the `session` point (payload, or full result with ``with_vector``)."""
    return qdrant_http.get_point(
        point_id_for(session_id),
        collection=COLLECTION,
        api_key=api_key,
        with_vector=with_vector,
    )


ACTIVE_STATUSES = ("ready", "in_progress", "blocked")


def active_work_items(
    agent: str,
    *,
    statuses: tuple[str, ...] = ACTIVE_STATUSES,
    api_key: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Scroll the agent's active `task`/`work_item` points (§ 2.2 step 3).

    Returns the list of point payloads with ``point_type=task`` for ``agent``
    whose ``status`` is in ``statuses``. Used by the session-start materialise.
    """
    body = {
        "filter": {
            "must": [
                {"key": "point_type", "match": {"value": "task"}},
                {"key": "agent", "match": {"value": agent}},
                {"key": "status", "match": {"any": list(statuses)}},
            ]
        },
        "limit": limit,
        "with_payload": True,
    }
    resp = qdrant_http.scroll(collection=COLLECTION, body=body, api_key=api_key)
    points = resp.get("result", {}).get("points", [])
    return [p.get("payload", {}) for p in points]


def find_session_id_by_work_item(
    agent: str, work_item: str, *, api_key: Optional[str] = None
) -> Optional[str]:
    """Resolve a pre-dispatch session point by agent+work_item (§ 2.2 step 2)."""
    body = {
        "filter": {
            "must": [
                {"key": "point_type", "match": {"value": "session"}},
                {"key": "agent", "match": {"value": agent}},
                {"key": "work_item", "match": {"value": work_item}},
            ]
        },
        "limit": 1,
        "with_payload": True,
    }
    resp = qdrant_http.scroll(collection=COLLECTION, body=body, api_key=api_key)
    points = resp.get("result", {}).get("points", [])
    if points:
        return points[0].get("payload", {}).get("session_id")
    return None


# --------------------------------------------------------------------------- #
# Per-Stop append (set_payload, read-modify-write — R-009 / SD-3-001)
# --------------------------------------------------------------------------- #

def append_session_stop(
    session_id: str,
    entry: dict,
    *,
    api_key: Optional[str] = None,
) -> dict:
    """Append one entry to ``session_stop[]`` via set_payload (never full upsert).

    Read-modify-write: Qdrant has no native array-push, and ``set_payload``
    overwrites only the named key while preserving all named vectors (F-2-008),
    so this is safe where a full upsert would null `brief`/`response`.
    Returns ``{"point_id", "length", "ok"}``.
    """
    pid = point_id_for(session_id)
    existing = qdrant_http.get_point(pid, collection=COLLECTION, api_key=api_key)
    current = []
    if existing and isinstance(existing.get("session_stop"), list):
        current = existing["session_stop"]
    current = list(current) + [entry]
    qdrant_http.set_payload(
        {"session_stop": current}, [pid], collection=COLLECTION, api_key=api_key
    )
    return {"point_id": pid, "length": len(current), "ok": True}


# --------------------------------------------------------------------------- #
# session-end writes (set_payload + update-vectors — R-010..014 / SD-3-001)
# --------------------------------------------------------------------------- #

def upsert_response(
    session_id: str,
    *,
    text: str,
    status_transition: Optional[str] = None,
    event_refs: Optional[list] = None,
    end_ts: Optional[str] = None,
    api_key: Optional[str] = None,
    ollama_host: Optional[str] = None,
) -> dict:
    """Write the `response` object (set_payload) + patch the `response` vector
    (update-vectors). Two partial writes — never a full upsert (§ 2.4 step 1)."""
    pid = point_id_for(session_id)
    response = {
        "text": text,
        "status_transition": status_transition,
        "event_refs": event_refs or [],
        "end_ts": end_ts or _now_iso(),
    }
    qdrant_http.set_payload(
        {"response": response}, [pid], collection=COLLECTION, api_key=api_key
    )
    response_vector = embed_text(text, ollama_host=ollama_host)
    qdrant_http.update_vectors(
        [{"id": pid, "vector": {"response": response_vector}}],
        collection=COLLECTION,
        api_key=api_key,
    )
    return {"point_id": pid, "ok": True}


def attach_event_refs(
    session_id: str, event_refs: list, *, api_key: Optional[str] = None
) -> dict:
    """Attach task-event measurement refs to the session point (R-012)."""
    pid = point_id_for(session_id)
    qdrant_http.set_payload(
        {"event_refs": event_refs}, [pid], collection=COLLECTION, api_key=api_key
    )
    return {"point_id": pid, "ok": True}


def compute_rollup(jsonl_path: str | Path) -> dict:
    """Derive ``{tool_usage, cost_tokens}`` from the session JSONL (R-013/R-014).

    Uses the canonical :func:`lib.jsonl_scrape.scrape` aggregation so the numbers
    reconcile exactly with a fresh re-scrape (DT-006). ``cost_tokens`` mirrors the
    per-model bucket; ``tool_usage`` is per-tool invocation counts. Both are
    unvectorised (Q-002).
    """
    scraped = jsonl_scrape.scrape(jsonl_path)
    return {
        "tool_usage": scraped.get("tool_usage", {}),
        "cost_tokens": scraped.get("model_usage", {}),
    }


def attach_rollup(
    session_id: str,
    rollup: dict,
    *,
    api_key: Optional[str] = None,
) -> dict:
    """Attach ``rollup = {tool_usage, cost_tokens}`` via set_payload (unvectorised)."""
    pid = point_id_for(session_id)
    qdrant_http.set_payload(
        {"rollup": rollup}, [pid], collection=COLLECTION, api_key=api_key
    )
    return {"point_id": pid, "ok": True}
