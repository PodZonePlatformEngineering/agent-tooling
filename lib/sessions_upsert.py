"""
sessions_upsert.py — shared upsert library for the PROJ-034 writers.

Used by:
  - tools/backfill-sessions.py        (data_source="backfill")
  - hooks/stop-heartbeat.py           (data_source="stop_hook")
  - tools/upsert-current-session.py   (data_source="session_end_skill")

Targets the cloud Qdrant `sessions` collection. Best-effort: never raises;
returns a dict describing what happened so callers can log/aggregate.

Status derivation (when caller passes status=None) follows §D6 of the proposal.

Write precedence: latest write wins, with one guard — a `session_end_skill`
point is "final" and a backfill write must skip it when its jsonl_mtime is not
newer. The skip rule is owned by callers (they have the freedom to query
existing points cheaply alongside other work); `should_skip_backfill()` is
provided as a helper.

PROJ-033/T-016: Qdrant access now goes through `lib.qdrant_http` (stdlib
urllib, no `requests` dependency). A missing/empty `PODZONE_QDRANT_APIKEY`
raises `QdrantAuthError` rather than silently no-op'ing — there is no longer
any path that proceeds to a zero-write because the key was empty. Transient
network/HTTP errors are still returned in the result dict (best-effort) so
non-blocking hooks can aggregate; only the configuration error (no key) is
loud. Best-effort callers (hooks) must catch `QdrantAuthError`.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from lib import qdrant_http
from lib.qdrant_http import QdrantAuthError  # re-exported for callers

CLOUD_QDRANT_URL = qdrant_http.CLOUD_QDRANT_URL
SESSIONS_COLLECTION = "sessions"

# Phase 1: no vectors on `sessions` payloads — store as zeros. Collection is
# 768-dim cosine (created in PROJ-031/T-001). Sessions collection is queried
# by payload filter, not by vector similarity.
ZERO_VECTOR_DIM = 768

VALID_DATA_SOURCES = {"backfill", "stop_hook", "session_end_skill"}


def _log(msg: str) -> None:
    print(f"[sessions_upsert] {msg}", file=sys.stderr)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def point_id_for(session_id: str) -> str:
    """Deterministic Qdrant point ID for a session_id."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, session_id))


def derive_status(jsonl_mtime_iso: str) -> str:
    """§D6: 30 min → in_progress, 6 h → idle, else ended."""
    try:
        mtime = datetime.fromisoformat(jsonl_mtime_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "ended"
    delta = (datetime.now(timezone.utc) - mtime).total_seconds()
    if delta < 30 * 60:
        return "in_progress"
    if delta < 6 * 3600:
        return "idle"
    return "ended"


def _get_existing(session_id: str, timeout: float = 5.0) -> Optional[dict]:
    """Fetch existing point payload, or None on not-found / transient failure.

    A missing API key raises ``QdrantAuthError`` (loud — a missing key must
    never be mistaken for "no existing point", which would let a backfill
    clobber a finalised row). Network/HTTP errors return None (best-effort).
    """
    pid = point_id_for(session_id)
    try:
        return qdrant_http.get_point(
            pid, collection=SESSIONS_COLLECTION, timeout=timeout
        )
    except QdrantAuthError:
        raise
    except qdrant_http.QdrantError:
        return None


def should_skip_backfill(session_id: str, new_jsonl_mtime: str) -> bool:
    """Return True if a backfill write should skip this session.

    Rule: skip when existing point is `session_end_skill` AND existing
    jsonl_mtime >= new jsonl_mtime. Backfill always overrides stop_hook.
    """
    existing = _get_existing(session_id)
    if not existing:
        return False
    if existing.get("data_source") != "session_end_skill":
        return False
    existing_mtime = existing.get("jsonl_mtime") or ""
    return existing_mtime >= (new_jsonl_mtime or "")


def upsert_session(
    payload: dict,
    data_source: str,
    status: Optional[str] = None,
    last_heartbeat_ts: Optional[str] = None,
    extra_payload: Optional[dict] = None,
    timeout: float = 8.0,
) -> dict:
    """Upsert a sessions payload to the cloud Qdrant `sessions` collection.

    Returns a result dict:
        {"ok": bool, "skipped": bool, "reason": str, "point_id": str|None,
         "payload": dict}

    A missing/empty ``PODZONE_QDRANT_APIKEY`` raises ``QdrantAuthError`` — it
    is never downgraded to a silent ``ok: False`` (that was the PROJ-033/T-016
    zero-write bug). Transient network/HTTP errors are caught and returned in
    the dict so non-blocking callers can aggregate; loud callers should let the
    QdrantAuthError propagate to a non-zero exit.
    """
    result: dict[str, Any] = {
        "ok": False,
        "skipped": False,
        "reason": "",
        "point_id": None,
        "payload": payload,
    }

    if data_source not in VALID_DATA_SOURCES:
        result["reason"] = f"invalid data_source: {data_source}"
        _log(result["reason"])
        return result

    session_id = payload.get("session_id")
    if not session_id:
        result["reason"] = "missing session_id"
        _log(result["reason"])
        return result

    if status is None:
        status = derive_status(payload.get("jsonl_mtime") or "")

    payload["data_source"] = data_source
    payload["status"] = status
    payload["updated_at"] = _now_iso()
    if last_heartbeat_ts is not None:
        payload["last_heartbeat_ts"] = last_heartbeat_ts
    # Caller-supplied extra fields (e.g. push_failed:true from /session-end on a
    # failed home-repo push — PROJ-032 T-005). Merged last so callers can stamp
    # session-level flags without a bespoke writer.
    if extra_payload:
        payload.update(extra_payload)

    pid = point_id_for(session_id)
    result["point_id"] = pid

    # Resolve the key up front: a missing key is a configuration error and
    # must be loud — never a silent zero-write. (Raises QdrantAuthError.)
    qdrant_http.resolve_api_key()

    point = {"id": pid, "vector": [0.0] * ZERO_VECTOR_DIM, "payload": payload}
    try:
        qdrant_http.upsert_points(
            [point], collection=SESSIONS_COLLECTION, timeout=timeout
        )
        result["ok"] = True
        result["reason"] = "upserted"
        return result
    except qdrant_http.QdrantError as exc:
        # Transient network/HTTP failure — best-effort, return for aggregation.
        result["reason"] = f"upsert failed: {exc}"
        _log(result["reason"])
        return result
