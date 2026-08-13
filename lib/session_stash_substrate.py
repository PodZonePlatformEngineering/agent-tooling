"""
session_stash_substrate.py — operations on the `session_stash` Qdrant
collection (PROJ-039/T-255, design doc
podzoneTeam/planning/projects/PROJ-039-unified-session-substrate/
t254-session-stash-design.md).

A session-stash entry is transient "resume here" scratch: a session
mid-flight (compaction, a headless limit_stop retry, or a deliberate
explicit handoff) stashes a resume narrative keyed by `brief_id`, so the
next session materialising that same brief can pick it up. Mirrors
:mod:`lib.session_results_substrate`'s shape — single deterministic-ID
upsert, no partial-write surface beyond the pop transition (T-256, out of
scope here) — with one difference: push here is a *replace*, not a
write-once. Every push overwrites `session_id`/`trigger`/`content`/
`pushed_at` and resets `status` to `"active"` (design doc §3); `brief_id`
is the addressing key and is never overwritten as payload.

Payload-only — no named vector (design doc §2): this is exact-key lookup
scratch, not a semantic-search corpus, so the embed-optional machinery
:mod:`lib.session_results_substrate` carries is skipped entirely. Points are
written with an EMPTY named-vector map (`"vector": {}`), matching the
`training_token_registry` payload-only precedent — never an omitted
`vector` key (Qdrant 400s on that).

Built on :mod:`lib.qdrant_http` (stdlib-only write path) — no third-party
dependency, loud on a missing API key.
"""

from __future__ import annotations

import uuid
from typing import Optional

from . import qdrant_http, session_substrate

COLLECTION = "session_stash"

VALID_TRIGGERS = ("compaction", "limit_stop", "explicit")


def point_id_for(brief_id: str) -> str:
    """Deterministic Qdrant point ID for a `brief_id` key (re-upsert-
    convergent) — `uuid5(NAMESPACE_DNS, f"stash/{brief_id}")` per design doc
    §2, matching the construction `session_substrate.point_id_for` and
    `session_results_substrate.point_id_for` already use for their own
    keys."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"stash/{brief_id}"))


def push(
    brief_id: str,
    session_id: str,
    agent: str,
    work_item: Optional[str],
    trigger: str,
    content: str,
    *,
    api_key: Optional[str] = None,
) -> dict:
    """Push (full-upsert-replace) the session-stash entry for `brief_id`.

    A second push during the same brief overwrites the first at the same
    deterministic point ID — it does not grow a list (design doc §3). Every
    call resets `status` to `"active"` (re-arms a previously-consumed entry)
    and overwrites `session_id`/`trigger`/`content`/`pushed_at`. `brief_id`
    is the addressing key, never treated as mutable payload here.

    Idempotent-convergent: re-running with the same inputs converges on the
    same point without erroring or duplicating; re-running with different
    `content` replaces it (proven by the test suite).

    Returns ``{"point_id", "ok"}``.
    """
    if not brief_id or not brief_id.strip():
        raise ValueError("session-stash push requires a non-empty brief_id")
    if not content or not content.strip():
        raise ValueError(f"session-stash push content is empty for brief_id {brief_id!r}")
    if trigger not in VALID_TRIGGERS:
        raise ValueError(
            f"session-stash push trigger must be one of {VALID_TRIGGERS!r}, got {trigger!r}"
        )

    pid = point_id_for(brief_id)
    payload = {
        "point_type": "session_stash",
        "brief_id": brief_id,
        "session_id": session_id,
        "agent": agent,
        "work_item": work_item,
        "trigger": trigger,
        "content": content,
        "pushed_at": session_substrate._now_iso(),
        "status": "active",
        "consumed_at": None,
        "consumed_by_session_id": None,
    }
    # Payload-only shape: an EMPTY named-vector map, not an omitted key —
    # Qdrant 400s on a missing `vector` field (same gotcha
    # brief_substrate/session_results_substrate document).
    qdrant_http.upsert_points(
        [{"id": pid, "vector": {}, "payload": payload}],
        collection=COLLECTION,
        api_key=api_key,
    )
    return {"point_id": pid, "ok": True}


def get_stash(brief_id: str, *, api_key: Optional[str] = None) -> Optional[dict]:
    """Fetch the `session_stash` point payload for `brief_id`, or None."""
    return qdrant_http.get_point(
        point_id_for(brief_id), collection=COLLECTION, api_key=api_key
    )
