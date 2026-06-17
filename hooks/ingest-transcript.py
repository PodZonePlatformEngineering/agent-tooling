#!/usr/bin/env python3
"""
SessionEnd hook — ingest JSONL transcript to Qdrant prompt_logs collection.
PROJ-031/T-002. Non-blocking: always exits 0.
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

CLOUD_QDRANT_URL = "https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io"
AGENTSONLY_QDRANT_URL = "http://qdrant.agenticflows.co.uk:8080"
CLOUD_COLLECTIONS = {"sessions", "tasks", "task_events", "prompt_logs", "one_shots"}

OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
COLLECTION_PROMPT_LOGS = "prompt_logs"
COLLECTION_SESSIONS = "sessions"


def _load_qdrant_http():
    """Locate agent-tooling and import the canonical qdrant_http primitive
    (PROJ-033/T-016). Returns the module, or None if not locatable — the hook
    is non-blocking, so it degrades to logging-and-skipping rather than a
    silent unauthenticated write. Mirrors stop-heartbeat._locate_agent_tooling.
    """
    candidates = []
    # parents[] covers both layouts: agent-tooling/hooks/ (root holds lib/) and a
    # self-contained home repo's .claude/hooks/ (.claude/ holds lib/). No
    # AGENT_TOOLING_DIR override — self-containment per PROJ-039 C2-v2.1.
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
                print(f"[ingest-transcript] qdrant_http import failed: {exc}", file=sys.stderr)
                return None
    print("[ingest-transcript] agent-tooling/lib not located; Qdrant writes skipped",
          file=sys.stderr)
    return None


qdrant_http = _load_qdrant_http()


def get_qdrant_url(collection: str) -> str:
    return CLOUD_QDRANT_URL if collection in CLOUD_COLLECTIONS else AGENTSONLY_QDRANT_URL


def embed(text: str) -> list[float] | None:
    try:
        import requests
        r = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text[:2000]},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["embedding"]
    except Exception as e:
        print(f"[ingest-transcript] embed error: {e}", file=sys.stderr)
        return None


def upsert_point(collection: str, point_id: str, vector: list[float], payload: dict) -> bool:
    if qdrant_http is None:
        return False
    point = {"id": point_id, "vector": vector, "payload": payload}
    try:
        qdrant_http.upsert_points(
            [point], collection=collection,
            qdrant_url=get_qdrant_url(collection), timeout=15,
        )
        return True
    except qdrant_http.QdrantAuthError as e:
        print(f"[ingest-transcript] PODZONE_QDRANT_APIKEY unavailable; skipped {collection} write: {e}",
              file=sys.stderr)
        return False
    except qdrant_http.QdrantError as e:
        print(f"[ingest-transcript] upsert error ({collection}): {e}", file=sys.stderr)
        return False


def upsert_point_no_vector(collection: str, point_id: str, payload: dict) -> bool:
    """Update payload only (session record — not vectorised)."""
    if qdrant_http is None:
        return False
    url = f"{get_qdrant_url(collection)}/collections/{collection}/points/payload"
    try:
        qdrant_http.request_json(
            "POST", url, payload={"payload": payload, "points": [point_id]}, timeout=15,
        )
        return True
    except qdrant_http.QdrantAuthError as e:
        print(f"[ingest-transcript] PODZONE_QDRANT_APIKEY unavailable; skipped {collection} payload update: {e}",
              file=sys.stderr)
        return False
    except qdrant_http.QdrantError as e:
        print(f"[ingest-transcript] payload update error ({collection}): {e}", file=sys.stderr)
        return False


def fetch_session_agent(session_id: str) -> str:
    """Look up agent from the sessions record written at SessionStart."""
    if qdrant_http is None:
        return "unknown"
    try:
        payload = qdrant_http.get_point(
            session_id, collection=COLLECTION_SESSIONS,
            qdrant_url=get_qdrant_url(COLLECTION_SESSIONS), timeout=8,
        )
        return (payload or {}).get("agent", "unknown")
    except qdrant_http.QdrantError:
        return "unknown"


def _task_slug_from_cwd(cwd: str) -> str:
    """Extract task slug from session directory path.

    Sessions live at ~/sessions/{agent}-{YYYY-MM-DD}-{task-slug}/...
    Returns '{agent}-{task-slug}' or 'unknown' if pattern doesn't match.
    """
    try:
        # Walk up from cwd to find a path component matching the session pattern
        from pathlib import Path as _Path
        import re as _re
        parts = _Path(cwd).parts
        for part in reversed(parts):
            m = _re.match(r"(\w+)-\d{4}-\d{2}-\d{2}-(.+)", part)
            if m:
                return f"{m.group(1)}: {m.group(2).replace('-', ' ')}"
    except Exception:
        pass
    return "session"


def _notify_session_concluded(agent: str, cwd: str) -> None:
    """Best-effort Telegram notification: session concluded."""
    try:
        notify_script = Path(cwd) / ".claude" / "hooks" / "telegram-notify.py"
        if not notify_script.exists():
            project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
            if project_dir:
                notify_script = Path(project_dir) / ".claude" / "hooks" / "telegram-notify.py"
        if not notify_script.exists():
            print("[ingest-transcript] telegram-notify.py not found; skipping notification",
                  file=sys.stderr)
            return
        task_slug = _task_slug_from_cwd(cwd)
        message = f"✅ {agent or 'agent'} session done — {task_slug}"
        import subprocess as _subprocess
        _subprocess.run(
            [sys.executable, str(notify_script), message],
            timeout=25,
            capture_output=True,
        )
    except Exception as exc:
        print(f"[ingest-transcript] telegram notification failed: {exc}", file=sys.stderr)


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception as e:
        print(f"[ingest-transcript] failed to parse stdin: {e}", file=sys.stderr)
        sys.exit(0)

    session_id = data.get("session_id", str(uuid.uuid4()))
    transcript_path = data.get("transcript_path", "")
    cwd = data.get("cwd", "")
    agent = fetch_session_agent(session_id)
    now = datetime.now(timezone.utc).isoformat()

    if not transcript_path or not Path(transcript_path).exists():
        print(f"[ingest-transcript] transcript not found: {transcript_path}", file=sys.stderr)
        # Still update session record as closed
        upsert_point_no_vector(
            COLLECTION_SESSIONS,
            session_id,
            {"end_ts": now, "status": "closed"},
        )
        sys.exit(0)

    # Parse JSONL — extract user turns
    turns = []
    try:
        with open(transcript_path) as f:
            for line_num, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Transcript format: {type: "user", message: {role: "user", content: str}}
                # Ignore queue-operation, attachment, file-history-snapshot, ai-title, etc.
                if obj.get("type") != "user":
                    continue
                msg = obj.get("message", {})
                content = msg.get("content", "")

                if content:
                    if isinstance(content, list):
                        # Content blocks — extract text
                        text = " ".join(
                            b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    else:
                        text = str(content)

                    if text.strip():
                        turns.append({
                            "turn_number": len(turns),
                            "text": text.strip(),
                            "timestamp": obj.get("timestamp", now),
                        })
    except Exception as e:
        print(f"[ingest-transcript] failed to read transcript: {e}", file=sys.stderr)
        sys.exit(0)

    # Ingest each turn
    ingested = 0
    for turn in turns:
        vector = embed(turn["text"])
        if vector is None:
            continue
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{session_id}:{turn['turn_number']}"))
        payload = {
            "session_id": session_id,
            "agent": agent,
            "turn_number": turn["turn_number"],
            "timestamp": turn["timestamp"],
            "prompt_text": turn["text"][:1000],
            "cwd": cwd,
        }
        if upsert_point(COLLECTION_PROMPT_LOGS, point_id, vector, payload):
            ingested += 1

    # Update session record
    upsert_point_no_vector(
        COLLECTION_SESSIONS,
        session_id,
        {
            "end_ts": now,
            "status": "closed",
            "transcript_path": transcript_path,
        },
    )

    print(f"[ingest-transcript] ingested {ingested}/{len(turns)} turns for session {session_id}")

    # Telegram: notify session concluded
    _notify_session_concluded(agent, cwd)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ingest-transcript] unhandled error: {e}", file=sys.stderr)
        sys.exit(0)
