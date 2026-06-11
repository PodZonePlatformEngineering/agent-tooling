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


def _load_qdrant_http():
    """Locate agent-tooling and import the canonical qdrant_http primitive
    (PROJ-033/T-016). Returns the module, or None if not locatable — the hook
    is non-blocking, so it degrades to logging-and-skipping rather than a
    silent unauthenticated write. Mirrors stop-heartbeat._locate_agent_tooling.
    """
    candidates = []
    env = os.environ.get("AGENT_TOOLING_DIR")
    if env:
        candidates.append(Path(env))
    candidates.extend(Path(__file__).resolve().parents)
    candidates.append(Path.home() / "workspace" / "agent-tooling")
    if (Path.home() / "sessions").is_dir():
        candidates.extend((Path.home() / "sessions").glob("*/agent-tooling"))
    for c in candidates:
        if (c / "lib" / "qdrant_http.py").is_file():
            if str(c) not in sys.path:
                sys.path.insert(0, str(c))
            try:
                from lib import qdrant_http  # type: ignore
                return qdrant_http
            except Exception as exc:
                print(f"[subagent-stop] qdrant_http import failed: {exc}", file=sys.stderr)
                return None
    print("[subagent-stop] agent-tooling/lib not located; Qdrant writes skipped",
          file=sys.stderr)
    return None


qdrant_http = _load_qdrant_http()


def get_qdrant_url(collection: str) -> str:
    return CLOUD_QDRANT_URL if collection in CLOUD_COLLECTIONS else AGENTSONLY_QDRANT_URL


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
    if qdrant_http is None:
        log("qdrant_http unavailable; skipping event upsert")
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

    point = {"id": point_id, "vector": vector, "payload": payload}
    try:
        qdrant_http.upsert_points(
            [point], collection=EVENTS_COLLECTION,
            qdrant_url=get_qdrant_url(EVENTS_COLLECTION), timeout=20,
        )
        log(f"event upserted for session {session_id[:16]}...")
    except qdrant_http.QdrantAuthError as exc:
        log(f"PODZONE_QDRANT_APIKEY unavailable; skipping event upsert: {exc}")
    except qdrant_http.QdrantError as exc:
        log(f"Warning: event upsert failed: {exc}")


def close_session(session_id: str, now: str) -> None:
    """Mark the subagent's session as closed in the sessions collection."""
    if qdrant_http is None:
        log("qdrant_http unavailable; skipping session close")
        return

    url = (
        f"{get_qdrant_url(SESSIONS_COLLECTION)}"
        f"/collections/{SESSIONS_COLLECTION}/points/payload"
    )
    try:
        qdrant_http.request_json(
            "POST", url,
            payload={"payload": {"status": "closed", "end_ts": now}, "points": [session_id]},
            timeout=8,
        )
        log(f"session {session_id[:16]}... marked closed")
    except qdrant_http.QdrantAuthError as exc:
        log(f"PODZONE_QDRANT_APIKEY unavailable; skipping session close: {exc}")
    except qdrant_http.QdrantError as exc:
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
