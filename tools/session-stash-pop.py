#!/usr/bin/env python3
"""
session-stash-pop.py — PROJ-039/T-256.

Callable primitive that shells out to `session_stash_substrate.pop` (design
doc t254-session-stash-design.md §4). This is the primitive T-257 wires into
`SessionStart`'s brief-first materialise path (§5.4) — this script builds
only the callable primitive, T-257 does the wiring.

Usage:
    python3 tools/session-stash-pop.py \\
        --brief-id "hephaestus/2026-08-13-t256-session-stash-pop" \\
        --session-id "$CLAUDE_SESSION_ID"

On an active stash entry: prints its `content` to stdout, marks it consumed,
and exits 0. On nothing to resume (no point, or already consumed): prints
nothing to stdout and exits 1, so a caller script can branch on exit code
without parsing output. A genuine Qdrant/auth failure propagates (loud,
non-zero exit).
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
    args = ap.parse_args()

    payload = session_stash_substrate.pop(args.brief_id, args.session_id)
    if payload is None:
        print("session-stash-pop: nothing to resume", file=sys.stderr)
        return 1

    print(payload.get("content", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
