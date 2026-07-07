#!/usr/bin/env python3
"""
usage-report.py — render a markdown usage summary from the cloud Qdrant
`sessions` collection.

PROJ-034/T-008 (Option C — standalone /usage-report skill).
PROJ-034/T-016 zombie cleanup folded in as Step 0.

Output:
  - markdown file at team/hermes/outgoing/usage-reports/{today}-usage-summary.md
    (overwritten if same-day file already exists)
  - 4-6 line digest to stdout

Phase 1: usage data only — no dollar values. See PROJ-034 proposal.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib import qdrant_http  # noqa: E402

CLOUD_QDRANT_URL = qdrant_http.CLOUD_QDRANT_URL
SESSIONS_COLLECTION = "sessions"
SCROLL_LIMIT = 256

DEFAULT_OUTPUT_DIR = (
    Path.home() / "workspace" / "podzoneTeam" / "team" / "hermes" / "outgoing" / "usage-reports"
)

SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _log(msg: str) -> None:
    print(f"[usage-report] {msg}", file=sys.stderr)


def scroll_all(
    qdrant_url: str = CLOUD_QDRANT_URL,
    collection: str = SESSIONS_COLLECTION,
    timeout: float = 30.0,
) -> list[dict]:
    """Scroll the whole collection. Returns a list of {id, payload} dicts.

    Raises ``QdrantAuthError`` (loud) if no API key is available, rather than
    silently returning an empty report.
    """
    out: list[dict] = []
    offset = None
    while True:
        body: dict = {"limit": SCROLL_LIMIT, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        result = qdrant_http.scroll(
            collection=collection, body=body, qdrant_url=qdrant_url, timeout=timeout
        ).get("result", {})
        points = result.get("points") or []
        for p in points:
            out.append({"id": p.get("id"), "payload": p.get("payload") or {}})
        offset = result.get("next_page_offset")
        if not offset:
            break
    return out


def delete_points(
    ids: list,
    qdrant_url: str = CLOUD_QDRANT_URL,
    collection: str = SESSIONS_COLLECTION,
    timeout: float = 15.0,
) -> bool:
    if not ids:
        return True
    try:
        qdrant_http.delete_points(
            ids, collection=collection, qdrant_url=qdrant_url, timeout=timeout
        )
        return True
    except qdrant_http.QdrantError as exc:
        _log(f"delete failed: {exc}")
        return False


def cleanup_zombies(points: list[dict], dry_run: bool = False) -> dict:
    """Identify points missing `data_source` and delete them.

    Returns {"pre": int, "post": int, "removed": int, "ids": list}.
    `pre`/`post` are the same total here (caller supplies the scroll result);
    `removed` is the number we asked Qdrant to delete.
    """
    zombie_ids = [
        p["id"] for p in points
        if not (p.get("payload") or {}).get("data_source")
    ]
    if not zombie_ids:
        return {"pre": len(points), "post": len(points), "removed": 0, "ids": []}

    if dry_run:
        return {
            "pre": len(points),
            "post": len(points),
            "removed": 0,
            "ids": zombie_ids,
            "dry_run": True,
        }

    ok = delete_points(zombie_ids)
    removed = len(zombie_ids) if ok else 0
    return {
        "pre": len(points),
        "post": len(points) - removed,
        "removed": removed,
        "ids": zombie_ids,
    }


# ---------------------------------------------------------------------------
# Aggregations


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def filter_window(points: list[dict], days: int, now: Optional[datetime] = None) -> tuple[list[dict], date, date]:
    """Filter points where last_message_ts is within the trailing `days` window.

    Window is inclusive of today. `days=7` → today + 6 prior days.
    Returns (filtered, start_date, end_date).
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()
    start = today - timedelta(days=days - 1)
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)

    out = []
    for p in points:
        payload = p.get("payload") or {}
        ts = _parse_iso(payload.get("last_message_ts"))
        if ts is None:
            continue
        if ts >= start_dt:
            out.append(p)
    return out, start, today


def _sum_total(t: dict) -> int:
    """Sum input + output + cache_creation tokens (excludes cache_read)."""
    return int(t.get("input_tokens", 0)) + int(t.get("output_tokens", 0)) + int(t.get("cache_creation_input_tokens", 0))


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
        "iterations": 0,
    }


def _add_bucket(target: dict, src: dict) -> None:
    for k in target.keys():
        target[k] += int(src.get(k, 0))


