"""
cst_cleanup.py — raw-event deletion from CST (PROJ-039 R-013, § 2.4 step 5).

After the session JSONL is committed and pushed to `agent-telemetry.git`, the raw
``PreToolUse``/``PostToolUse`` observability points for the session are deleted
from the `claude_session_telemetry` (CST) collection — and ONLY those two event
types (DC-1: the deletion targets CST, not session_substrate; the rest of the CST
events are retained, R-008). They remain reconstructable from the git-versioned
JSONL via the F-3-002 mapping (each `tool_use` block ↔ one PreToolUse; its paired
`tool_result` ↔ one PostToolUse, keyed by tool_use_id).

**This must run only after a successful telemetry push** (C-006). The caller owns
that gate; this module just performs the scoped delete and reports the count.
"""

from __future__ import annotations

from typing import Optional

from . import qdrant_http

CST_COLLECTION = "claude_session_telemetry"
RAW_EVENT_TYPES = ("PreToolUse", "PostToolUse")


def count_raw_tool_events(
    session_id: str, *, api_key: Optional[str] = None, collection: str = CST_COLLECTION
) -> int:
    """Count PreToolUse/PostToolUse points for ``session_id`` in CST."""
    body = {
        "filter": {
            "must": [
                {"key": "session_id", "match": {"value": session_id}},
                {"key": "event_type", "match": {"any": list(RAW_EVENT_TYPES)}},
            ]
        },
        "limit": 10000,
        "with_payload": False,
    }
    resp = qdrant_http.scroll(collection=collection, body=body, api_key=api_key)
    return len(resp.get("result", {}).get("points", []))


def delete_raw_tool_events(
    session_id: str, *, api_key: Optional[str] = None, collection: str = CST_COLLECTION
) -> dict:
    """Delete the session's PreToolUse/PostToolUse points from CST (filter delete).

    Returns ``{"deleted_before_count", "ok"}``. Uses a filter-based delete so it
    removes exactly the two raw event types for this session and nothing else.
    """
    before = count_raw_tool_events(session_id, api_key=api_key, collection=collection)
    url = f"{qdrant_http.CLOUD_QDRANT_URL}/collections/{collection}/points/delete"
    payload = {
        "filter": {
            "must": [
                {"key": "session_id", "match": {"value": session_id}},
                {"key": "event_type", "match": {"any": list(RAW_EVENT_TYPES)}},
            ]
        }
    }
    qdrant_http.request_json("POST", url, payload=payload, api_key=api_key)
    return {"deleted_before_count": before, "ok": True}
