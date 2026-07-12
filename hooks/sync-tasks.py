#!/usr/bin/env python3
"""
sync-tasks.py — PostToolUse hook.

Fires when Claude edits planning/projects/*/tasks.md.
Reads the edited file, detects status changes vs Qdrant, patches Qdrant,
writes task_events points, then re-runs the file hierarchy generator for
the affected project.

Input: JSON on stdin (Claude Code PostToolUse hook format).
Exit: always 0 (non-blocking).
"""

import os
import sys
import re
import json
import subprocess
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Filter,
        FieldCondition,
        MatchAny,
        MatchValue,
        PointStruct,
    )
except ImportError:
    # If qdrant_client is unavailable, exit silently — hook must not block
    sys.exit(0)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CLOUD_QDRANT_URL = "https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io"
AGENTSONLY_QDRANT_URL = "http://qdrant.agenticflows.co.uk:8080"
CLOUD_COLLECTIONS = {"sessions", "tasks", "task_events", "prompt_logs", "one_shots"}

TASKS_COLLECTION = "tasks"
EVENTS_COLLECTION = "task_events"


def get_client(collection: str) -> QdrantClient:
    if collection in CLOUD_COLLECTIONS:
        return QdrantClient(
            url=CLOUD_QDRANT_URL,
            api_key=os.environ.get("PODZONE_QDRANT_APIKEY"),
            timeout=30,
        )
    return QdrantClient(url=AGENTSONLY_QDRANT_URL, timeout=30)
VECTOR_SIZE = 768

# Map display strings back to canonical status values
STATUS_DISPLAY_MAP = {
    "🚀 ready": "ready",
    "🔄 in-progress": "in-progress",
    "⚠️ blocked": "blocked",
    "⏸ paused": "paused",
    "ready": "ready",
    "in-progress": "in-progress",
    "blocked": "blocked",
    "paused": "paused",
    "complete": "complete",
    "superseded": "superseded",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    """Write a log line to stderr (visible in Claude Code output)."""
    print(f"[sync-tasks] {msg}", file=sys.stderr)


def parse_proj_id(file_path: str) -> Optional[str]:
    """Extract PROJ-NNN from a file path."""
    m = re.search(r"(PROJ-\d+)", file_path, re.IGNORECASE)
    return m.group(1).upper() if m else None


def parse_table_rows(content: str) -> List[Dict]:
    """
    Parse markdown table rows from a tasks.md file.
    Returns list of {task_id, status} dicts.
    Expected columns: | ID | CC | Status | Agent | Summary |
    """
    rows: List[Dict] = []
    in_table = False
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            in_table = False
            continue
        # Skip header and separator rows
        if re.match(r"^\|\s*(ID|----)", line, re.IGNORECASE):
            in_table = True
            continue
        if not in_table:
            continue
        cols = [c.strip() for c in line.split("|")]
        # cols[0] is empty (leading |), cols[1..N] are the actual columns
        cols = [c for c in cols if c != ""]
        if len(cols) < 3:
            continue
        task_id = cols[0].strip()
        # status is cols[2]
        raw_status = cols[2].strip()
        canonical = STATUS_DISPLAY_MAP.get(raw_status)
        if not canonical:
            # Try stripping emoji prefix
            for key, val in STATUS_DISPLAY_MAP.items():
                if raw_status.endswith(key) or raw_status.endswith(val):
                    canonical = val
                    break
        if task_id and canonical:
            rows.append({"task_id": task_id, "status": canonical})
    return rows


def fetch_qdrant_statuses(client: QdrantClient, proj_id: str) -> Dict[str, Dict]:
    """
    Fetch all task points for proj_id from Qdrant.
    Returns dict: {task_id: {point_id, status, ...}}
    """
    result: Dict[str, Dict] = {}
    offset = None
    while True:
        points, next_offset = client.scroll(
            collection_name=TASKS_COLLECTION,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="proj_id",
                        match=MatchValue(value=proj_id),
                    )
                ]
            ),
            limit=500,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        for p in points:
            if p.payload:
                tid = p.payload.get("task_id")
                if tid:
                    result[tid] = {"point_id": str(p.id), "payload": p.payload}
        if next_offset is None:
            break
        offset = next_offset
    return result


def update_task_status(client: QdrantClient, point_id: str, new_status: str) -> None:
    """Patch the status field on a task point."""
    client.set_payload(
        collection_name=TASKS_COLLECTION,
        payload={"status": new_status},
        points=[point_id],
    )


