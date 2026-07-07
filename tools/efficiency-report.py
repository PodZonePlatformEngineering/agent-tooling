#!/usr/bin/env python3
"""efficiency-report.py — model × {project|work_item|agent} cross-tabs.

PROJ-034/T-012. Reads the cloud Qdrant `sessions` collection via
`lib.sessions_reader`. Tabulates token volume per (model, dimension) cell,
plus cache-hit % per cell, plus a small mechanical observations block.

Shared-attribution rule (T-011): a session referencing multiple
projects/work_items contributes its tokens to each dimension value.

Agent attribution: sessions whose `agent` field is null AND whose workspace
has multiple candidate agents are excluded from the model × agent cross-tab;
the exclusion count is reported in the footer.

CLI:
  efficiency-report.py                          # trailing 30-day cross-tabs
  efficiency-report.py --days 14
  efficiency-report.py --dimensions model,project
  efficiency-report.py --output PATH
  efficiency-report.py --stdout-only
  efficiency-report.py --dry-run
"""

from __future__ import annotations

import argparse
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

ALL_DIMENSIONS = ("project", "work_item", "agent")
DEFAULT_DIMENSIONS = ("project", "work_item", "agent")
SYNTHETIC = "<synthetic>"
TOP_N_WORK_ITEMS = 10


def _log(msg: str) -> None:
    print(f"[efficiency-report] {msg}", file=sys.stderr)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _sum_total(t: dict) -> int:
    return (
        int(t.get("input_tokens", 0))
        + int(t.get("output_tokens", 0))
        + int(t.get("cache_creation_input_tokens", 0))
    )


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


def _cache_hit_pct(t: dict) -> Optional[float]:
    read = int(t.get("cache_read_input_tokens", 0))
    inp = int(t.get("input_tokens", 0))
    creation = int(t.get("cache_creation_input_tokens", 0))
    denom = read + inp + creation
    if denom == 0:
        return None
    return 100.0 * read / denom


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


def _workspace_agents(payloads: list[dict]) -> dict[str, set[str]]:
    """Map workspace → set of non-null agent ids seen on its sessions."""
    out: dict[str, set[str]] = {}
    for pl in payloads:
        ws = pl.get("workspace") or "unknown"
        ag = pl.get("agent")
        if ag:
            out.setdefault(ws, set()).add(ag)
    return out


def cross_tab(
    payloads: list[dict],
    dimension: str,
    *,
    top_work_items: Optional[list[str]] = None,
    workspace_agents: Optional[dict[str, set[str]]] = None,
) -> dict:
    """Build {model: {dim_value: bucket}} aggregate cells.

    dimension ∈ {'project', 'work_item', 'agent'}. Returns:
        {
          "cells": {model: {dim_val: bucket}},  # bucket has full token fields
          "row_totals": {model: bucket},
          "col_totals": {dim_val: bucket},
          "columns": [dim_val, ...],            # ordered, top-N for work_item
          "excluded": int,                      # agent dimension only
        }
    """
    workspace_agents = workspace_agents or {}
    cells: dict[str, dict[str, dict]] = {}
    col_totals: dict[str, dict] = {}
    excluded = 0

    # Pre-compute top work_items if needed
    if dimension == "work_item" and top_work_items is None:
        wi_totals: dict[str, int] = {}
        for pl in payloads:
            wis = pl.get("work_items") or []
            total = _sum_total(pl.get("total_tokens") or {})
            for wi in wis:
                wi_totals[wi.upper()] = wi_totals.get(wi.upper(), 0) + total
        top_work_items = [
            wi for wi, _ in sorted(wi_totals.items(), key=lambda kv: (-kv[1], kv[0]))
        ][:TOP_N_WORK_ITEMS]
    top_work_items = top_work_items or []

    for pl in payloads:
        model_usage = pl.get("model_usage") or {}

        # Determine dimension values for this session
        if dimension == "project":
            dim_values = [p.upper() for p in (pl.get("projects") or [])]
            if not dim_values:
                dim_values = ["unmapped"]
        elif dimension == "work_item":
            wis = [w.upper() for w in (pl.get("work_items") or [])]
            dim_values = [w for w in wis if w in top_work_items]
            if not dim_values:
                continue  # session contributed nothing to the top-N columns
        elif dimension == "agent":
            ag = pl.get("agent")
            ws = pl.get("workspace") or "unknown"
            if ag is None and len(workspace_agents.get(ws, set())) >= 2:
                excluded += 1
                continue
            dim_values = [ag or "unknown"]
        else:
            raise ValueError(f"unknown dimension: {dimension}")

        # Render synthetic model when none recorded
        iterator = (
            list(model_usage.items()) if model_usage else [(SYNTHETIC, _empty_bucket())]
        )
        for model, bucket in iterator:
            model_key = model or "unknown"
            for dv in dim_values:
                row = cells.setdefault(model_key, {})
                cell = row.setdefault(dv, _empty_bucket())
                _add_bucket(cell, bucket)
                col_total = col_totals.setdefault(dv, _empty_bucket())
                _add_bucket(col_total, bucket)

    # Row totals
    row_totals: dict[str, dict] = {}
    for model, row in cells.items():
        tot = _empty_bucket()
        for dv, cell in row.items():
            _add_bucket(tot, cell)
        row_totals[model] = tot

    # Columns ordering
    if dimension == "work_item":
        columns = [w for w in top_work_items if w in col_totals]
    else:
        columns = sorted(
            col_totals.keys(), key=lambda c: (-_sum_total(col_totals[c]), c)
        )

    return {
        "cells": cells,
        "row_totals": row_totals,
        "col_totals": col_totals,
        "columns": columns,
        "excluded": excluded,
    }


