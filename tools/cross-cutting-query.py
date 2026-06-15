#!/usr/bin/env python3
"""
cross-cutting-query.py — CLI for the AC-008 / DT-012 cross-cutting query.

Given a ``session_id``, prints the "what the brief asked vs what the agent did"
comparison, joining `session_substrate` (brief + response) with
`claude_session_telemetry` (CST activity) from the **single** cloud Qdrant
instance (PROJ-033/T-019 co-located CST on cloud, clearing F-2-007). The
single-instance property is printed and, with ``--assert-single-instance``,
enforced with a non-zero exit if the join ever touched more than one instance.

Needs ``PODZONE_QDRANT_APIKEY``. Run under
``mcp__secrets__secret_run -k podzone_qdrant_apikey`` / ``secretctl run``.

This is a tool, not a hook: failures are loud (non-zero exit), per the
qdrant_http philosophy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib import cross_cutting  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session_id", help="the session UUID to query")
    ap.add_argument(
        "--json", action="store_true", help="emit the raw result as JSON"
    )
    ap.add_argument(
        "--assert-single-instance",
        action="store_true",
        help="exit non-zero if the join touched more than one Qdrant instance (DT-012)",
    )
    args = ap.parse_args(argv)

    result = cross_cutting.cross_cutting_query(args.session_id)

    if args.json:
        print(json.dumps(result.__dict__, indent=2, default=str))
    else:
        print(result.render())

    if args.assert_single_instance and not result.single_instance:
        print(
            f"\nFAIL: join touched {len(result.instances_touched)} instances "
            f"{result.instances_touched} — AC-008 requires one.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
