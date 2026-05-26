#!/usr/bin/env python3
"""decay-detector.py — PROJ-038 context-decay detector (CLI).

Reads a trajectory manifest + a design project directory; emits a Markdown
decay report grouped by category. See `lib/decay/` for detector logic and
`planning/projects/PROJ-038-decay-detector/spec.md` v1.3 for requirements.

CLI:
  decay-detector.py --project-dir <PATH>
  decay-detector.py --project-dir <PATH> --manifest <PATH> --output <PATH>
  decay-detector.py --batch --project-dir <PATH> [--project-dir <PATH> ...]
  decay-detector.py --project-dir <PATH> --noise-budget {low|medium|high}
  decay-detector.py --project-dir <PATH> --incremental
  decay-detector.py --project-dir <PATH> --trajectory-replay
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib.decay.runner import (  # noqa: E402
    DEFAULT_FILLER_PATH,
    run_batch,
    run_detection,
    run_trajectory_replay,
)


def _default_manifest(project_dir: Path, iteration: int) -> Path:
    return project_dir / "iterations" / f"iteration-{iteration}" / "trajectory-manifest.yaml"


def _default_output(project_dir: Path, iteration: int) -> Path:
    return project_dir / "iterations" / f"iteration-{iteration}" / "decay-report.md"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="decay-detector",
        description="PROJ-038 context-decay detector (structural; zero-LLM)",
    )
    p.add_argument("--project-dir", action="append", required=True,
                   type=Path,
                   help="Design project directory; repeat for --batch.")
    p.add_argument("--iteration", type=int, default=1,
                   help="Iteration number for default manifest/output paths.")
    p.add_argument("--manifest", type=Path, default=None,
                   help="Trajectory manifest path (single-project mode).")
    p.add_argument("--output", type=Path, default=None,
                   help="Report output path (single-project mode).")
    p.add_argument("--batch", action="store_true",
                   help="Treat all --project-dir as a batch.")
    p.add_argument("--noise-budget", choices=("low", "medium", "high"),
                   default="low")
    p.add_argument("--incremental", action="store_true",
                   help="Reprocess only entries newer than `.last-run-timestamp`.")
    p.add_argument("--trajectory-replay", action="store_true",
                   help="Emit per-session sub-report + origin-untraced flags (R-014).")
    p.add_argument("--filler-vocab", type=Path, default=DEFAULT_FILLER_PATH,
                   help="Path to off-topic filler vocabulary YAML.")
    p.add_argument("--run-date", type=str, default=None,
                   help="Fixed ISO-8601 generation timestamp (testing).")
    return p


def _parse_run_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generated_at = _parse_run_date(args.run_date)

    if args.batch or len(args.project_dir) > 1:
        results = run_batch(
            args.project_dir,
            iteration=args.iteration,
            filler_path=args.filler_vocab,
            noise_budget=args.noise_budget,
            incremental=args.incremental,
            generated_at=generated_at,
        )
        for r in results:
            print(f"[decay-detector] wrote {r.output_path} "
                  f"({len(r.events)} events)")
        return 0

    project_dir = args.project_dir[0]
    manifest_path = args.manifest or _default_manifest(
        project_dir, args.iteration
    )
    output_path = args.output or _default_output(
        project_dir, args.iteration
    )

    if args.trajectory_replay:
        result = run_trajectory_replay(
            project_dir=project_dir,
            manifest_path=manifest_path,
            output_path=output_path,
            filler_path=args.filler_vocab,
            noise_budget=args.noise_budget,
            generated_at=generated_at,
        )
    else:
        result = run_detection(
            project_dir=project_dir,
            manifest_path=manifest_path,
            output_path=output_path,
            filler_path=args.filler_vocab,
            noise_budget=args.noise_budget,
            incremental=args.incremental,
            generated_at=generated_at,
        )
    print(f"[decay-detector] wrote {result.output_path} "
          f"({len(result.events)} events)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
