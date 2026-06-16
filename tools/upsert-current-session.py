#!/usr/bin/env python3
"""
upsert-current-session.py — scrape and upsert the *current* session JSONL.

Invoked from the /session-end skill (T-006) as the final write of a session.
Best-effort: never blocks close-out; logs and exits 0 on any error.

Usage:
  upsert-current-session.py --session-id <uuid> --cwd <path> \
      --data-source session_end_skill --status ended

Either argument can be omitted; the script falls back to $CLAUDE_SESSION_ID
and os.getcwd() respectively. If neither can be resolved, exits 0 with a
warning.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib import jsonl_scrape, sessions_upsert  # noqa: E402


def _log(msg: str) -> None:
    print(f"[upsert-current-session] {msg}", file=sys.stderr)


def _resolve_jsonl(session_id: str, cwd: str) -> Optional[Path]:
    encoded = cwd.replace("/", "-")
    candidate = Path.home() / ".claude" / "projects" / encoded / f"{session_id}.jsonl"
    return candidate if candidate.is_file() else None


def run(
    session_id: Optional[str],
    cwd: Optional[str],
    data_source: str,
    status: Optional[str],
    push_failed: bool = False,
) -> int:
    session_id = session_id or os.environ.get("CLAUDE_SESSION_ID")
    cwd = cwd or os.getcwd()
    if not session_id:
        _log("no session_id (arg or $CLAUDE_SESSION_ID); skipping")
        return 0

    jsonl = _resolve_jsonl(session_id, cwd)
    if jsonl is None:
        _log(
            f"jsonl not found for session {session_id[:16]}… "
            f"under ~/.claude/projects/{cwd.replace('/', '-')}/; skipping"
        )
        return 0

    try:
        payload = jsonl_scrape.scrape(jsonl)
    except Exception as exc:
        _log(f"scrape failed: {exc}")
        return 0

    result = sessions_upsert.upsert_session(
        payload,
        data_source=data_source,
        status=status,
        extra_payload={"push_failed": True} if push_failed else None,
    )
    if result["ok"]:
        _log(
            f"upserted session {session_id[:16]}… "
            f"data_source={data_source} status={result['payload']['status']}"
        )
    else:
        _log(f"upsert failed: {result['reason']}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session-id", help="Session UUID (default: $CLAUDE_SESSION_ID)")
    ap.add_argument("--cwd", help="Session cwd (default: os.getcwd())")
    ap.add_argument(
        "--data-source",
        default="session_end_skill",
        choices=sorted(sessions_upsert.VALID_DATA_SOURCES),
    )
    ap.add_argument("--status", default="ended")
    ap.add_argument(
        "--push-failed",
        action="store_true",
        help="Stamp push_failed:true on the session point (PROJ-032 T-005: "
        "/session-end could not push the home repo).",
    )
    args = ap.parse_args(argv)
    return run(
        session_id=args.session_id,
        cwd=args.cwd,
        data_source=args.data_source,
        status=args.status,
        push_failed=args.push_failed,
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        _log(f"unexpected error: {exc}")
        sys.exit(0)
