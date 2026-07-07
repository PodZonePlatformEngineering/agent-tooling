#!/usr/bin/env python3
"""rollup-report.py — project + programme rollup over a trailing time window.

PROJ-034/T-011. Reads the cloud Qdrant `sessions` collection via
`lib.sessions_reader`. Aggregates `work_items` + `projects` payload fields
populated by T-010, groups projects by programme via `task-naming.md`, writes
a markdown report and prints a digest.

Aggregation rule — shared attribution: a session referencing multiple projects
counts fully against each (documented in the report footer). Phase 3 can
refine if needed.

CLI:
  rollup-report.py                        # trailing 7-day rollup
  rollup-report.py --days 30
  rollup-report.py --programme-map PATH   # override task-naming.md location
  rollup-report.py --output PATH
  rollup-report.py --stdout-only
  rollup-report.py --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib.sessions_reader import scroll_all_sessions  # noqa: E402


DEFAULT_OUTPUT_DIR = (
    Path.home()
    / "workspace"
    / "podzoneTeam"
    / "team"
    / "hermes"
    / "outgoing"
    / "usage-reports"
)

DEFAULT_PROGRAMME_MAP = (
    Path.home()
    / "workspace"
    / "podzoneTeam"
    / "agenticflows"
    / "operations"
    / "task-naming.md"
)

SPARK_CHARS = "▁▂▃▄▅▆▇█"
UNMAPPED = "unmapped"


def _log(msg: str) -> None:
    print(f"[rollup-report] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Programme map


PROJECT_ROW_RE = re.compile(
    r"^\|\s*(PROJ-\d{3,})\s*\|\s*`?([a-z0-9\-]+)`?\s*\|\s*`?([a-z0-9\-]+)`?\s*\|",
    re.IGNORECASE,
)


def load_programme_map(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Parse task-naming.md.

    Returns (project_to_programme, project_to_shortform).
    Empty dicts (with a warning log) if the file is missing or has no rows.
    """
    if not path.exists():
        _log(f"programme map not found at {path} — all projects will be unmapped")
        return {}, {}

    project_to_programme: dict[str, str] = {}
    project_to_shortform: dict[str, str] = {}
    for line in path.read_text().splitlines():
        m = PROJECT_ROW_RE.match(line)
        if not m:
            continue
        proj, shortform, programme = m.group(1), m.group(2), m.group(3)
        project_to_programme[proj.upper()] = programme
        project_to_shortform[proj.upper()] = shortform
    return project_to_programme, project_to_shortform


# ---------------------------------------------------------------------------
# Aggregation


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _sum_total(t: dict) -> int:
    """Sum input + output + cache_creation tokens (excludes cache_read)."""
    return (
        int(t.get("input_tokens", 0))
        + int(t.get("output_tokens", 0))
        + int(t.get("cache_creation_input_tokens", 0))
    )


def _cache_hit_pct(t: dict) -> Optional[float]:
    read = int(t.get("cache_read_input_tokens", 0))
    inp = int(t.get("input_tokens", 0))
    creation = int(t.get("cache_creation_input_tokens", 0))
    denom = read + inp + creation
    if denom == 0:
        return None
    return 100.0 * read / denom


def _empty_bucket() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }


def _add_bucket(target: dict, src: dict) -> None:
    for k in target.keys():
        target[k] += int(src.get(k, 0))


def filter_window(
    payloads: Iterable[dict], days: int, now: Optional[datetime] = None
) -> tuple[list[dict], date, date]:
    now = now or datetime.now(timezone.utc)
    today = now.date()
    start = today - timedelta(days=days - 1)
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    out = []
    for pl in payloads:
        ts = _parse_iso(pl.get("last_message_ts"))
        if ts is None:
            continue
        if ts >= start_dt:
            out.append(pl)
    return out, start, today


