"""
brief_substrate.py — operations on the general-purpose `briefs` collection.

PROJ-039/T-043 (brief-as-first-class-citizen). A **brief is not linked to a
specific session**: it is authored once (per trainee, or per dispatch) and worked
across *many* sessions. Briefs become first-class points in their own cloud
Qdrant collection; sessions reference a brief by ``brief_id`` and each session's
runtime id is appended to the brief's ``session_ids[]`` (the reverse link) at
SessionStart. This decouples the brief from the pinned-session-id ceremony the
`session_substrate` launch flow required (ADR-008 D1/D5 evolve, not reopen).

Design mirrors :mod:`lib.session_substrate` deliberately (same team should read
both without a context switch):

  * The point ID is ``uuid5(NAMESPACE_DNS, brief_id)`` — deterministic and
    re-upsert-convergent, exactly like a session point is keyed by session_id.
    Re-authoring the same ``brief_id`` converges on the same point (idempotent).
  * Built on :mod:`lib.qdrant_http` (stdlib-only write path, PROJ-033/T-016) so
    there is no third-party dependency and a missing API key is loud, never a
    silent zero-write.
  * The one `brief` named vector is **embed-optional** (PROJ-041/T-002) and set
    at **creation only** (a full upsert): it is embedded from ``body`` only when
    an embed endpoint is explicitly configured (``OLLAMA_HOST`` env /
    ``ollama_host`` argument — team leads on trainer workstations author briefs
    too); otherwise the point is written vector-less (``"vector": {}``) with a
    stderr note, and the PROJ-042 enrichment job embeds in retrospect. Every
    later write — :func:`append_session_id`, :func:`set_status`,
    :func:`complete_brief` — uses ``set_payload`` so a named vector survives
    (F-2-008 / SD-3-001). When embedding, the embed *input* is bounded (T-027)
    so a large brief body cannot 500 the embed model and lose its vector; the
    full ``body`` is always stored unchanged.

The embed-optional helper (:func:`~lib.session_substrate.maybe_embed`) and
``_now_iso`` are reused from ``session_substrate`` so the embed policy is
defined in exactly one place.
"""

from __future__ import annotations

import uuid
from typing import Optional

from . import qdrant_http, session_substrate

COLLECTION = "briefs"

# Status lifecycle (brief § "Payload"):
#   draft → approved → in_progress → complete | superseded
# `approved` remains the ADR-008 D5 **execution gate**: materialise refuses to
# stand up a session for a brief that has not been approved.
STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETE = "complete"
STATUS_SUPERSEDED = "superseded"

BRIEF_STATUSES = (
    STATUS_DRAFT,
    STATUS_APPROVED,
    STATUS_IN_PROGRESS,
    STATUS_COMPLETE,
    STATUS_SUPERSEDED,
)

# Statuses from which a session may be materialised (the execution gate): a brief
# must be at least `approved`. `complete` is included so a follow-up session
# against a finished brief can re-open it (many-sessions-per-brief); `draft` and
# `superseded` are refused.
EXECUTABLE_STATUSES = (STATUS_APPROVED, STATUS_IN_PROGRESS, STATUS_COMPLETE)

ASSIGNEE_AGENT = "agent"
ASSIGNEE_TRAINEE = "trainee"


