#!/usr/bin/env python3
"""register-planning-session.py — PROJ-029/T-018.

Registers a session in `planning.session` (the relational board plannerapi's
GUI reads) via the `register_session()` RPC. This is orthogonal to the Qdrant
`briefs`/`session_substrate` machinery `session-materialise.py` already owns
(brief authoring, session_ids[] append) — this script only makes the
dispatch visible on the board.

Resolves each `--work-item PROJ-XXX/T-YYY` ref to a `planning.task.id` UUID
via a direct lookup (register_session's own p_task_ids param is `uuid[]`, not
refs). If no `--work-item` is given, reads the brief's own `work_items[]`
payload (authored by create-brief.py --work-item) instead of requiring the
caller to repeat it.

Usage:
    python3 register-planning-session.py \\
        --brief-id "hermes/2026-08-09-launch-session-planning-registration" \\
        --agent hermes --home-repo home-podzone-hermes \\
        [--work-item PROJ-XXX/T-YYY ...]

Prints the new `planning.session.id` UUID on stdout (nothing else), so a
caller can capture it: `SID=$(python3 register-planning-session.py ...)`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import brief_substrate, planning_mirror  # noqa: E402


def _resolve_task_ids(conn, work_items: list[str]) -> list[str]:
    ids: list[str] = []
    with conn.cursor() as cur:
        for ref in work_items:
            if "/" not in ref:
                raise ValueError(
                    f"register-planning-session: work-item ref {ref!r} must be "
                    "PROJ-XXX/T-YYY (project ref / task ref)"
                )
            project_ref, task_ref = ref.split("/", 1)
            cur.execute(
                "SELECT t.id FROM planning.task t "
                "JOIN planning.project p ON p.id = t.project_id "
                "WHERE p.ref = %s AND t.ref = %s",
                (project_ref, task_ref),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(
                    f"register-planning-session: no planning.task found for "
                    f"{ref!r} — create it first (planning.create_task via "
                    f"/create-task) before registering a session against it"
                )
            ids.append(str(row[0]))
    return ids


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--brief-id", required=True)
    ap.add_argument("--agent", required=True)
    ap.add_argument("--home-repo", required=True)
    ap.add_argument("--work-item", action="append", default=[], dest="work_items",
                     help="PROJ-XXX/T-YYY (repeatable); defaults to the brief's own work_items[]")
    ap.add_argument("--api-key", default=None, help="Qdrant API key override (else env)")
    args = ap.parse_args()

    work_items = args.work_items
    if not work_items:
        brief = brief_substrate.get_brief(args.brief_id, api_key=args.api_key)
        if not brief:
            print(f"HALT: brief {args.brief_id!r} not found in Qdrant — author it "
                  "first via create-brief.py", file=sys.stderr)
            return 1
        work_items = brief.get("work_items") or []
        if not work_items:
            print(f"HALT: brief {args.brief_id!r} has no work_items[] and none "
                  "were passed via --work-item — register_session requires at "
                  "least one task", file=sys.stderr)
            return 1

    conn = planning_mirror.connect()
    try:
        task_ids = _resolve_task_ids(conn, work_items)
        result = planning_mirror.call_rpc(
            conn,
            "register_session",
            {
                "brief_id": args.brief_id,
                "agent": args.agent,
                "task_ids": task_ids,
                "home_repo": args.home_repo,
            },
        )
    finally:
        conn.close()

    session_id = result[0] if result else None
    if not session_id:
        print("HALT: register_session returned no session id", file=sys.stderr)
        return 1
    print(session_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