def aggregate(
    payloads: list[dict],
    start: date,
    end: date,
    project_to_programme: dict[str, str],
) -> dict:
    """Build per-project, per-programme, per-day, per-work-item rollups."""
    per_project: dict[str, dict] = {}
    per_programme: dict[str, dict] = {}
    # per-day: {project: {date_iso: tokens}}
    per_day: dict[str, dict[str, int]] = {}
    per_work_item: dict[str, dict] = {}

    sessions_count = len(payloads)

    for pl in payloads:
        projects = pl.get("projects") or []
        work_items = pl.get("work_items") or []
        total = pl.get("total_tokens") or _empty_bucket()
        model_usage = pl.get("model_usage") or {}
        last_ts = _parse_iso(pl.get("last_message_ts"))

        # Per-project (shared attribution)
        for proj in projects:
            proj_u = proj.upper()
            entry = per_project.setdefault(
                proj_u,
                {
                    "sessions": 0,
                    "totals": _empty_bucket(),
                    "model_tokens": {},  # model -> tokens
                },
            )
            entry["sessions"] += 1
            _add_bucket(entry["totals"], total)
            for model, bucket in model_usage.items():
                key = model or "unknown"
                entry["model_tokens"][key] = entry["model_tokens"].get(
                    key, 0
                ) + _sum_total(bucket)

            if last_ts is not None:
                day_key = last_ts.date().isoformat()
                proj_days = per_day.setdefault(proj_u, {})
                proj_days[day_key] = proj_days.get(day_key, 0) + _sum_total(total)

        # Per-work-item (shared attribution)
        for wi in work_items:
            wi_u = wi.upper()
            entry = per_work_item.setdefault(
                wi_u, {"sessions": 0, "totals": _empty_bucket(), "model_tokens": {}}
            )
            entry["sessions"] += 1
            _add_bucket(entry["totals"], total)
            for model, bucket in model_usage.items():
                key = model or "unknown"
                entry["model_tokens"][key] = entry["model_tokens"].get(
                    key, 0
                ) + _sum_total(bucket)

    # Per-programme — sum from per-project (shared-attribution caveat applies)
    for proj_u, entry in per_project.items():
        programme = project_to_programme.get(proj_u, UNMAPPED)
        prog_entry = per_programme.setdefault(
            programme,
            {"projects": set(), "sessions": 0, "totals": _empty_bucket()},
        )
        prog_entry["projects"].add(proj_u)
        prog_entry["sessions"] += entry["sessions"]
        _add_bucket(prog_entry["totals"], entry["totals"])

    return {
        "per_project": per_project,
        "per_programme": per_programme,
        "per_day": per_day,
        "per_work_item": per_work_item,
        "session_count": sessions_count,
        "start": start,
        "end": end,
    }


# ---------------------------------------------------------------------------
# Rendering


def _fmt_m(n: int) -> str:
    return f"{n / 1_000_000:.1f}"


def _spark(values: list[int]) -> str:
    if not values or max(values) == 0:
        return " " * len(values)
    mx = max(values)
    out = []
    for v in values:
        if v == 0:
            out.append(" ")
            continue
        idx = int(round((v / mx) * (len(SPARK_CHARS) - 1)))
        out.append(SPARK_CHARS[idx])
    return "".join(out)


def _day_labels(start: date, end: date) -> list[str]:
    days = (end - start).days + 1
    return ["MTWTFSS"[(start + timedelta(days=i)).weekday()] for i in range(days)]


def _short_model(model: str) -> str:
    return model.replace("claude-", "")


def _top_model(model_tokens: dict[str, int]) -> str:
    if not model_tokens:
        return "—"
    name, _ = max(model_tokens.items(), key=lambda kv: kv[1])
    return _short_model(name)


def _top_work_item_for_project(
    proj_u: str, per_work_item: dict[str, dict]
) -> tuple[str, int]:
    """Return (work_item_id, tokens) of the top work item for this project."""
    matches = [
        (wi, e) for wi, e in per_work_item.items() if wi.startswith(proj_u + "/")
    ]
    if not matches:
        return ("—", 0)
    wi, e = max(matches, key=lambda x: _sum_total(x[1]["totals"]))
    return (wi, _sum_total(e["totals"]))