def aggregate(points: list[dict], start: date, end: date) -> dict:
    """Compute per-workspace, per-model, per-day, outliers, flags."""
    per_workspace: dict[str, dict] = {}
    per_model: dict[str, dict] = {}
    workspace_agents: dict[str, set] = {}
    # per-day: {workspace: {date_iso: tokens}}
    per_day: dict[str, dict[str, int]] = {}

    # Track per-session totals for outlier ranking
    sessions_summary: list[dict] = []

    in_progress_stale = 0
    high_iteration = 0
    multi_agent = 0

    now = datetime.now(timezone.utc)

    for p in points:
        pl = p.get("payload") or {}
        ws = pl.get("workspace") or "unknown"
        agent = pl.get("agent")
        total = pl.get("total_tokens") or _empty_bucket()
        msg_counts = pl.get("message_counts") or {}
        assistant_count = int(msg_counts.get("assistant", 0))

        ws_entry = per_workspace.setdefault(ws, {"sessions": 0, "totals": _empty_bucket()})
        ws_entry["sessions"] += 1
        _add_bucket(ws_entry["totals"], total)

        if agent:
            workspace_agents.setdefault(ws, set()).add(agent)

        model_usage = pl.get("model_usage") or {}
        if not model_usage:
            # Empty session — count it under <synthetic> as zero
            m_entry = per_model.setdefault("<synthetic>", {"sessions": 0, "totals": _empty_bucket()})
            m_entry["sessions"] += 1
        else:
            for model, bucket in model_usage.items():
                key = model or "unknown"
                m_entry = per_model.setdefault(key, {"sessions": 0, "totals": _empty_bucket()})
                m_entry["sessions"] += 1
                _add_bucket(m_entry["totals"], bucket)

        last_ts = _parse_iso(pl.get("last_message_ts"))
        if last_ts is not None:
            day_key = last_ts.date().isoformat()
            ws_days = per_day.setdefault(ws, {})
            ws_days[day_key] = ws_days.get(day_key, 0) + _sum_total(total)

        # Flags
        status = pl.get("status")
        if status == "in_progress":
            heartbeat = _parse_iso(pl.get("last_heartbeat_ts"))
            if heartbeat and (now - heartbeat).total_seconds() > 6 * 3600:
                in_progress_stale += 1
        if assistant_count >= 100:
            high_iteration += 1

        sessions_summary.append({
            "session_id": pl.get("session_id"),
            "workspace": ws,
            "agent": agent,
            "tokens": _sum_total(total),
            "iterations": assistant_count,
        })

    for ws, agents in workspace_agents.items():
        if len(agents) >= 2:
            multi_agent += 1

    # Outliers — top 5 by tokens OR by iterations (deduplicate)
    by_tokens = sorted(sessions_summary, key=lambda s: s["tokens"], reverse=True)[:5]
    by_iter = [s for s in sessions_summary if s["iterations"] >= 100]
    by_iter.sort(key=lambda s: s["iterations"], reverse=True)
    by_iter = by_iter[:5]
    outliers_seen: set = set()
    outliers: list[dict] = []
    for s in by_tokens + by_iter:
        sid = s["session_id"]
        if sid in outliers_seen:
            continue
        outliers_seen.add(sid)
        outliers.append(s)
        if len(outliers) >= 5:
            break

    return {
        "per_workspace": per_workspace,
        "per_model": per_model,
        "per_day": per_day,
        "workspace_agents": workspace_agents,
        "outliers": outliers,
        "flags": {
            "in_progress_stale": in_progress_stale,
            "high_iteration": high_iteration,
            "multi_agent_workspaces": multi_agent,
        },
        "session_count": len(points),
        "start": start,
        "end": end,
    }


# ---------------------------------------------------------------------------
# Rendering


def _fmt_m_tokens(n: int) -> str:
    return f"{n / 1_000_000:.1f}"


def _spark(values: list[int]) -> str:
    """Map a list of ints to unicode block characters. Empty days render as a space."""
    if not values or max(values) == 0:
        return " " * len(values)
    mx = max(values)
    out = []
    for v in values:
        if v == 0:
            out.append(" ")
            continue
        # scale 1..len(SPARK_CHARS); ensure smallest non-zero hits index 0
        idx = int(round((v / mx) * (len(SPARK_CHARS) - 1)))
        out.append(SPARK_CHARS[idx])
    return "".join(out)


def _day_labels(start: date, end: date) -> list[str]:
    days = (end - start).days + 1
    labels = []
    for i in range(days):
        d = start + timedelta(days=i)
        labels.append("MTWTFSS"[d.weekday()])
    return labels


