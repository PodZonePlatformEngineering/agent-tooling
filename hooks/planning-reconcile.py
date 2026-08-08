#!/usr/bin/env python3
"""
planning-reconcile.py — replay ``.planning/pending-changes.jsonl`` against
the real RPCs once Neon is reachable again (PROJ-029 plannerapi BCP
mechanism, spec §6.2.4, build item 5/6).

Manual/operator-run CLI, not hook-wired (there is no reliable "Neon just
came back" event to trigger on automatically — the team lead runs this once
they know connectivity is restored, or a future ``/schedule`` job could poll
it; out of scope here per the brief's own scope boundary). Replays each
queued record in order, committing per-line so a failure partway through
doesn't lose already-successful earlier replays, then clears the journal and
runs a full materialise to refresh the mirror from the now-current DB state.

Safe to re-run: the underlying RPCs are themselves idempotent where it
matters (see ``006_rpcs.sql`` / ``lib.planning_mirror.call_rpc``'s
docstring), and any record that fails to replay is left queued rather than
dropped.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def log(msg: str) -> None:
    print(f"[planning-reconcile] {msg}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-dir",
        default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(),
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--sub",
        default=None,
        help="team-lead sub to impersonate for the RPC calls (default: "
        "PLANNING_RECONCILE_SUB env, or the podzone placeholder sub)",
    )
    args = parser.parse_args()

    from lib import planning_mirror

    pending = planning_mirror.read_pending_changes(args.repo_dir)
    log(f"{len(pending)} queued change(s) found")

    result = planning_mirror.reconcile(
        args.repo_dir, database_url=args.database_url, sub=args.sub
    )
    log(f"replayed {result['replayed']}, {len(result['failed'])} failed")
    for f in result["failed"]:
        log(f"  FAILED rpc={f['record'].get('rpc')!r}: {f['error']}")
    if result["materialise"].get("ok"):
        log(f"post-reconcile materialise ok: {result['materialise']['counts']}")
    elif result["materialise"]:
        log(f"post-reconcile materialise failed: {result['materialise'].get('error')}")

    print(json.dumps(result, default=str))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