def render_markdown(
    agg: dict,
    days: int,
    project_to_programme: dict[str, str],
    map_path: Path,
    map_present: bool,
) -> str:
    start: date = agg["start"]
    end: date = agg["end"]

    lines: list[str] = []
    lines.append(f"# Project Rollup — {end.isoformat()}")
    lines.append("")
    lines.append(
        f"## Window: last {days} days ({start.isoformat()} → {end.isoformat()})"
    )
    lines.append("")
    lines.append(f"Sessions in window: {agg['session_count']}")
    lines.append("")

    if not map_present:
        lines.append(
            f"> ⚠️  Programme map not found at `{map_path}` — all projects "
            f"grouped under `{UNMAPPED}`."
        )
        lines.append("")

    # Per-project
    lines.append("## Per-project")
    lines.append("")
    lines.append(
        "| Project | Sessions | Tokens (M) | Cache hit % | Top model | Top work_item |"
    )
    lines.append("|---|---|---|---|---|---|")
    sorted_projects = sorted(
        agg["per_project"].items(),
        key=lambda kv: -_sum_total(kv[1]["totals"]),
    )
    for proj_u, e in sorted_projects:
        tokens_m = _fmt_m(_sum_total(e["totals"]))
        hit = _cache_hit_pct(e["totals"])
        hit_s = f"{hit:.0f}%" if hit is not None else "n/a"
        top_m = _top_model(e["model_tokens"])
        top_wi, top_wi_tokens = _top_work_item_for_project(proj_u, agg["per_work_item"])
        top_wi_s = (
            f"{top_wi} ({_fmt_m(top_wi_tokens)}M)" if top_wi != "—" else "—"
        )
        lines.append(
            f"| {proj_u} | {e['sessions']} | {tokens_m} | {hit_s} | {top_m} | {top_wi_s} |"
        )
    lines.append("")

    # Per-programme
    lines.append("## Per-programme")
    lines.append("")
    lines.append("| Programme | Projects | Sessions | Tokens (M) |")
    lines.append("|---|---|---|---|")
    sorted_programmes = sorted(
        agg["per_programme"].items(),
        key=lambda kv: -_sum_total(kv[1]["totals"]),
    )
    for prog, e in sorted_programmes:
        tokens_m = _fmt_m(_sum_total(e["totals"]))
        projs = ", ".join(sorted(e["projects"]))
        lines.append(f"| {prog} | {projs} | {e['sessions']} | {tokens_m} |")
    lines.append("")

    # Daily sparkline — top 5 projects
    top5 = [p for p, _ in sorted_projects[:5]]
    if top5:
        lines.append("## Daily sparkline — top 5 projects (M tokens)")
        lines.append("")
        lines.append("```")
        labels = _day_labels(start, end)
        widest = max(len(p) for p in top5)
        widest = max(widest, len("Project"))
        header_pad = " " * (widest + 1)
        if days <= 14:
            lines.append(header_pad + "    ".join(labels) + "    total")
        else:
            lines.append(
                header_pad + "(" + start.isoformat() + " → " + end.isoformat() + ")"
            )
        day_count = (end - start).days + 1
        for proj_u in top5:
            day_map = agg["per_day"].get(proj_u, {})
            values = [
                int(day_map.get((start + timedelta(days=i)).isoformat(), 0))
                for i in range(day_count)
            ]
            spark = _spark(values)
            if days <= 14:
                spaced = "    ".join(spark)
            else:
                spaced = spark
            total = _fmt_m(sum(values))
            lines.append(f"{proj_u.ljust(widest)} {spaced}   {total}M")
        lines.append("```")
        lines.append("")

    # Top 10 work_items
    lines.append("## Top 10 work_items by token volume")
    lines.append("")
    wi_ranked = sorted(
        agg["per_work_item"].items(),
        key=lambda kv: (-_sum_total(kv[1]["totals"]), kv[0]),
    )[:10]
    if not wi_ranked:
        lines.append("_None._")
    else:
        for i, (wi, e) in enumerate(wi_ranked, 1):
            tokens_m = _fmt_m(_sum_total(e["totals"]))
            top_m = _top_model(e["model_tokens"])
            lines.append(
                f"{i}. {wi} — {tokens_m}M tokens · {e['sessions']} session(s) · {top_m} dominant"
            )
    lines.append("")

    lines.append("---")
    lines.append(
        f"*Generated by `agent-tooling/tools/rollup-report.py` on "
        f"{datetime.now(timezone.utc).isoformat()}. "
        f"Attribution-method: shared-counting (multi-project sessions count fully "
        f"against each project; programme totals may inflate where sessions span "
        f"multiple programmes).*"
    )
    lines.append("")
    return "\n".join(lines)