# ---------------------------------------------------------------------------
# Rendering


def _fmt_m(n: int) -> str:
    return f"{n / 1_000_000:.1f}"


def _short_model(model: str) -> str:
    if model == SYNTHETIC:
        return model
    return model.replace("claude-", "")


def render_cross_tab_table(ct: dict, title: str, columns_label: str) -> list[str]:
    """Render a token cross-tab as a markdown table (M tokens per cell)."""
    cells = ct["cells"]
    columns = ct["columns"]
    row_totals = ct["row_totals"]
    col_totals = ct["col_totals"]

    lines = [f"## {title}", ""]
    if not columns or not cells:
        lines.append("_No data in window._")
        lines.append("")
        return lines

    header = "| Model | " + " | ".join(columns) + " | total |"
    sep = "|---|" + "|".join(["---"] * (len(columns) + 1)) + "|"
    lines.append(header)
    lines.append(sep)

    # Sort models by row total tokens descending
    model_order = sorted(
        cells.keys(), key=lambda m: -_sum_total(row_totals.get(m, _empty_bucket()))
    )
    for model in model_order:
        row = cells[model]
        cells_str = []
        for col in columns:
            tokens = _sum_total(row.get(col, _empty_bucket()))
            cells_str.append(_fmt_m(tokens))
        row_total = _sum_total(row_totals.get(model, _empty_bucket()))
        lines.append(
            f"| {_short_model(model)} | " + " | ".join(cells_str) + f" | {_fmt_m(row_total)} |"
        )
    # Column totals row
    col_totals_str = [_fmt_m(_sum_total(col_totals.get(c, _empty_bucket()))) for c in columns]
    grand = sum(_sum_total(b) for b in row_totals.values())
    lines.append("| **total** | " + " | ".join(col_totals_str) + f" | {_fmt_m(grand)} |")
    lines.append("")
    return lines


def render_cache_hit_table(ct: dict, title: str) -> list[str]:
    cells = ct["cells"]
    columns = ct["columns"]
    row_totals = ct["row_totals"]

    lines = [f"## {title}", ""]
    if not columns or not cells:
        lines.append("_No data in window._")
        lines.append("")
        return lines

    header = "| Model | " + " | ".join(columns) + " |"
    sep = "|---|" + "|".join(["---"] * len(columns)) + "|"
    lines.append(header)
    lines.append(sep)
    model_order = sorted(
        cells.keys(), key=lambda m: -_sum_total(row_totals.get(m, _empty_bucket()))
    )
    for model in model_order:
        row = cells[model]
        cells_str = []
        for col in columns:
            cell = row.get(col, _empty_bucket())
            hit = _cache_hit_pct(cell)
            cells_str.append(f"{hit:.0f}%" if hit is not None else "n/a")
        lines.append(f"| {_short_model(model)} | " + " | ".join(cells_str) + " |")
    lines.append("")
    return lines