def render_markdown(agg: dict, cleanup: dict, days: int) -> str:
    start: date = agg["start"]
    end: date = agg["end"]

    lines: list[str] = []
    lines.append(f"# Usage Summary — {end.isoformat()}")
    lines.append("")
    lines.append(f"## Window: last {days} days ({start.isoformat()} → {end.isoformat()})")
    lines.append("")
    lines.append(f"Sessions in window: {agg['session_count']}")
    lines.append("")

    # Per-workspace
    lines.append("### Per-workspace")
    lines.append("")
    lines.append("| Workspace | Sessions | Tokens (M) | Cache hit % | Notable |")
    lines.append("|---|---|---|---|---|")
    for ws, e in sorted(agg["per_workspace"].items(), key=lambda kv: -_sum_total(kv[1]["totals"])):
        tokens_m = _fmt_m_tokens(_sum_total(e["totals"]))
        hit = _cache_hit_pct(e["totals"])
        hit_s = f"{hit:.0f}%" if hit is not None else "n/a"
        agents = agg["workspace_agents"].get(ws, set())
        note = "—"
        if len(agents) >= 2:
            note = f"multi-agent ({','.join(sorted(agents))})"
        lines.append(f"| {ws} | {e['sessions']} | {tokens_m} | {hit_s} | {note} |")
    lines.append("")

    # Per-model
    lines.append("### Per-model")
    lines.append("")
    lines.append("| Model | Sessions | Tokens (M) | Cache hit % |")
    lines.append("|---|---|---|---|")
    for model, e in sorted(agg["per_model"].items(), key=lambda kv: -_sum_total(kv[1]["totals"])):
        tokens_m = _fmt_m_tokens(_sum_total(e["totals"]))
        hit = _cache_hit_pct(e["totals"])
        hit_s = f"{hit:.0f}%" if hit is not None else "n/a"
        lines.append(f"| {model} | {e['sessions']} | {tokens_m} | {hit_s} |")
    lines.append("")

    # Sparkline
    lines.append(f"### Daily sparkline (per-workspace, M tokens)")
    lines.append("")
    labels = _day_labels(start, end)
    lines.append("```")
    widest = max((len(w) for w in agg["per_workspace"]), default=10)
    widest = max(widest, len("workspace"))
    header_pad = " " * (widest + 1)
    if days <= 14:
        lines.append(header_pad + "    ".join(labels) + "    total")
    else:
        lines.append(header_pad + "(" + start.isoformat() + " → " + end.isoformat() + ")")
    day_count = (end - start).days + 1
    for ws, _ in sorted(
        agg["per_workspace"].items(),
        key=lambda kv: -_sum_total(kv[1]["totals"]),
    ):
        day_map = agg["per_day"].get(ws, {})
        values = []
        for i in range(day_count):
            d = (start + timedelta(days=i)).isoformat()
            values.append(int(day_map.get(d, 0)))
        spark = _spark(values)
        # space the bars to align with " " * 4 separation when day labels shown
        if days <= 14:
            spaced = "    ".join(spark)
        else:
            spaced = spark
        total = _fmt_m_tokens(sum(values))
        lines.append(f"{ws.ljust(widest)} {spaced}   {total}M")
    lines.append("```")
    lines.append("")

    # Outliers
    lines.append("### Outliers (top 5)")
    lines.append("")
    if not agg["outliers"]:
        lines.append("_None._")
    else:
        for i, s in enumerate(agg["outliers"], 1):
            sid_short = (s.get("session_id") or "")[:8]
            tokens_m = _fmt_m_tokens(s["tokens"])
            agent_part = f" / {s['agent']}" if s.get("agent") else ""
            lines.append(
                f"{i}. `{sid_short}` — {s['workspace']}{agent_part} — "
                f"{s['iterations']} iter · {tokens_m}M tokens"
            )
    lines.append("")

    # Flags
    f = agg["flags"]
    lines.append("### Flags")
    lines.append("")
    lines.append(f"- Multi-agent workspaces (attribution best-effort): {f['multi_agent_workspaces']}")
    lines.append(f"- Still `in_progress` with heartbeat > 6h ago: {f['in_progress_stale']}")
    lines.append(f"- High-iteration sessions (≥ 100): {f['high_iteration']}")
    lines.append("")

    lines.append("---")
    lines.append(
        f"*Generated by `agent-tooling/tools/usage-report.py` on "
        f"{datetime.now(timezone.utc).isoformat()}. "
        f"Zombie cleanup: removed {cleanup.get('removed', 0)} legacy heartbeat rows.*"
    )
    lines.append("")
    return "\n".join(lines)


