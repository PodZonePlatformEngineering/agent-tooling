#!/usr/bin/env python3
"""
planning-materialise.py — full DB (+ Qdrant briefs) -> ``.planning/`` seed
(PROJ-029 plannerapi BCP mechanism, spec §6.2.1, build item 5/6).

Two callers:
  * a one-off manual run to seed a new team-lead home repo's ``.planning/``
    (or a full-resync recovery if the interim mirror is ever suspected
    stale/corrupt) — ``python3 planning-materialise.py --repo-dir .``
  * ``hooks/planning-session-end-mirror.py``'s ``SessionEnd`` pass, which
    imports and calls ``lib.planning_mirror`` directly rather than
    shelling out to this script.

Naming matches the existing ``trainee-materialise.py``/
``session-materialise.py`` hook vocabulary (DB/Qdrant -> local), per spec
§6.2.1. Degrades soft — never raises, always exits 0 (logs the failure
instead); a BCP mirror script that crashes the session it's meant to keep
working during an outage would defeat its own purpose.
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
    print(f"[planning-materialise] {msg}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-dir",
        default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(),
        help="home repo root containing (or to contain) .planning/",
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--skip-briefs", action="store_true", help="skip the Qdrant briefs mirror"
    )
    args = parser.parse_args()

    try:
        from lib import planning_mirror
    except Exception as exc:
        log(f"could not import lib.planning_mirror (degrading soft): {exc}")
        return 0

    result = planning_mirror.materialise(args.repo_dir, database_url=args.database_url)
    if result["ok"]:
        log(f"materialise ok: {result['counts']}")
    else:
        log(f"materialise failed (degrading soft): {result['error']}")

    if not args.skip_briefs:
        briefs_result = planning_mirror.mirror_briefs(args.repo_dir)
        if briefs_result["ok"]:
            log(f"briefs mirror ok: {briefs_result['count']} written")
        else:
            log(f"briefs mirror failed (degrading soft): {briefs_result['error']}")
        result["briefs"] = briefs_result

    print(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
