#!/usr/bin/env python3
"""
stop-heartbeat.py — Stop hook.

Fires at the end of each Claude turn. PROJ-034 T-005 behaviour:

  1. Locate this session's JSONL at ~/.claude/projects/<encoded-cwd>/<sid>.jsonl
  2. If present: scrape() → full payload → upsert with data_source=stop_hook,
     status=in_progress.
  3. If missing: fall back to a heartbeat-only upsert (legacy behaviour).
  4. Wrap the work in a hard time ceiling (2 s) — better to lose one point
     than block the agent turn.

Input: JSON on stdin (Claude Code Stop hook format).
Exit: always 0 (best-effort, non-blocking).
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


HARD_TIMEOUT_SECONDS = 2

CLOUD_QDRANT_URL = (
    "https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io"
)
SESSIONS_COLLECTION = "sessions"


def _log(msg: str) -> None:
    print(f"[stop-heartbeat] {msg}", file=sys.stderr)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _locate_agent_tooling() -> Optional[Path]:
    """Find the agent-tooling repo root (contains lib/sessions_upsert.py)."""
    candidates: list[Path] = []
    env = os.environ.get("AGENT_TOOLING_DIR")
    if env:
        candidates.append(Path(env))
    # Walk up from this script — covers the canonical agent-tooling/hooks/ copy
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidates.append(ancestor)
        if ancestor == ancestor.parent:
            break
    # Common workstation locations
    candidates.append(Path.home() / "workspace" / "agent-tooling")
    # Sibling under ~/sessions/<dir>/agent-tooling — the worktree case
    candidates.extend(
        (Path.home() / "sessions").glob("*/agent-tooling")
        if (Path.home() / "sessions").is_dir()
        else []
    )
    for c in candidates:
        if (c / "lib" / "sessions_upsert.py").is_file():
            return c
    return None


def _encode_cwd_to_project_dir(cwd: str) -> str:
    """Mirror Claude Code's ~/.claude/projects/<encoded> convention.

    Each '/' in the absolute path becomes '-'. A leading '/' becomes a leading
    '-'. Example: /Users/x/y → -Users-x-y.
    """
    return cwd.replace("/", "-")


def _resolve_jsonl_path(session_id: str, cwd: Optional[str]) -> Optional[Path]:
    if not cwd:
        return None
    encoded = _encode_cwd_to_project_dir(cwd)
    candidate = Path.home() / ".claude" / "projects" / encoded / f"{session_id}.jsonl"
    return candidate if candidate.is_file() else None


def _heartbeat_only_upsert(
    session_id: str, cwd: Optional[str], agent_tooling: Optional[Path]
) -> None:
    """Legacy heartbeat-only path. Used when JSONL not yet on disk.

    Goes through the canonical lib.qdrant_http primitive (stdlib urllib, no
    `requests` dependency). Best-effort: a missing key or transient error is
    logged loudly, never silently swallowed, and never crashes the turn.
    """
    if agent_tooling is None:
        _log("agent-tooling not located; skipping heartbeat")
        return
    if str(agent_tooling) not in sys.path:
        sys.path.insert(0, str(agent_tooling))
    try:
        from lib import qdrant_http  # type: ignore
    except Exception as exc:
        _log(f"qdrant_http import failed; skipping heartbeat: {exc}")
        return

    import uuid as _uuid
    pid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, session_id))
    now = _now_iso()
    payload = {
        "session_id": session_id,
        "last_heartbeat_ts": now,
        "status": "in_progress",
        "data_source": "stop_hook",
        "updated_at": now,
    }
    if cwd:
        payload["cwd"] = cwd
    point = {"id": pid, "vector": [0.0] * 768, "payload": payload}
    try:
        qdrant_http.upsert_points(
            [point], collection=SESSIONS_COLLECTION, timeout=5
        )
        _log(f"heartbeat-only upserted for {session_id[:16]}…")
    except qdrant_http.QdrantAuthError as exc:
        _log(f"PODZONE_QDRANT_APIKEY unavailable; skipping heartbeat: {exc}")
    except qdrant_http.QdrantError as exc:
        _log(f"heartbeat upsert failed: {exc}")


def _full_payload_upsert(jsonl_path: Path, agent_tooling: Path) -> bool:
    """Scrape + upsert via the shared library. Returns True on success."""
    if str(agent_tooling) not in sys.path:
        sys.path.insert(0, str(agent_tooling))
    try:
        from lib import jsonl_scrape, sessions_upsert  # type: ignore
    except Exception as exc:
        _log(f"lib import failed: {exc}")
        return False

    try:
        payload = jsonl_scrape.scrape(jsonl_path)
    except Exception as exc:
        _log(f"scrape failed: {exc}")
        return False

    now = _now_iso()
    try:
        result = sessions_upsert.upsert_session(
            payload,
            data_source="stop_hook",
            status="in_progress",
            last_heartbeat_ts=now,
        )
    except sessions_upsert.QdrantAuthError as exc:
        # Loud config error — log and fall back, but never crash the turn.
        _log(f"PODZONE_QDRANT_APIKEY unavailable; skipping upsert: {exc}")
        return False
    if result["ok"]:
        _log(f"full payload upserted for {payload['session_id'][:16]}…")
        return True
    _log(f"upsert failed: {result['reason']}")
    return False


def _handle_timeout(signum, frame):  # type: ignore[no-untyped-def]
    raise TimeoutError("stop-heartbeat exceeded hard ceiling")


def run(session_id: str, cwd: Optional[str]) -> None:
    started = time.monotonic()
    jsonl = _resolve_jsonl_path(session_id, cwd)
    agent_tooling = _locate_agent_tooling()

    if jsonl is not None and agent_tooling is not None:
        if _full_payload_upsert(jsonl, agent_tooling):
            _log(f"done in {(time.monotonic() - started) * 1000:.0f} ms")
            return
        # fall through to heartbeat
    _heartbeat_only_upsert(session_id, cwd, agent_tooling)
    _log(f"done in {(time.monotonic() - started) * 1000:.0f} ms")


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        hook_data = json.loads(raw)
    except Exception as exc:
        _log(f"could not parse stdin JSON: {exc}")
        sys.exit(0)

    session_id: Optional[str] = hook_data.get("session_id")
    cwd: Optional[str] = hook_data.get("cwd")
    if not session_id:
        _log("no session_id in hook input; skipping.")
        sys.exit(0)

    # Hard ceiling — POSIX SIGALRM. On platforms without SIGALRM (e.g. Windows)
    # the hook runs without a hard cap (still fast in practice).
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _handle_timeout)
        signal.alarm(HARD_TIMEOUT_SECONDS)
    try:
        run(session_id, cwd)
    except TimeoutError as exc:
        _log(str(exc))
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _log(f"unexpected error: {exc}")
    sys.exit(0)
