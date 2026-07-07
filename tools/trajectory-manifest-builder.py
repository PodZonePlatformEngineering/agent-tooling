#!/usr/bin/env python3
"""trajectory-manifest-builder.py — emit trajectory-manifest.yaml for a design.

PROJ-038/T-002 — operator-side input to the decay-detector. Auto-generates
the manifest by scanning four sources (Claude Code JSONLs, podzoneTeam
outbox + incoming, cloud Qdrant `sessions`) for artefacts mentioning the
project ID.

Stage-1 design decision (Hermes lean, option 2): project-to-cwd-slug mapping
lives in each project's `spec.md` frontmatter (`cwd_slugs: [...]`), falling
back to `READMEFIRST.md` for projects with no spec.md. Override with
`--cwd-slug` (repeatable).

Example:

    tools/trajectory-manifest-builder.py \\
        --project-dir ~/workspace/podzoneTeam/planning/projects/PROJ-003-gitopsapi-product \\
        --team-root  ~/workspace/podzoneTeam \\
        --include outbox --include incoming --dry-run

See team/hephaestus/incoming/2026-05-27-trajectory-manifest-builder.md for
the full brief.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib import manifest_builder  # noqa: E402
from lib.decay.manifest import ManifestError, load_manifest  # noqa: E402


def _default_team_root(project_dir: Path) -> Path | None:
    """Walk up from project-dir looking for `.../{team_root}/planning/projects/`."""
    for parent in project_dir.resolve().parents:
        if parent.name == "planning" and parent.parent.is_dir():
            return parent.parent
    return None


def _default_output(project_dir: Path) -> Path:
    iters = project_dir / "iterations"
    n = 1
    if iters.is_dir():
        nums = []
        for d in iters.iterdir():
            if d.is_dir() and d.name.startswith("iteration-"):
                try:
                    nums.append(int(d.name.split("-", 1)[1]))
                except ValueError:
                    continue
        if nums:
            n = max(nums)
    return iters / f"iteration-{n}" / "trajectory-manifest.yaml"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trajectory-manifest-builder",
        description="Build a trajectory-manifest.yaml for a design project.",
    )
    p.add_argument("--project-dir", required=True, type=Path,
                   help="Design project directory (e.g. .../PROJ-003-gitopsapi-product/).")
    p.add_argument("--team-root", type=Path, default=None,
                   help="podzoneTeam root for outbox/incoming scans. "
                        "Auto-detected if --project-dir is under a planning/projects/ tree.")
    p.add_argument("--output", type=Path, default=None,
                   help="Manifest output path (default: "
                        "<project-dir>/iterations/iteration-N/trajectory-manifest.yaml).")
    p.add_argument("--include", action="append",
                   choices=list(manifest_builder.VALID_SOURCES),
                   help="Source(s) to scan; repeat. Default: all four.")
    p.add_argument("--project-id", default=None,
                   help="PROJ-XXX override (default: derived from --project-dir basename).")
    p.add_argument("--cwd-slug", action="append", default=None,
                   help="Override cwd_slugs from spec/READMEFIRST frontmatter (repeatable).")
    p.add_argument("--start-date", default=None,
                   help="ISO date/timestamp lower bound (inclusive).")
    p.add_argument("--end-date", default=None,
                   help="ISO date/timestamp upper bound (inclusive).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print manifest YAML to stdout; do not write.")
    p.add_argument("--verbose", action="store_true",
                   help="Print include/reject reasons to stderr.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_dir = args.project_dir.resolve()
    if not project_dir.is_dir():
        print(f"error: --project-dir does not exist: {project_dir}", file=sys.stderr)
        return 2

    team_root = args.team_root.resolve() if args.team_root else _default_team_root(project_dir)
    if team_root is None and (args.include is None or
                              {"outbox", "incoming"} & set(args.include)):
        print(
            "error: could not auto-detect --team-root (project-dir not under "
            "a .../planning/projects/ tree). Pass --team-root explicitly.",
            file=sys.stderr,
        )
        return 2

    include = tuple(args.include) if args.include else manifest_builder.VALID_SOURCES

    result = manifest_builder.build(
        project_dir=project_dir,
        team_root=team_root or Path("/dev/null"),
        project_id=args.project_id,
        cwd_slugs=args.cwd_slug,
        include=include,
        start_iso=args.start_date,
        end_iso=args.end_date,
        verbose=args.verbose,
    )

    yaml_text = manifest_builder.dump_yaml(result.manifest)

    if args.verbose:
        for line in result.verbose_log:
            print(line, file=sys.stderr)

    if args.dry_run:
        sys.stdout.write(yaml_text)
    else:
        out = args.output.resolve() if args.output else _default_output(project_dir)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(yaml_text, encoding="utf-8")
        # AC-MB-001: validate by re-loading via the canonical loader.
        try:
            loaded = load_manifest(out)
        except ManifestError as exc:
            print(f"error: emitted manifest failed validation: {exc}",
                  file=sys.stderr)
            return 1
        print(
            f"[trajectory-manifest-builder] wrote {out} "
            f"({len(loaded)} artefacts)",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
