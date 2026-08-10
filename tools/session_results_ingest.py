#!/usr/bin/env python3
"""
session_results_ingest.py — one-time backfill of every agent home repo's
`results/*.md` into the cloud Qdrant `session_results` collection.

PROJ-029/T-257 (plannerapi Design view v1 build; design doc
PROJ-029-plannerapi/t250-design-view-design.md §3.3/§1.4). Naming matches the
fleet's existing one-shot migration tools (`backfill-sessions.py`,
`planner_migrate/status_md.py`).

Parsing (design doc §1.4's grounded finding): newer files carry YAML
frontmatter (`type`, `session_id`, `agent`, `work_item`, `date`,
`status_transition` — see `lib/session_finalise.py:generate_session_result`).
Older, pre-frontmatter files carry none — this script falls back to the
filename convention `session-{date}-{proj-slug}[-{task-slug}][-{sid8}].md`.
A file's `project_ref` is considered **unparseable** only when NEITHER path
yields one; per operator direction (brief §"Operator direction" point 3) this
is surfaced loudly in the run's own summary output, never silently dropped
from the index — the point is still upserted (with `work_item`/`project_ref`
null) so it stays visible in `list` mode, just flagged for manual tagging.

Idempotent: deterministic point ids (`uuid5(NAMESPACE_DNS, "{home_repo}/{filename}")`)
mean a re-run converges, matching every other Qdrant backfill in this fleet.

Usage:
    python3 tools/session_results_ingest.py [--home-repos-dir DIR]
        [--collection NAME] [--dry-run] [--only REPO_NAME]

Rehearse against a disposable collection first (this fleet's standard
migration-rehearsal discipline, design doc §3.3's own reference to spec §12):
    python3 tools/session_results_ingest.py --collection session_results_rehearsal_XXX
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib import session_results_substrate as srs  # noqa: E402

DEFAULT_HOME_REPOS_DIR = REPO_ROOT.parent  # ~/workspace, sibling to agent-tooling

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)

# session-{YYYY-MM-DD}-{slug}[-{sid8}].md — sid8 is 8 lowercase hex chars, which
# never contains the literal 't' the task-number regex below looks for, so
# stripping it first cannot corrupt a real "t-NNN" match.
FILENAME_DATE_RE = re.compile(r"^session-(\d{4}-\d{2}-\d{2})-(.+)\.md$")
SID_SUFFIX_RE = re.compile(r"-[0-9a-f]{8}$")
PROJ_RE = re.compile(r"proj-?(\d+)", re.IGNORECASE)
TASK_RE = re.compile(r"t-?(\d+)", re.IGNORECASE)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict, body-after-frontmatter). Empty dict if none."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_text, body = m.group(1), m.group(2)
    fm: dict = {}
    for line in fm_text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm, body


def _parse_filename(filename: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Fallback parse of ``session-{date}-{slug}[-{sid8}].md``.

    Returns ``(date, work_item, project_ref)`` — any element may be None.
    """
    m = FILENAME_DATE_RE.match(filename)
    if not m:
        return None, None, None
    date, slug = m.group(1), m.group(2)
    slug = SID_SUFFIX_RE.sub("", slug)

    proj_m = PROJ_RE.search(slug)
    if not proj_m:
        return date, None, None
    project_ref = f"PROJ-{proj_m.group(1)}"

    task_m = TASK_RE.search(slug, proj_m.end())
    work_item = f"{project_ref}/T-{task_m.group(1)}" if task_m else None
    return date, work_item, project_ref


def _agent_from_home_repo(home_repo: str) -> str:
    # "home-{team}-{agent}" -> agent (best-effort; matches podzoneTeam's own
    # "Session setup" naming convention).
    parts = home_repo.split("-")
    return parts[-1] if len(parts) >= 3 else home_repo


def _commit_date(repo_dir: Path, rel_path: str) -> Optional[str]:
    """The commit date the file was first added, ISO-8601 — the design doc's
    "ground truth for when did this land" (§3.3/§6.1). Falls back to the most
    recent commit touching the file when the birth commit can't be resolved
    (e.g. a shallow clone)."""
    for args in (
        ["log", "--diff-filter=A", "--follow", "-1", "--format=%cI", "--", rel_path],
        ["log", "-1", "--format=%cI", "--", rel_path],
    ):
        r = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True, text=True, check=False,
        )
        out = r.stdout.strip()
        if r.returncode == 0 and out:
            return out
    return None


