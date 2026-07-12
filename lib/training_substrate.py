"""
training_substrate.py — schema helpers for the training collections (PROJ-011/T-031).

The single place that knows the `training_briefs` / `training_session_telemetry`
point shapes (collections/training_briefs.yaml,
collections/training_session_telemetry.yaml). T-030's trainee hooks and the
trainer-side authoring tools both build points through these helpers so the
schema has one implementation.

Design constraints carried from the fleet substrate:
  - stdlib-only (no ``requests`` — trainee hosts are bare interpreters);
  - deterministic uuid5 point ids so re-authoring/retries converge;
  - trainee-side writes are PAYLOAD-ONLY (T-002 hooks-never-embed): points
    carry ``"vector": {}`` — an empty named-vector map, never an omitted key
    (Qdrant 400s on a missing ``vector`` field).

Write ownership convention (not JWT-enforced — trainee tokens are
collection-rw): trainers author ``brief`` points, trainee agents author
``message`` points.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

TRAINING_BRIEFS = "training_briefs"
TRAINING_TELEMETRY = "training_session_telemetry"
TOKEN_REGISTRY = "training_token_registry"

# The full set a trainee credential may touch. The registry is deliberately
# NOT here — it is training-team-owned (revocation must be out of trainee
# reach).
TRAINEE_COLLECTIONS = (TRAINING_BRIEFS, TRAINING_TELEMETRY)

BRIEF_STATUSES = ("active", "paused", "retired")  # recurring — no "complete"
MESSAGE_STATUSES = ("open", "seen", "resolved")
CHANNELS = ("training", "operational")
MESSAGE_TYPES = ("progress", "question", "issue", "ack")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def brief_id_for(trainee: str, *, channel: str = "training",
                 slug: Optional[str] = None) -> str:
    """The human brief_id key. No date component — recurring briefs are
    re-authored under the SAME id (dating the id would fork the thread).

      training/{trainee}/{slug}          personalised training brief
      training/{trainee}/operational     the repo's operational brief
    """
    if channel == "operational":
        return f"training/{trainee}/operational"
    if not slug:
        raise ValueError("a training-channel brief_id needs a curriculum slug")
    return f"training/{trainee}/{slug}"


def brief_point_id(brief_id: str) -> str:
    """uuid5(brief_id) — trainer re-author converges on the same point."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, brief_id))


def message_point_id(brief_id: str, session_id: str, seq: int) -> str:
    """uuid5("{brief_id}/msg/{session_id}/{seq}") — retry-idempotent."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{brief_id}/msg/{session_id}/{seq}"))


def build_brief_point(*, brief_id: str, trainee: str, channel: str,
                      body: str, summary: str = "", author: str = "",
                      status: str = "active", revision: int = 1,
                      created_at: Optional[str] = None,
                      updated_at: Optional[str] = None,
                      session_ids: Optional[list] = None,
                      vector: Optional[dict] = None) -> dict:
    """A trainer-authored ``brief`` point (direction to_trainee).

    ``vector`` is the named-vector map ``{"brief": [...768 floats]}`` when the
    trainer-side tool embedded; defaults to the payload-only empty map.
    """
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, got {channel!r}")
    if status not in BRIEF_STATUSES:
        raise ValueError(f"brief status must be one of {BRIEF_STATUSES}, got {status!r}")
    ts = now_iso()
    return {
        "id": brief_point_id(brief_id),
        "vector": vector if vector is not None else {},
        "payload": {
            "point_type": "brief",
            "direction": "to_trainee",
            "brief_id": brief_id,
            "trainee": trainee,
            "channel": channel,
            "status": status,
            "revision": revision,
            "author": author,
            "created_at": created_at or ts,
            "updated_at": updated_at or ts,
            "session_ids": session_ids or [],
            "summary": summary,
            "body": body,
        },
    }


def build_message_point(*, brief_id: str, trainee: str, session_id: str,
                        seq: int, message_type: str, body: str,
                        channel: str = "training", sender: str = "",
                        recipient: str = "trainer", summary: str = "",
                        ack_of_revision: Optional[int] = None,
                        created_at: Optional[str] = None) -> dict:
    """A trainee-agent ``message`` point (direction from_trainee).

    Always payload-only — trainee hosts never embed (T-002). ``seq`` is the
    per-session message counter that makes retries idempotent.
    """
    if message_type not in MESSAGE_TYPES:
        raise ValueError(
            f"message_type must be one of {MESSAGE_TYPES}, got {message_type!r}")
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, got {channel!r}")
    if message_type == "ack" and ack_of_revision is None:
        raise ValueError("an ack message must carry ack_of_revision")
    ts = created_at or now_iso()
    payload = {
        "point_type": "message",
        "direction": "from_trainee",
        "brief_id": brief_id,
        "trainee": trainee,
        "channel": channel,
        "status": "open",
        "message_type": message_type,
        "sender": sender or trainee,
        "recipient": recipient,
        "session_id": session_id,
        "created_at": ts,
        "updated_at": ts,
        "summary": summary,
        "body": body,
    }
    if ack_of_revision is not None:
        payload["ack_of_revision"] = ack_of_revision
    return {
        "id": message_point_id(brief_id, session_id, seq),
        "vector": {},
        "payload": payload,
    }


def build_telemetry_point(event_payload: dict, *, trainee: str,
                          point_id: str) -> dict:
    """Wrap a CST-shaped event payload for ``training_session_telemetry``.

    ``event_payload`` is whatever the CST writer for the event class builds
    (e.g. ``lib/stop_telemetry.build_payload``); this adds the additive
    ``trainee`` field and the payload-only vector map. ``point_id`` follows
    the CST construction for the event class (e.g.
    ``stop_telemetry.point_id_for_stop``) so retried hooks converge.
    """
    payload = dict(event_payload)
    payload["trainee"] = trainee
    return {"id": point_id, "vector": {}, "payload": payload}
