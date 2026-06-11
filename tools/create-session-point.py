#!/usr/bin/env python3
"""
create-session-point.py — Team-Lead brief authoring (PROJ-039 R-007, § 2.6).

At dispatch the Team Lead creates the canonical `session` point in
`session_substrate`, carrying the brief (text + dispatch_ts + target_agent) with
the `brief` named vector. The agent then retrieves its brief from this point at
session start (no `team/{agent}/incoming/` read — DT-002/AC-001).

This is the one legitimate full-point upsert on a `session` point (creation only,
SD-3-001).

Usage:
  create-session-point.py --session-id <id> --agent <name> \
      --work-item PROJ-XXX/T-YYY (--brief-file <path> | --brief <text>) \
      [--target-agent <name>] [--dispatch-ts <iso>]

Auth: PODZONE_QDRANT_APIKEY (or wrap in `mcp__secrets__secret_run -k
      podzone_qdrant_apikey`). Exits non-zero on failure (loud — a dispatch that
      silently fails to create the point would strand the agent at session start).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib import session_substrate  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create a session_substrate `session` point (brief authoring).")
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--agent", required=True)
    ap.add_argument("--work-item", required=True, help="PROJ-XXX/T-YYY")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--brief", help="brief text inline")
    g.add_argument("--brief-file", help="path to a file holding the brief text")
    ap.add_argument("--target-agent", default=None)
    ap.add_argument("--dispatch-ts", default=None, help="ISO ts; defaults to now")
    args = ap.parse_args(argv)

    if args.brief_file:
        brief_text = Path(args.brief_file).read_text(encoding="utf-8")
    else:
        brief_text = args.brief
    if not brief_text.strip():
        print("create-session-point: brief text is empty", file=sys.stderr)
        return 2

    result = session_substrate.create_session_point(
        session_id=args.session_id,
        agent=args.agent,
        work_item=args.work_item,
        brief_text=brief_text,
        target_agent=args.target_agent,
        dispatch_ts=args.dispatch_ts,
    )
    print(f"created session point {result['point_id']} for {args.agent} / {args.work_item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