def parse_result_file(home_repo: str, path: Path) -> dict:
    """Parse one results/*.md file into the fields ``upsert_result`` needs.

    Returns a dict with an ``unparseable`` key (bool) — True only when no
    project_ref could be derived from either frontmatter or filename.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body_after_fm = _parse_frontmatter(text)

    fn_date, fn_work_item, fn_project_ref = _parse_filename(path.name)

    work_item = fm.get("work_item") or fn_work_item or None
    project_ref = (
        srs.project_ref_from_work_item(work_item) if work_item else fn_project_ref
    )
    date = fm.get("date") or fn_date or "unknown"
    agent = fm.get("agent") or _agent_from_home_repo(home_repo)
    # Embed/store the FULL file (frontmatter included) — matches
    # generate_session_result's own posture that git + this file are the
    # durable human-readable record; frontmatter-stripped body is only used to
    # derive a title when nothing better is available.
    title = path.stem

    return {
        "home_repo": home_repo,
        "filename": path.name,
        "body": text,
        "work_item": work_item,
        "project_ref": project_ref,
        "agent": agent,
        "date": date,
        "title": title,
        "unparseable": project_ref is None,
    }


def discover_result_files(home_repos_dir: Path, only: Optional[str]) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for repo_dir in sorted(home_repos_dir.iterdir()):
        if not repo_dir.is_dir() or not repo_dir.name.startswith("home-"):
            continue
        if only and repo_dir.name != only:
            continue
        results_dir = repo_dir / "results"
        if not results_dir.is_dir():
            continue
        for f in sorted(results_dir.iterdir()):
            if f.is_file() and f.suffix == ".md":
                out.append((repo_dir.name, f))
    return out


def run(
    *,
    home_repos_dir: Path,
    collection: str,
    dry_run: bool,
    only: Optional[str],
) -> dict:
    files = discover_result_files(home_repos_dir, only)
    upserted = 0
    upsert_errors = 0
    unparseable: list[str] = []
    per_repo: dict[str, int] = {}

    orig_collection = srs.COLLECTION
    srs.COLLECTION = collection
    try:
        for home_repo, path in files:
            parsed = parse_result_file(home_repo, path)
            if parsed["unparseable"]:
                unparseable.append(f"{home_repo}/{path.name}")

            if dry_run:
                upserted += 1
                per_repo[home_repo] = per_repo.get(home_repo, 0) + 1
                continue

            created_at = _commit_date(path.parent.parent, f"results/{path.name}")
            try:
                srs.upsert_result(
                    home_repo=parsed["home_repo"],
                    filename=parsed["filename"],
                    body=parsed["body"],
                    work_item=parsed["work_item"],
                    agent=parsed["agent"],
                    date=parsed["date"],
                    title=parsed["title"],
                    created_at=created_at,
                )
                upserted += 1
                per_repo[home_repo] = per_repo.get(home_repo, 0) + 1
            except Exception as exc:
                print(f"[ingest] upsert failed for {home_repo}/{path.name}: {exc}",
                      file=sys.stderr)
                upsert_errors += 1
    finally:
        srs.COLLECTION = orig_collection

    return {
        "scanned": len(files),
        "upserted": upserted,
        "upsert_errors": upsert_errors,
        "unparseable": unparseable,
        "per_repo": per_repo,
        "dry_run": dry_run,
        "collection": collection,
    }


def print_report(report: dict) -> None:
    mode = " (dry-run)" if report["dry_run"] else ""
    print(f"session_results_ingest -> collection={report['collection']}{mode}")
    print(f"  Scanned:  {report['scanned']} results/*.md files")
    print(f"  Upserted: {report['upserted']}")
    if report["upsert_errors"]:
        print(f"  Errors:   {report['upsert_errors']}")
    if report["per_repo"]:
        print()
        print("Per-repo counts:")
        for repo, n in sorted(report["per_repo"].items()):
            print(f"  {repo}: {n}")
    print()
    if report["unparseable"]:
        print(f"⚠️  {len(report['unparseable'])} file(s) surfaced as UNPARSEABLE "
              "(no project_ref from frontmatter or filename — indexed anyway, "
              "with work_item/project_ref null, per operator direction):")
        for name in report["unparseable"]:
            print(f"  - {name}")
    else:
        print("No unparseable files — every result carries a project_ref.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home-repos-dir", type=Path, default=DEFAULT_HOME_REPOS_DIR)
    ap.add_argument("--collection", default=srs.COLLECTION)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="Limit to one home-repo dir name (e.g. home-podzone-hephaestus)")
    args = ap.parse_args()

    report = run(
        home_repos_dir=args.home_repos_dir,
        collection=args.collection,
        dry_run=args.dry_run,
        only=args.only,
    )
    print_report(report)
    return 1 if report["upsert_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
