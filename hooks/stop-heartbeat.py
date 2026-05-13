#!/usr/bin/env python3
"""
stop-heartbeat.py — Stop hook.

Fires at the end of each Claude turn (before the next user turn).
Upserts a heartbeat record in the `sessions` Qdrant collection.

Input: JSON on stdin (Claude Code Stop hook format).
Exit: always 0 (non-blocking, fast — no Ollama calls).
"""

import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

CLOUD_QDRANT_URL = "https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io"
AGENTSONLY_QDRANT_URL = "http://qdrant.agenticflows.co.uk:8080"
CLOUD_COLLECTIONS = {"sessions", "tasks", "task_events", "prompt_logs", "one_shots"}

SESSIONS_COLLECTION = "sessions"


def get_qdrant_url(collection: str) -> str:
    return CLOUD_QDRANT_URL if collection in CLOUD_COLLECTIONS else AGENTSONLY_QDRANT_URL


def get_qdrant_headers(collection: str) -> dict:
    if collection in CLOUD_COLLECTIONS:
        api_key = os.environ.get("PODZONE_QDRANT_APIKEY", "")
        return {"api-key": api_key} if api_key else {}
    return {}


def log(msg: str) -> None:
    print(f"[stop-heartbeat] {msg}", file=sys.stderr)


def upsert_heartbeat(session_id: str, cwd: Optional[str]) -> None:
    """Upsert a heartbeat record to the sessions collection."""
    try:
        import requests
    except ImportError:
        log("requests not available; skipping heartbeat upsert")
        return

    now = datetime.now(timezone.utc).isoformat()

    payload = {
        "session_id": session_id,
        "last_heartbeat_ts": now,
        "status": "active",
    }
    if cwd:
        payload["cwd"] = cwd

    _url = get_qdrant_url(SESSIONS_COLLECTION)
    _headers = get_qdrant_headers(SESSIONS_COLLECTION)
    try:
        r = requests.patch(
            f"{_url}/collections/{SESSIONS_COLLECTION}/points/payload",
            headers=_headers,
            json={
                "payload": {"last_heartbeat_ts": now, "status": "active"},
                "points": [session_id],
            },
            timeout=8,
        )
        r.raise_for_status()
        log(f"heartbeat updated for session {session_id[:16]}...")
    except Exception:
        # Fall back to full upsert (point may not exist yet)
        try:
            r = requests.put(
                f"{_url}/collections/{SESSIONS_COLLECTION}/points",
                headers=_headers,
                json={
                    "points": [
                        {
                            "id": session_id,
                            "vector": [0.0] * 768,
                            "payload": payload,
                        }
                    ]
                },
                timeout=8,
            )
            r.raise_for_status()
            log(f"heartbeat upserted for session {session_id[:16]}...")
        except Exception as exc:
            log(f"Warning: heartbeat upsert failed: {exc}")


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        hook_data = json.loads(raw)
    except Exception as exc:
        log(f"Could not parse stdin JSON: {exc}")
        sys.exit(0)

    session_id: Optional[str] = hook_data.get("session_id")
    cwd: Optional[str] = hook_data.get("cwd")

    if not session_id:
        log("No session_id in hook input; skipping.")
        sys.exit(0)

    upsert_heartbeat(session_id, cwd)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[stop-heartbeat] Unexpected error: {exc}", file=sys.stderr)
    sys.exit(0)
