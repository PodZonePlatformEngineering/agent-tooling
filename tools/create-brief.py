#!/usr/bin/env python3
"""
create-brief.py — brief authoring (PROJ-039/T-043).

Sibling of ``create-session-point.py``, but for the general-purpose `briefs`
collection. A brief is a FIRST-CLASS point, NOT keyed to a session: the Team Lead
(or a curriculum author, for a trainee) upserts the brief point from the
committed brief file; sessions later reference it by ``brief_id`` and the runtime
sid is appended to ``session_ids[]`` at SessionStart (SessionStart materialise).

The point id is ``uuid5(NAMESPACE_DNS, brief_id)`` so re-authoring the same
``brief_id`` converges on the same point (idempotent). The `brief` named vector is
embedded from the brief body (token-safe per T-027; the full body is stored
unchanged).

`approved` is the ADR-008 D5 execution gate — a brief must be `approved` before a
session can be materialised for it. Author with `--status draft` (default) and
promote with `--approve` (or `--status approved`) on operator/lead sign-off.

Usage:
  create-brief.py --brief-id training/2026-07-02-prompt-basics-alex \
      --team training --author athena --assignee alex --assignee-type trainee \
      --body-file <path> [--work-item PROJ-XXX/T-YYY ...] [--summary <text>] \
      [--origin-path <committed-file>] [--status draft|approved] [--approve]

Auth: PODZONE_QDRANT_APIKEY (or wrap in `mcp__secrets__secret_run -k
      podzone_qdrant_apikey`). Exits non-zero on failure (loud — a dispatch that
      silently fails to create the brief would strand the agent at session start).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib import brief_substrate  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create/upsert a `briefs` collection point (brief authoring).")
    ap.add_argument("--brief-id", required=True,
                    help="{team}/{date}-{slug} (trainee: training/{date}-{curriculum-slug}-{trainee})")
    ap.add_argument("--team", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--assignee", required=True, help="agent name or trainee id")
    ap.add_argument("--assignee-type", default=brief_substrate.ASSIGNEE_AGENT,
                    choices=(brief_substrate.ASSIGNEE_AGENT, brief_substrate.ASSIGNEE_TRAINEE))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--body", help="brief body markdown inline")
    g.add_argument("--body-file", help="path to the committed brief file (its markdown is the body)")
    ap.add_argument("--work-item", action="append", default=[], dest="work_items",
                    help="canonical work-item ref PROJ-XXX/T-YYY (repeatable)")
    ap.add_argument("--summary", default=None)
    ap.add_argument("--origin-path", default=None,
                    help="committed brief file path (the human/PR record, git per D4)")
    ap.add_argument("--status", default=brief_substrate.STATUS_DRAFT,
                    choices=brief_substrate.BRIEF_STATUSES)
    ap.add_argument("--approve", action="store_true",
                    help="shorthand for --status approved (operator/lead sign-off, D5 gate)")
    args = ap.parse_args(argv)

    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
        origin_path = args.origin_path or args.body_file
    else:
        body = args.body
        origin_path = args.origin_path
    if not body.strip():
        print("create-brief: brief body is empty", file=sys.stderr)
        return 2

    status = brief_substrate.STATUS_APPROVED if args.approve else args.status

    result = brief_substrate.create_brief(
        brief_id=args.brief_id,
        team=args.team,
        author=args.author,
        assignee=args.assignee,
        assignee_type=args.assignee_type,
        body=body,
        status=status,
        work_items=args.work_items,
        summary=args.summary,
        origin_path=origin_path,
    )
    print(f"created brief point {result['point_id']} for {args.brief_id} "
          f"(assignee={args.assignee}, status={status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