def render_digest(agg: dict, cleanup: dict, days: int, report_path: Optional[Path]) -> str:
    end: date = agg["end"]

    grand_totals = _empty_bucket()
    for e in agg["per_workspace"].values():
        _add_bucket(grand_totals, e["totals"])
    total_tokens = _sum_total(grand_totals)
    cache_hit = _cache_hit_pct(grand_totals)
    cache_s = f"{cache_hit:.0f}%" if cache_hit is not None else "n/a"

    top_ws = "—"
    if agg["per_workspace"]:
        ws_name, ws_entry = max(
            agg["per_workspace"].items(),
            key=lambda kv: _sum_total(kv[1]["totals"]),
        )
        top_ws = f"{ws_name} ({_fmt_m_tokens(_sum_total(ws_entry['totals']))}M)"

    top_model = "—"
    if agg["per_model"]:
        m_name, m_entry = max(
            agg["per_model"].items(),
            key=lambda kv: _sum_total(kv[1]["totals"]),
        )
        m_short = m_name.replace("claude-", "")
        top_model = f"{m_short} ({_fmt_m_tokens(_sum_total(m_entry['totals']))}M)"

    flags = agg["flags"]
    flags_total = flags["in_progress_stale"] + flags["high_iteration"] + flags["multi_agent_workspaces"]

    lines = [
        f"Usage Summary — {end.isoformat()} — last {days} days",
        f"  Sessions: {agg['session_count']} · Tokens: {_fmt_m_tokens(total_tokens)}M · Cache hit: {cache_s}",
        f"  Top workspace: {top_ws}",
        f"  Top model: {top_model}",
        f"  Outliers: {len(agg['outliers'])} · Flags: {flags_total} ({flags['multi_agent_workspaces']} multi-agent)",
    ]
    if report_path is not None:
        lines.append(f"  Report: {report_path}")
    lines.append(
        f"  Zombie cleanup: removed {cleanup.get('removed', 0)} legacy rows"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point


def run(
    days: int = 7,
    output: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    stdout_only: bool = False,
    no_cleanup: bool = False,
    dry_run: bool = False,
    points: Optional[list[dict]] = None,
) -> dict:
    """Library entry — used by tests with `points=` injected.

    Returns {"digest": str, "markdown": str, "report_path": Path|None,
             "cleanup": dict, "agg": dict}.
    """
    if points is None:
        points = scroll_all()

    if no_cleanup or dry_run:
        cleanup = {"pre": len(points), "post": len(points), "removed": 0, "ids": []}
    else:
        cleanup = cleanup_zombies(points)
        # Remove deleted points from the in-memory list before aggregating
        if cleanup["ids"]:
            removed_set = set(cleanup["ids"])
            points = [p for p in points if p["id"] not in removed_set]

    filtered, start, end = filter_window(points, days=days)
    agg = aggregate(filtered, start, end)
    markdown = render_markdown(agg, cleanup, days)

    report_path: Optional[Path] = None
    if not stdout_only and not dry_run:
        if output is not None:
            report_path = output
        else:
            target_dir = out_dir if out_dir is not None else DEFAULT_OUTPUT_DIR
            target_dir.mkdir(parents=True, exist_ok=True)
            report_path = target_dir / f"{end.isoformat()}-usage-summary.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(markdown)

    digest = render_digest(agg, cleanup, days, report_path)

    return {
        "digest": digest,
        "markdown": markdown,
        "report_path": report_path,
        "cleanup": cleanup,
        "agg": agg,
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7, help="Window in days (default: 7)")
    ap.add_argument("--output", type=Path, help="Override the output markdown path")
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
    ap.add_argument("--no-cleanup", action="store_true", help="Skip Step 0 zombie cleanup")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Read but no writes (no file, no cleanup deletes)",
    )
    args = ap.parse_args(argv)

    if args.days < 1:
        print("usage-report: --days must be ≥ 1", file=sys.stderr)
        return 2

    try:
        result = run(
            days=args.days,
            output=args.output,
            out_dir=args.out_dir,
            stdout_only=args.stdout_only,
            no_cleanup=args.no_cleanup,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"usage-report: failed: {exc}", file=sys.stderr)
        return 1

    print(result["digest"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