def event_point_id(session_id: str, task_id: str, timestamp: str) -> int:
    """Deterministic integer point ID for a task_events point."""
    uid = uuid.uuid5(uuid.NAMESPACE_DNS, f"{session_id}:{task_id}:{timestamp}")
    return int(hashlib.md5(str(uid).encode()).hexdigest()[:16], 16)


def write_event(
    client: QdrantClient,
    task_id: str,
    proj_id: str,
    old_status: str,
    new_status: str,
    session_id: str,
) -> None:
    """Write a task_events point recording the status change."""
    now = datetime.now(timezone.utc).isoformat()
    detail = f"{old_status} → {new_status}"
    point_id = event_point_id(session_id, task_id, now)
    # Payload-only (PROJ-041/T-002): hooks never embed. `task_events` is an
    # unnamed-single-vector collection, so the point carries a zero vector;
    # the PROJ-042 enrichment job patches real vectors in retrospect.
    vector = [0.0] * VECTOR_SIZE

    point = PointStruct(
        id=point_id,
        vector=vector,
        payload={
            "event_type": "status_change",
            "actor": "hook",
            "task_id": task_id,
            "proj_id": proj_id,
            "detail": detail,
            "timestamp": now,
            "session_id": session_id,
        },
    )
    try:
        client.upsert(collection_name=EVENTS_COLLECTION, points=[point])
    except Exception as exc:
        log(f"Warning: could not write event to {EVENTS_COLLECTION}: {exc}")


def run_generator(proj_id: str, cwd: Optional[str]) -> None:
    """Re-run the file hierarchy generator for the given project."""
    script = Path(cwd or ".") / "team" / "thoth" / "tools" / "generate-file-hierarchy.py"
    cmd = [sys.executable, str(script), "--project", proj_id]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=60,
        )
        if result.stdout:
            log(f"generator stdout: {result.stdout.strip()}")
        if result.stderr:
            log(f"generator stderr: {result.stderr.strip()}")
    except Exception as exc:
        log(f"Warning: generator failed: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    # Read hook input from stdin
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        hook_data = json.loads(raw)
    except Exception as exc:
        log(f"Could not parse stdin JSON: {exc}")
        sys.exit(0)

    tool_input = hook_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    cwd = hook_data.get("cwd", ".")
    session_id: str = hook_data.get("session_id", "")

    # Check this is a tasks.md file we care about
    if not re.search(r"planning/projects/[^/]+/tasks\.md$", file_path):
        sys.exit(0)

    proj_id = parse_proj_id(file_path)
    if not proj_id:
        log(f"Could not parse proj_id from path: {file_path}")
        sys.exit(0)

    log(f"Detected edit to {file_path} for {proj_id}")

    # Read current file content
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except Exception as exc:
        log(f"Could not read {file_path}: {exc}")
        sys.exit(0)

    # Parse rows from file
    file_rows = parse_table_rows(content)
    if not file_rows:
        log("No task rows found in file; skipping sync.")
        sys.exit(0)

    # Connect to Qdrant
    try:
        client = get_client(TASKS_COLLECTION)
    except Exception as exc:
        log(f"Could not connect to Qdrant: {exc}")
        sys.exit(0)

    # Fetch current Qdrant statuses
    try:
        qdrant_tasks = fetch_qdrant_statuses(client, proj_id)
    except Exception as exc:
        log(f"Could not fetch tasks from Qdrant: {exc}")
        sys.exit(0)

    # Compare and update
    changes = 0
    for row in file_rows:
        task_id = row["task_id"]
        new_status = row["status"]

        qdrant_entry = qdrant_tasks.get(task_id)
        if not qdrant_entry:
            log(f"Task {task_id} not found in Qdrant; skipping.")
            continue

        old_status = qdrant_entry["payload"].get("status", "")
        point_id = qdrant_entry["point_id"]

        if old_status == new_status:
            continue  # No change

        log(f"{task_id}: {old_status} → {new_status}")
        try:
            update_task_status(client, point_id, new_status)
            write_event(client, task_id, proj_id, old_status, new_status, session_id)
            changes += 1
        except Exception as exc:
            log(f"Error updating {task_id}: {exc}")

    log(f"{changes} status change(s) synced to Qdrant.")

    # Re-run generator for this project
    run_generator(proj_id, cwd)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Hook must never block Claude — catch-all
        print(f"[sync-tasks] Unexpected error: {exc}", file=sys.stderr)
    sys.exit(0)
