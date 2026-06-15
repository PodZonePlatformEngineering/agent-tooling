#!/usr/bin/env python3
"""
migrate-legacy-tasking.py — CLI for the DT-014 / AC-007 additive migration.

Folds the active tasking state of the six legacy collections into
`session_substrate`, `point_type`-discriminated (DTD § 3.2). **Additive only** —
writes solely to `session_substrate`; never reads-modifies or deletes the source
collections (the C4 drop gate is a separate, operator-confirmed step). C1 is
reversible: undo == delete the migrated ids.

  --plan        (default) read the legacy collections and print the plan; no writes
  --apply       perform the upserts into session_substrate
  --reconcile   after apply, compare planned vs live point_type counts

Needs ``PODZONE_QDRANT_APIKEY``. Run under
``mcp__secrets__secret_run -k podzone_qdrant_apikey`` / ``secretctl run``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib import legacy_migration  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the migration (default: plan only)")
    ap.add_argument("--reconcile", action="store_true", help="check planned vs live counts after apply")
    ap.add_argument("--json", action="store_true", help="emit plan/reconcile as JSON")
    args = ap.parse_args(argv)

    migration = legacy_migration.collect_migration()

    if args.json:
        print(json.dumps({
            "source_counts": migration.source_counts,
            "selected_counts": migration.selected_counts,
            "type_counts": dict(migration.type_counts),
            "dedup_dropped": migration.dedup_dropped,
            "total": len(migration.points),
        }, indent=2))
    else:
        print(migration.summary())

    result = legacy_migration.write_migration(migration, dry_run=not args.apply)
    print(f"\nwrite: {json.dumps(result)}")

    if args.reconcile:
        rec = legacy_migration.reconcile(migration)
        print(f"reconcile: {json.dumps(rec)}")
        if not rec["ok"]:
            print("FAIL: migrated counts do not reconcile with the plan.", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
