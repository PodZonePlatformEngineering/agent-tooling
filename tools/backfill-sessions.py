#!/usr/bin/env python3
"""
backfill-sessions.py — walk ~/.claude/projects/ and upsert each session JSONL
into the cloud Qdrant `sessions` collection.

Use cases:
  - One-shot historical take-on (run once after PROJ-034 ships).
  - Catch-up runs from /consolidate-tasks gap-fill (T-007, future).
  - Workspace-scoped debugging (`--workspace foo`).

Best-effort: failures on individual files are logged and counted; the walker
continues. Idempotent: deterministic point IDs mean repeated runs are no-ops.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib import jsonl_scrape, sessions_upsert  # noqa: E402


DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
UUID_FILENAME_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl$",
    re.IGNORECASE,
)


def _format_int(n: int) -> str:
    return f"{n:,}"


def _walk_jsonls(projects_dir: Path) -> list[Path]:
    if not projects_dir.is_dir():
        return []
    out: list[Path] = []
    for proj in sorted(projects_dir.iterdir()):
        if not proj.is_dir():
            continue
        for f in sorted(proj.iterdir()):
            if f.is_file() and UUID_FILENAME_RE.match(f.name):
                out.append(f)
    return out


def _parse_since(s: str) -> datetime:
    # Accept YYYY-MM-DD or ISO 8601
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return datetime.fromisoformat(f"{s}T00:00:00+00:00")
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _file_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _point_exists(session_id: str) -> bool:
    return sessions_upsert._get_existing(session_id) is not None


def _aggregate_usage(target: dict, mu: dict) -> None:
    for model, bucket in mu.items():
        tgt = target.setdefault(
            model,
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "iterations": 0,
            },
        )
        for k in tgt.keys():
            tgt[k] += int(bucket.get(k, 0))


def backfill(
    projects_dir: Path = DEFAULT_PROJECTS_DIR,
    workspace_filter: Optional[str] = None,
    since: Optional[datetime] = None,
    only_new: bool = False,
    dry_run: bool = False,
) -> dict:
    """Walk, scrape, upsert. Returns a report dict."""
    files = _walk_jsonls(projects_dir)

    per_workspace: dict[str, dict] = {}
    workspace_agents: dict[str, set] = {}
    upserted = 0
    skipped_finalised = 0
    skipped_only_new = 0
    skipped_since = 0
    skipped_workspace = 0
    scrape_errors = 0
    upsert_errors = 0

    for f in files:
        try:
            mtime_iso = _file_mtime_iso(f)
        except OSError:
            scrape_errors += 1
            continue

        if since is not None:
            try:
                if datetime.fromisoformat(mtime_iso) < since:
                    skipped_since += 1
                    continue
            except ValueError:
                pass

        try:
            payload = jsonl_scrape.scrape(f)
        except Exception as exc:
            print(f"[backfill] scrape failed for {f.name}: {exc}", file=sys.stderr)
            scrape_errors += 1
            continue

        ws = payload.get("workspace") or "unknown"
        if workspace_filter and ws != workspace_filter:
            skipped_workspace += 1
            continue

        session_id = payload["session_id"]
        new_mtime = payload.get("jsonl_mtime") or mtime_iso

        if not dry_run and only_new and _point_exists(session_id):
            skipped_only_new += 1
            continue

        if not dry_run and sessions_upsert.should_skip_backfill(session_id, new_mtime):
            skipped_finalised += 1
            continue

        if dry_run:
            upserted += 1
        else:
            r = sessions_upsert.upsert_session(payload, data_source="backfill")
            if r["ok"]:
                upserted += 1
            else:
                upsert_errors += 1
                continue

        ws_entry = per_workspace.setdefault(
            ws, {"sessions": 0, "model_usage": {}}
        )
        ws_entry["sessions"] += 1
        _aggregate_usage(ws_entry["model_usage"], payload.get("model_usage") or {})
        agent = payload.get("agent")
        if agent:
            workspace_agents.setdefault(ws, set()).add(agent)

    grand_totals: dict = {}
    for ws_entry in per_workspace.values():
        _aggregate_usage(grand_totals, ws_entry["model_usage"])

    return {
        "scanned": len(files),
        "upserted": upserted,
        "skipped_finalised": skipped_finalised,
        "skipped_only_new": skipped_only_new,
        "skipped_since": skipped_since,
        "skipped_workspace": skipped_workspace,
        "scrape_errors": scrape_errors,
        "upsert_errors": upsert_errors,
        "per_workspace": per_workspace,
        "workspace_agents": workspace_agents,
        "grand_totals": grand_totals,
        "dry_run": dry_run,
    }


def print_report(report: dict) -> None:
    mode = " (dry-run)" if report["dry_run"] else ""
    n_ws = len(report["per_workspace"])
    print(
        f"Scanned: {_format_int(report['scanned'])} JSONL files across "
        f"{n_ws} workspaces{mode}."
    )
    print(f"  Upserted: {_format_int(report['upserted'])} sessions")
    if report["skipped_finalised"]:
        print(
            f"  Skipped:    {_format_int(report['skipped_finalised'])} "
            f"(already finalised by /session-end)"
        )
    if report["skipped_only_new"]:
        print(
            f"  Skipped:    {_format_int(report['skipped_only_new'])} "
            f"(--only-new: existing point)"
        )
    if report["skipped_since"]:
        print(
            f"  Skipped:    {_format_int(report['skipped_since'])} "
            f"(older than --since)"
        )
    if report["skipped_workspace"]:
        print(
            f"  Skipped:    {_format_int(report['skipped_workspace'])} "
            f"(workspace filter)"
        )
    if report["scrape_errors"] or report["upsert_errors"]:
        print(
            f"  Errors:     scrape={report['scrape_errors']} "
            f"upsert={report['upsert_errors']}"
        )

    if report["per_workspace"]:
        print()
        print("Per-workspace totals:")
        # Format: " {ws:<20}  sessions=N  {model}={N tokens}  ..."
        widest = max(len(ws) for ws in report["per_workspace"])
        for ws, entry in sorted(report["per_workspace"].items()):
            mu_str = "  ".join(
                f"{model}={_format_int(b['input_tokens'] + b['output_tokens'])} tokens"
                for model, b in sorted(entry["model_usage"].items())
            )
            note = ""
            agents = report["workspace_agents"].get(ws, set())
            if len(agents) >= 2:
                note = (
                    f"  ⚠️ agent attribution best-effort "
                    f"({len(agents)} candidates: {','.join(sorted(agents))})"
                )
            print(
                f"  {ws.ljust(widest)}  "
                f"sessions={entry['sessions']:<4} {mu_str}{note}"
            )

    if report["grand_totals"]:
        print()
        print("Total tokens across all sessions:")
        for model, b in sorted(report["grand_totals"].items()):
            print(
                f"  {model}:  {_format_int(b['input_tokens'])} input · "
                f"{_format_int(b['output_tokens'])} output · "
                f"{_format_int(b['cache_read_input_tokens'])} cache_read"
            )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--projects-dir",
        type=Path,
        default=DEFAULT_PROJECTS_DIR,
        help="Root of ~/.claude/projects/ (default: %(default)s)",
    )
    ap.add_argument("--workspace", help="Only walk this workspace name")
    ap.add_argument("--since", help="Only walk JSONLs modified after this date (YYYY-MM-DD or ISO)")
    ap.add_argument(
        "--only-new",
        action="store_true",
        help="Skip session_ids that already have a point in `sessions`",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and report, but do not upsert",
    )
    args = ap.parse_args(argv)

    since_dt = _parse_since(args.since) if args.since else None

    report = backfill(
        projects_dir=args.projects_dir,
        workspace_filter=args.workspace,
        since=since_dt,
        only_new=args.only_new,
        dry_run=args.dry_run,
    )
    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
