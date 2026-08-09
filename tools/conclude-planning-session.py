#!/usr/bin/env python3
"""conclude-planning-session.py — PROJ-029/T-019.

Concludes a `planning.session` row via the `conclude_session()` RPC — the
counterpart to register-planning-session.py (T-018). Resolves the session
to conclude as the most recent NOT-YET-`cleaned_up` row for `--brief-id`
(brief-first re-launches each get their own row, T-018's
`session_brief_id_key` fix, so this always targets the right one). Two
call sites both resolve through this same query: `launch.sh`'s finalise
(`--status concluded`, session likely `dispatching`/`in_flight` beforehand)
and `/consolidate-tasks`' Step 3 authoritative close (`--status cleaned_up`,
session already sitting in `concluded` from the first call).

Intended callers: `launch.sh`'s own finalise step, unconditionally, at
every exit path (complete / session_limit exhausted / other failure) — per
the PROJ-029/T-019 Fork 1 operator ruling, `ready_for_review` is a
mechanical breadcrumb set by the deployment infrastructure, never by agent
judgment; and `/consolidate-tasks`' Step 3, for the authoritative close
after Team-Lead review. Not intended to be called by a dispatched session
itself.

Best-effort by design: if no session row exists for `--brief-id` (the Team
Lead never ran register-planning-session.py before launching — registration
is optional, T-018) or `PLANNING_DATABASE_URL` is unset, this exits 0 with a
note on stderr rather than failing the whole launch over board visibility.

Usage:
    python3 conclude-planning-session.py \\
        --brief-id "hermes/2026-08-09-t018-rehearsal-test" \\
        --status concluded --task-status ready_for_review \\
        --outcome-note-file /tmp/last-response.txt \\
        --pr-ref "academy-frontend#33" --pr-ref "academy-admin#68"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import planning_mirror  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--brief-id", required=True)
    ap.add_argument("--status", default="concluded", choices=["concluded", "cleaned_up"])
    ap.add_argument("--task-status", default=None,
                     help="e.g. ready_for_review — applied to every task_id on the session")
    ap.add_argument("--outcome-note", default=None)
    ap.add_argument("--outcome-note-file", default=None,
                     help="read outcome_note from a file instead of --outcome-note")
    ap.add_argument("--pr-ref", action="append", default=[], dest="pr_refs",
                     help="repo#number (repeatable)")
    args = ap.parse_args()

    outcome_note = args.outcome_note
    if args.outcome_note_file:
        outcome_note = Path(args.outcome_note_file).read_text(encoding="utf-8", errors="replace")

    try:
        conn = planning_mirror.connect()
    except RuntimeError as e:
        print(f"conclude-planning-session: skipping (soft) — {e}", file=sys.stderr)
        return 0

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM planning.session WHERE brief_id = %s "
                "AND status != 'cleaned_up' "
                "ORDER BY launched_at DESC LIMIT 1",
                (args.brief_id,),
            )
            row = cur.fetchone()
        if row is None:
            print(f"conclude-planning-session: skipping (soft) — no open session row "
                  f"for brief_id {args.brief_id!r} (never registered, or already "
                  f"concluded)", file=sys.stderr)
            return 0
        session_id = str(row[0])

        result = planning_mirror.call_rpc(
            conn,
            "conclude_session",
            {
                "session_id": session_id,
                "status": args.status,
                "outcome_note": outcome_note,
                "pr_refs": args.pr_refs or None,
                "task_status": args.task_status,
            },
        )
    finally:
        conn.close()

    if not result:
        print("conclude-planning-session: RPC returned no row", file=sys.stderr)
        return 1
    print(session_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