def point_id_for(brief_id: str) -> str:
    """Deterministic Qdrant point ID for a ``brief_id`` (re-upsert-convergent).

    Same construction as a session point (``uuid5(NAMESPACE_DNS, key)``) so a
    brief is addressable by its human key without a lookup.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, brief_id))


# --------------------------------------------------------------------------- #
# Creation (full upsert — the one legitimate PUT …/points, SD-3-001)
# --------------------------------------------------------------------------- #

def create_brief(
    *,
    brief_id: str,
    team: str,
    author: str,
    assignee: str,
    body: str,
    assignee_type: str = ASSIGNEE_AGENT,
    status: str = STATUS_DRAFT,
    work_items: Optional[list[str]] = None,
    summary: Optional[str] = None,
    origin_path: Optional[str] = None,
    tooling_update: Optional[str] = None,
    created_at: Optional[str] = None,
    api_key: Optional[str] = None,
    ollama_host: Optional[str] = None,
) -> dict:
    """Create (or re-converge) the canonical `brief` point.

    Full-point upsert — creation only. **Embed-optional** (PROJ-041/T-002): the
    `brief` named vector is set from ``body`` only when an embed endpoint is
    explicitly configured (``ollama_host`` argument or ``OLLAMA_HOST`` env),
    else the point is written vector-less with a stderr note. Re-running with
    the same ``brief_id`` converges on the same point (idempotent authoring). ``session_ids`` and ``completed_at`` start empty/None
    and are only ever written later via the partial-write helpers, so a re-author
    of an in-flight brief does NOT clobber accumulated session links unless the
    caller passes them — this function deliberately does not accept them.

    ``tooling_update`` (PROJ-039/T-056) is the audit-record twin of the
    launch-time ``TOOLING_UPDATE`` env var that actually triggers the resident
    ``.claude/tools/update-tooling.py`` self-update: ``"<tag>"`` (e.g.
    ``"v1.1.0"``) or ``"latest"``. It is NOT read by the update tool itself —
    that reads the env var directly so it can run before/independent of
    materialise (no Qdrant round trip, no hook-ordering coupling). Storing it
    on the brief is the durable record of what a dispatch asked for; None/absent
    means the brief carries no self-update instruction.

    Returns ``{"point_id", "ok"}``.
    """
    if status not in BRIEF_STATUSES:
        raise ValueError(f"invalid brief status {status!r}; expected one of {BRIEF_STATUSES}")
    if assignee_type not in (ASSIGNEE_AGENT, ASSIGNEE_TRAINEE):
        raise ValueError(
            f"invalid assignee_type {assignee_type!r}; expected 'agent' or 'trainee'"
        )
    if not body.strip():
        raise ValueError("brief body is empty")

    pid = point_id_for(brief_id)
    created_at = created_at or session_substrate._now_iso()
    brief_vector = session_substrate.maybe_embed(
        body, label="brief_body", ollama_host=ollama_host
    )
    payload = {
        "point_type": "brief",
        "brief_id": brief_id,
        "team": team,
        "author": author,
        "assignee": assignee,
        "assignee_type": assignee_type,
        "status": status,
        "created_at": created_at,
        "completed_at": None,
        "session_ids": [],
        "work_items": list(work_items or []),
        "summary": summary or "",
        "body": body,
        "origin_path": origin_path or "",
        "tooling_update": tooling_update,
    }
    # Vector-less shape: an EMPTY named-vector map, not an omitted key — Qdrant
    # 400s on a missing `vector` field (proven live 2026-07-12).
    vector = {"brief": brief_vector} if brief_vector is not None else {}
    qdrant_http.upsert_points(
        [{"id": pid, "vector": vector, "payload": payload}],
        collection=COLLECTION,
        api_key=api_key,
    )
    return {"point_id": pid, "ok": True}


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

def get_brief(
    brief_id: str, *, api_key: Optional[str] = None, with_vector: bool = False
) -> Optional[dict]:
    """Fetch the `brief` point payload (or full result with ``with_vector``), or None."""
    return qdrant_http.get_point(
        point_id_for(brief_id),
        collection=COLLECTION,
        api_key=api_key,
        with_vector=with_vector,
    )


# --------------------------------------------------------------------------- #
# Partial writes (set_payload — never a full upsert; SD-3-001 / F-2-008)
# --------------------------------------------------------------------------- #

def append_session_id(
    brief_id: str, session_id: str, *, api_key: Optional[str] = None
) -> dict:
    """Append ``session_id`` to the brief's ``session_ids[]`` (the reverse link).

    Read-modify-write via ``set_payload`` (Qdrant has no array-push); **idempotent
    on the sid** — a re-materialise / resume of the same session does not duplicate
    the entry, while two *distinct* sessions against one brief accumulate two ids
    (the many-sessions-per-brief property). Preserves the `brief` named vector.

    Returns ``{"point_id", "session_ids", "appended", "ok"}``.

    T-047 (CC-348) — defence-in-depth against a placeholder sid polluting a live
    point. The runtime session id is always a UUID; a conventional synthetic test
    sid ("sid-1", "runtime-sid-9") is still several chars. A one-/two-char dummy
    ("s") is never a real session id, so refuse it loudly rather than write it. The
    original pollution vector was a non-hermetic hook test: `session-materialise`'s
    `main()` reads `BRIEF_ID` from the ambient env, and a T-045 serial-mode session
    ran the suite *inside* the primary clone where `BRIEF_ID` was set — sending a
    `{"session_id": "s"}` failure-case fixture down the brief-first path and into
    `append_session_id` against the LIVE point. That test is now hermetic
    (pops `BRIEF_ID`); this guard makes the whole class structurally impossible.
    """
    sid = (session_id or "").strip()
    if len(sid) < 4:
        raise ValueError(
            f"append_session_id: refusing an implausibly short session_id "
            f"{session_id!r} for {brief_id!r} — no runtime UUID or conventional "
            f"synthetic sid is that short; this is a placeholder that must never "
            f"reach a live brief point (T-047/CC-348)."
        )
    pid = point_id_for(brief_id)
    existing = qdrant_http.get_point(pid, collection=COLLECTION, api_key=api_key)
    current: list = []
    if existing and isinstance(existing.get("session_ids"), list):
        current = list(existing["session_ids"])
    appended = session_id not in current
    if appended:
        current.append(session_id)
        qdrant_http.set_payload(
            {"session_ids": current}, [pid], collection=COLLECTION, api_key=api_key
        )
    return {"point_id": pid, "session_ids": current, "appended": appended, "ok": True}


def set_status(
    brief_id: str, status: str, *, api_key: Optional[str] = None
) -> dict:
    """Set the brief's ``status`` via ``set_payload`` (preserves the named vector)."""
    if status not in BRIEF_STATUSES:
        raise ValueError(f"invalid brief status {status!r}; expected one of {BRIEF_STATUSES}")
    pid = point_id_for(brief_id)
    qdrant_http.set_payload(
        {"status": status}, [pid], collection=COLLECTION, api_key=api_key
    )
    return {"point_id": pid, "status": status, "ok": True}