def render_observations(cross_tabs: dict) -> list[str]:
    """Mechanical observations: concentration + best/worst cache hit."""
    lines = ["## Observations", ""]
    flagged = False

    # Concentration: for each model in the project cross-tab, find the
    # column accounting for the highest share of that model's tokens.
    proj_ct = cross_tabs.get("project")
    if proj_ct and proj_ct["cells"]:
        for model, row in proj_ct["cells"].items():
            total = _sum_total(proj_ct["row_totals"].get(model, _empty_bucket()))
            if total == 0:
                continue
            top_col, top_cell = max(
                row.items(), key=lambda kv: _sum_total(kv[1])
            )
            share = _sum_total(top_cell) / total
            if share >= 0.5:
                lines.append(
                    f"- concentration: {_short_model(model)} → {top_col} "
                    f"({_fmt_m(_sum_total(top_cell))}M of {_fmt_m(total)}M = {share*100:.0f}%)"
                )
                flagged = True

    # Best cache hit (model × project)
    if proj_ct and proj_ct["cells"]:
        best = None  # (pct, model, col)
        for model, row in proj_ct["cells"].items():
            for col, cell in row.items():
                hit = _cache_hit_pct(cell)
                if hit is None:
                    continue
                if best is None or hit > best[0]:
                    best = (hit, model, col)
        if best is not None:
            lines.append(
                f"- highest cache hit: {_short_model(best[1])} × {best[2]} ({best[0]:.0f}%)"
            )
            flagged = True

    if not flagged:
        lines.append("_No observations — insufficient signal in window._")
    lines.append("")
    return lines


def render_markdown(
    payloads: list[dict],
    cross_tabs: dict,
    days: int,
    start: date,
    end: date,
    dimensions: tuple[str, ...],
) -> str:
    lines: list[str] = []
    lines.append(f"# Model Efficiency — {end.isoformat()}")
    lines.append("")
    lines.append(
        f"## Window: last {days} days ({start.isoformat()} → {end.isoformat()})"
    )
    lines.append("")
    lines.append(f"Sessions in window: {len(payloads)}")
    lines.append("")

    if "project" in dimensions:
        lines.extend(
            render_cross_tab_table(
                cross_tabs["project"],
                "Cross-tab: model × project (M tokens)",
                "project",
            )
        )
    if "work_item" in dimensions:
        lines.extend(
            render_cross_tab_table(
                cross_tabs["work_item"],
                f"Cross-tab: model × top-{TOP_N_WORK_ITEMS} work_items (M tokens)",
                "work_item",
            )
        )
    if "agent" in dimensions:
        lines.extend(
            render_cross_tab_table(
                cross_tabs["agent"],
                "Cross-tab: model × agent (M tokens)",
                "agent",
            )
        )
        excluded = cross_tabs["agent"]["excluded"]
        if excluded:
            lines.append(
                f"> _{excluded} session(s) excluded from agent cross-tab "
                f"(null agent in multi-agent workspace)._"
            )
            lines.append("")

    if "project" in dimensions:
        lines.extend(
            render_cache_hit_table(
                cross_tabs["project"], "Cache hit % per model × project"
            )
        )

    lines.extend(render_observations(cross_tabs))

    lines.append("---")
    lines.append(
        f"*Generated by `agent-tooling/tools/efficiency-report.py` on "
        f"{datetime.now(timezone.utc).isoformat()}. "
        f"Attribution-method: shared-counting (multi-{{project,work_item}} sessions "
        f"contribute to each value).*"
    )
    lines.append("")
    return "\n".join(lines)