def render_digest(
    agg: dict, days: int, report_path: Optional[Path]
) -> str:
    end: date = agg["end"]
    n_projects = len(agg["per_project"])
    unmapped_count = sum(
        1 for p in agg["per_project"] if p not in {k for k in agg["per_project"]}
    )
    # Compute unmapped via programme grouping
    mapped_count = sum(
        len(e["projects"]) for k, e in agg["per_programme"].items() if k != UNMAPPED
    )
    unmapped_count = n_projects - mapped_count
    n_programmes = len(agg["per_programme"])

    top_project = "—"
    if agg["per_project"]:
        name, entry = max(
            agg["per_project"].items(),
            key=lambda kv: _sum_total(kv[1]["totals"]),
        )
        top_project = (
            f"{name} ({_fmt_m(_sum_total(entry['totals']))}M tokens, "
            f"{entry['sessions']} sessions)"
        )

    top_programme = "—"
    if agg["per_programme"]:
        name, entry = max(
            agg["per_programme"].items(),
            key=lambda kv: _sum_total(kv[1]["totals"]),
        )
        top_programme = (
            f"{name} ({_fmt_m(_sum_total(entry['totals']))}M tokens, "
            f"{entry['sessions']} sessions)"
        )

    top_wi = "—"
    if agg["per_work_item"]:
        wi, entry = max(
            agg["per_work_item"].items(),
            key=lambda kv: _sum_total(kv[1]["totals"]),
        )
        top_wi = f"{wi} ({_fmt_m(_sum_total(entry['totals']))}M tokens)"

    lines = [
        f"Project Rollup — {end.isoformat()} — last {days} days",
        f"  Projects: {n_projects} ({mapped_count} mapped, {unmapped_count} unmapped) · Programmes: {n_programmes}",
        f"  Top project:   {top_project}",
        f"  Top programme: {top_programme}",
        f"  Top work_item: {top_wi}",
    ]
    if report_path is not None:
        lines.append(f"  Report: {report_path}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point


def run(
    days: int = 7,
    output: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    programme_map: Optional[Path] = None,
    stdout_only: bool = False,
    dry_run: bool = False,
    payloads: Optional[list[dict]] = None,
) -> dict:
    """Library entry — used by tests with `payloads=` injected.

    Returns {"digest", "markdown", "report_path", "agg"}.
    """
    map_path = programme_map or DEFAULT_PROGRAMME_MAP
    project_to_programme, _ = load_programme_map(map_path)
    map_present = map_path.exists()

    if payloads is None:
        payloads = list(scroll_all_sessions())

    filtered, start, end = filter_window(payloads, days=days)
    agg = aggregate(filtered, start, end, project_to_programme)
    markdown = render_markdown(agg, days, project_to_programme, map_path, map_present)

    report_path: Optional[Path] = None
    if not stdout_only and not dry_run:
        if output is not None:
            report_path = output
        else:
            target_dir = out_dir if out_dir is not None else DEFAULT_OUTPUT_DIR
            target_dir.mkdir(parents=True, exist_ok=True)
            report_path = target_dir / f"{end.isoformat()}-project-rollup.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(markdown)

    digest = render_digest(agg, days, report_path)

    return {
        "digest": digest,
        "markdown": markdown,
        "report_path": report_path,
        "agg": agg,
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7, help="Window in days (default: 7)")
    ap.add_argument("--output", type=Path, help="Override output markdown path")
    ap.add_argument(
        "--out-dir",
        type=Path,
        dest="out_dir",
        help=(
            "Directory to write the report into (filename derived as usual). "
            "Default is the podzone-internal team/hermes/outgoing/usage-reports/; "
            "external adopters should pass --out-dir."
        ),
    )
    ap.add_argument(
        "--programme-map", type=Path, help="Override task-naming.md location"
    )
    ap.add_argument("--stdout-only", action="store_true", help="Do not write a file")
    ap.add_argument(
        "--dry-run", action="store_true", help="Read but no writes (no file)"
    )
    args = ap.parse_args(argv)

    if args.days < 1:
        print("rollup-report: --days must be ≥ 1", file=sys.stderr)
        return 2

    try:
        result = run(
            days=args.days,
            output=args.output,
            out_dir=args.out_dir,
            programme_map=args.programme_map,
            stdout_only=args.stdout_only,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"rollup-report: failed: {exc}", file=sys.stderr)
        return 1

    print(result["digest"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