def start_brief(brief_id: str, *, api_key: Optional[str] = None) -> dict:
    """Transition an `approved` brief to `in_progress` on first materialise.

    Idempotent: a brief already `in_progress` (or `complete`) is left untouched —
    only `approved` advances. A `draft`/`superseded` brief raises, because
    materialise must never stand up a session for one that has not cleared the
    execution gate (ADR-008 D5). Returns ``{"point_id", "status", "changed", "ok"}``.
    """
    pid = point_id_for(brief_id)
    existing = qdrant_http.get_point(pid, collection=COLLECTION, api_key=api_key)
    current = (existing or {}).get("status")
    if current not in EXECUTABLE_STATUSES:
        raise ValueError(
            f"brief {brief_id!r} status is {current!r}; refusing to start a session "
            f"for a brief that has not cleared the execution gate (expected one of "
            f"{EXECUTABLE_STATUSES})."
        )
    if current == STATUS_APPROVED:
        qdrant_http.set_payload(
            {"status": STATUS_IN_PROGRESS}, [pid], collection=COLLECTION, api_key=api_key
        )
        return {"point_id": pid, "status": STATUS_IN_PROGRESS, "changed": True, "ok": True}
    return {"point_id": pid, "status": current, "changed": False, "ok": True}


def complete_brief(
    brief_id: str, *, completed_at: Optional[str] = None, api_key: Optional[str] = None
) -> dict:
    """Stamp ``status=complete`` + ``completed_at`` (session-end, when the result
    declares the brief done). ``set_payload`` — preserves the named vector."""
    pid = point_id_for(brief_id)
    completed_at = completed_at or session_substrate._now_iso()
    qdrant_http.set_payload(
        {"status": STATUS_COMPLETE, "completed_at": completed_at},
        [pid],
        collection=COLLECTION,
        api_key=api_key,
    )
    return {"point_id": pid, "status": STATUS_COMPLETE, "completed_at": completed_at, "ok": True}
