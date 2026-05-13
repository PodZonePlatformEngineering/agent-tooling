#!/usr/bin/env python3
"""
subagent-stop.py — SubagentStop hook.

Fires when a subagent completes. Records the subagent result summary to
task_events and marks the subagent session as closed in sessions.

Input: JSON on stdin (Claude Code SubagentStop hook format).
Exit: always 0 (non-blocking).
"""

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

CLOUD_QDRANT_URL = "https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io"
AGENTSONLY_QDRANT_URL = "http://qdrant.agenticflows.co.uk:8080"
CLOUD_COLLECTIONS = {"sessions", "tasks", "task_events", "prompt_logs", "one_shots"}

SESSIONS_COLLECTION = "sessions"
EVENTS_COLLECTION = "task_events"
OLLAMA_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"
VECTOR_SIZE = 768


def get_qdrant_url(collection: str) -> str:
    return CLOUD_QDRANT_URL if collection in CLOUD_COLLECTIONS else AGENTSONLY_QDRANT_URL


def get_qdrant_headers(collection: str) -> dict:
    if collection in CLOUD_COLLECTIONS:
        api_key = os.environ.get("PODZONE_QDRANT_APIKEY", "")
        return {"api-key": api_key} if api_key else {}
    return {}


def log(msg: str) -> None:
    print(f"[subagent-stop] {msg}", file=sys.stderr)


def event_point_id(session_id: str, timestamp: str) -> int:
    """Deterministic integer point ID for a task_events point."""
    uid = uuid.uuid5(uuid.NAMESPACE_DNS, f"{session_id}:subagent_complete:{timestamp}")
    return int(hashlib.md5(str(uid).encode()).hexdigest()[:16], 16)


def get_embedding(text: str) -> List[float]:
    """Embed text via Ollama. Returns zero vector on failure."""
    try:
        import requests
        r = requests.post(
            OLLAMA_URL,
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("embedding", [0.0] * VECTOR_SIZE)
    except Exception as exc:
        log(f"Ollama unavailable ({exc}); using zero vector")
        return [0.0] * VECTOR_SIZE


def read_transcript_summary(transcript_path: str) -> str:
    """
    Read the subagent's transcript JSONL.
    Return the last assistant turn's text (first 500 chars).
    """
    try:
        p = Path(transcript_path)
        if not p.exists():
            return ""
        lines = p.read_text(encoding="utf-8").splitlines()
        last_text = ""
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("type") != "assistant":
                continue
            message = entry.get("message", {})
            content = message.get("content", [])
            if not isinstance(content, list):
                continue
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    if text:
                        last_text = text
                        break
        return last_text[:500] if last_text else ""
    except Exception as exc:
        log(f"Could not read transcript {transcript_path}: {exc}")
        return ""


def upsert_event(session_id: str, parent_session_id: Optional[str], detail: str, timestamp: str) -> None:
    """Write a subagent_complete event to task_events."""
    try:
        import requests
    except ImportError:
        log("requests not available; skipping event upsert")
        return

    point_id = event_point_id(session_id, timestamp)
    vector = get_embedding(detail) if detail else [0.0] * VECTOR_SIZE

    payload = {
        "event_type": "subagent_complete",
        "actor": "hook",
        "session_id": session_id,
        "detail": detail,
        "timestamp": timestamp,
    }
    if parent_session_id:
        payload["parent_session_id"] = parent_session_id

    try:
        r = requests.put(
            f"{get_qdrant_url(EVENTS_COLLECTION)}/collections/{EVENTS_COLLECTION}/points",
            headers=get_qdrant_headers(EVENTS_COLLECTION),
            json={
                "points": [
                    {
                        "id": point_id,
                        "vector": vector,
                        "payload": payload,
                    }
                ]
            },
            timeout=20,
        )
        r.raise_for_status()
        log(f"event upserted for session {session_id[:16]}...")
    except Exception as exc:
        log(f"Warning: event upsert failed: {exc}")


def close_session(session_id: str, now: str) -> None:
    """Mark the subagent's session as closed in the sessions collection."""
    try:
        import requests
    except ImportError:
        log("requests not available; skipping session close")
        return

    try:
        r = requests.patch(
            f"{get_qdrant_url(SESSIONS_COLLECTION)}/collections/{SESSIONS_COLLECTION}/points/payload",
            headers=get_qdrant_headers(SESSIONS_COLLECTION),
            json={
                "payload": {"status": "closed", "end_ts": now},
                "points": [session_id],
            },
            timeout=8,
        )
        r.raise_for_status()
        log(f"session {session_id[:16]}... marked closed")
    except Exception as exc:
        log(f"Warning: session close failed: {exc}")


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
    parent_session_id: Optional[str] = hook_data.get("parent_session_id")
    transcript_path: Optional[str] = hook_data.get("transcript_path", "")

    if not session_id:
        log("No session_id in hook input; skipping.")
        sys.exit(0)

    now = datetime.now(timezone.utc).isoformat()

    # Extract result summary from transcript
    result_summary = ""
    if transcript_path:
        result_summary = read_transcript_summary(transcript_path)

    # Write task_events point
    upsert_event(session_id, parent_session_id, result_summary, now)

    # Close the subagent session
    close_session(session_id, now)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[subagent-stop] Unexpected error: {exc}", file=sys.stderr)
    sys.exit(0)