def render_digest(
    payloads: list[dict],
    cross_tabs: dict,
    days: int,
    end: date,
    report_path: Optional[Path],
    dimensions: tuple[str, ...],
) -> str:
    # Build summary line
    models = set()
    for d in dimensions:
        models.update(cross_tabs[d]["cells"].keys())
    n_models = len(models)
    n_projects = len(cross_tabs.get("project", {}).get("col_totals", {})) if "project" in dimensions else 0
    n_agents = len(cross_tabs.get("agent", {}).get("col_totals", {})) if "agent" in dimensions else 0

    # Concentration line
    conc = "—"
    if "project" in dimensions and cross_tabs["project"]["cells"]:
        proj_ct = cross_tabs["project"]
        candidate = None  # (share, model, col)
        for model, row in proj_ct["cells"].items():
            total = _sum_total(proj_ct["row_totals"].get(model, _empty_bucket()))
            if total == 0:
                continue
            top_col, top_cell = max(row.items(), key=lambda kv: _sum_total(kv[1]))
            share = _sum_total(top_cell) / total
            if candidate is None or share > candidate[0]:
                candidate = (share, model, top_col)
        if candidate is not None:
            conc = f"{_short_model(candidate[1])} → {candidate[2]} ({candidate[0]*100:.0f}%)"

    # Highest cache hit
    cache_line = "—"
    if "project" in dimensions and cross_tabs["project"]["cells"]:
        best = None
        for model, row in cross_tabs["project"]["cells"].items():
            for col, cell in row.items():
                hit = _cache_hit_pct(cell)
                if hit is None:
                    continue
                if best is None or hit > best[0]:
                    best = (hit, model, col)
        if best is not None:
            cache_line = f"{_short_model(best[1])} × {best[2]} ({best[0]:.0f}%)"

    excluded = cross_tabs.get("agent", {}).get("excluded", 0) if "agent" in dimensions else 0

    lines = [
        f"Model Efficiency — {end.isoformat()} — last {days} days",
        f"  Models: {n_models} · Projects: {n_projects} · Agents: {n_agents}",
        f"  Concentration: {conc}",
        f"  Highest cache hit: {cache_line}",
        f"  Excluded from agent cross-tab: {excluded} session(s)",
    ]
    if report_path is not None:
        lines.append(f"  Report: {report_path}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point


def run(
    days: int = 30,
    dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS,
    output: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    stdout_only: bool = False,
    dry_run: bool = False,
    payloads: Optional[list[dict]] = None,
) -> dict:
    """Library entry — used by tests with `payloads=` injected.

    Returns {"digest", "markdown", "report_path", "cross_tabs", "start", "end"}.
    """
    if payloads is None:
        payloads = list(scroll_all_sessions())

    filtered, start, end = filter_window(payloads, days=days)
    ws_agents = _workspace_agents(filtered)

    cross_tabs: dict = {}
    if "project" in dimensions:
        cross_tabs["project"] = cross_tab(filtered, "project")
    if "work_item" in dimensions:
        cross_tabs["work_item"] = cross_tab(filtered, "work_item")
    if "agent" in dimensions:
        cross_tabs["agent"] = cross_tab(filtered, "agent", workspace_agents=ws_agents)

    markdown = render_markdown(filtered, cross_tabs, days, start, end, dimensions)

    report_path: Optional[Path] = None
    if not stdout_only and not dry_run:
        if output is not None:
            report_path = output
        else:
            target_dir = out_dir if out_dir is not None else DEFAULT_OUTPUT_DIR
            target_dir.mkdir(parents=True, exist_ok=True)
            report_path = target_dir / f"{end.isoformat()}-model-efficiency.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(markdown)

    digest = render_digest(
        filtered, cross_tabs, days, end, report_path, dimensions
    )

    return {
        "digest": digest,
        "markdown": markdown,
        "report_path": report_path,
        "cross_tabs": cross_tabs,
        "start": start,
        "end": end,
    }


def _parse_dimensions(value: str) -> tuple[str, ...]:
    parts = tuple(v.strip() for v in value.split(",") if v.strip())
    bad = [p for p in parts if p not in ALL_DIMENSIONS]
    if bad:
        raise argparse.ArgumentTypeError(
            f"unknown dimension(s): {bad}. Choices: {ALL_DIMENSIONS}"
        )
    return parts


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30, help="Window in days (default: 30)")
    ap.add_argument(
        "--dimensions",
        type=_parse_dimensions,
        default=DEFAULT_DIMENSIONS,
        help="Comma-separated subset of: project, work_item, agent (default: all)",
    )
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
    ap.add_argument("--stdout-only", action="store_true", help="Do not write a file")
    ap.add_argument(
        "--dry-run", action="store_true", help="Read but no writes (no file)"
    )
    args = ap.parse_args(argv)

    if args.days < 1:
        print("efficiency-report: --days must be ≥ 1", file=sys.stderr)
        return 2

    try:
        result = run(
            days=args.days,
            dimensions=args.dimensions,
            output=args.output,
            out_dir=args.out_dir,
            stdout_only=args.stdout_only,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"efficiency-report: failed: {exc}", file=sys.stderr)
        return 1

    print(result["digest"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
