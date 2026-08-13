#!/usr/bin/env python3
"""
session-stash-push.py — PROJ-039/T-255.

Callable primitive that shells out to `session_stash_substrate.push`
(design doc t254-session-stash-design.md §5). Invoked three ways, per the
design doc's wiring points:

  §5.1 compaction   — a `PostCompact` hook call, `--trigger compaction`,
                       `--content` the same retained-summary block Claude
                       Code already produces (T-257 wires this).
  §5.2 limit_stop    — `launch.sh`'s `session_limit` classify_exit branch,
                       `--trigger limit_stop`, `--content` the session's own
                       final response text (T-257 wires this).
  §5.3 explicit      — invoked directly by an agent recognising mid-session
                       it's approaching a deliberate handoff, the same way
                       `tools/conclude-planning-session.py` is already
                       invoked as a standalone `tools/` script.

This script builds and wires only the primitive (T-255) — the two automatic
trigger points (§5.1/§5.2) are T-257's job, not this one's.

Usage:
    python3 tools/session-stash-push.py \\
        --brief-id "hephaestus/2026-08-13-t255-session-stash-push" \\
        --session-id "$CLAUDE_SESSION_ID" \\
        --agent hephaestus \\
        --work-item PROJ-039/T-255 \\
        --trigger explicit \\
        --content "Built collections/session_stash.yaml + setup script + \\
                    substrate module; tests pass; PR not yet opened."

`--content-file PATH` may be used instead of `--content` (reads the file as
the narrative, e.g. a compaction summary or `${LAST_STDOUT}` tail already
written to disk by a caller).

Prints the point id on success and exits 0. A genuine Qdrant/auth failure
propagates (loud, non-zero exit) — unlike `conclude-planning-session.py`'s
soft-skip posture, a stash push has no "session row doesn't exist yet"
degenerate case to tolerate.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib import session_stash_substrate  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brief-id", required=True)
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--agent", required=True)
    ap.add_argument("--work-item", default=None)
    ap.add_argument("--trigger", required=True,
                     choices=list(session_stash_substrate.VALID_TRIGGERS))
    ap.add_argument("--content", default=None)
    ap.add_argument("--content-file", default=None,
                     help="read content from a file instead of --content")
    args = ap.parse_args()

    content = args.content
    if args.content_file:
        content = Path(args.content_file).read_text(encoding="utf-8", errors="replace")
    if not content or not content.strip():
        print("session-stash-push: --content or --content-file (non-empty) is required",
              file=sys.stderr)
        return 2

    result = session_stash_substrate.push(
        args.brief_id,
        args.session_id,
        args.agent,
        args.work_item,
        args.trigger,
        content,
    )
    print(result["point_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
