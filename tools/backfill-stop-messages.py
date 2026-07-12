#!/usr/bin/env python3
"""
backfill-stop-messages.py — backfill ``last_assistant_message`` onto CST Stop
points from the workstation transcript corpus (PROJ-039/T-072, CC-382).

Walks every ``~/.claude/projects/**/*.jsonl`` (mirrors backfill-sessions.py:
streaming parse, idempotent, per-workspace report) and derives the historical
stop events:

  * assistant records with ``message.stop_reason == "end_turn"``, excluding
    ``isSidechain: true`` (subagent turns);
  * plus the final text-carrying assistant record of a transcript that ends
    without one (abrupt cut / headless kill) — marked ``transcript_cut``.

Reconciliation with live Stop points (operator-confirmed 2026-07-07,
enrich-or-create): an existing live point matching ``session_id`` + ±5 s
timestamp gets ONLY ``last_assistant_message`` written via set_payload (never a
full upsert — SD-3-001); an unmatched stop event becomes a new point with
deterministic id ``uuid5("stop-backfill/{sessionId}/{record-uuid}")``,
``data_source: stop_backfill``, repository/branch from the record, and an
embed-optional ``response_vector`` (PROJ-041/T-002: embedded from the
head-truncated message, T-027 bounding, only when ``OLLAMA_HOST`` is
configured; vector-less otherwise).

Idempotent: re-runs converge — already-enriched matches and already-existing
backfill ids are skipped, so an immediate re-run enriches nothing and creates
0 new points.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib import qdrant_http, session_substrate, stop_telemetry  # noqa: E402

COLLECTION = "claude_session_telemetry"
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
MATCH_TOLERANCE_S = 5.0
BACKFILL_NAMESPACE = "stop-backfill"

# A transcript modified this recently is treated as an ACTIVE session: its
# trailing cut event is suppressed (see derive_stop_events include_cut).
ACTIVE_TRANSCRIPT_WINDOW_S = 600.0

UUID_FILENAME_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl$",
    re.IGNORECASE,
)


def backfill_point_id(session_id: str, record_uuid: str) -> str:
    return str(uuid.uuid5(
        uuid.NAMESPACE_DNS, f"{BACKFILL_NAMESPACE}/{session_id}/{record_uuid}"
    ))


def _epoch(ts: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


# --------------------------------------------------------------------------- #
# Stop-event derivation (streaming, per transcript)
# --------------------------------------------------------------------------- #

def derive_stop_events(path: Path, *, include_cut: bool = True) -> dict:
    """Stream one transcript; returns {events, skipped_sidechain, skipped_empty}.

    An event: ``{uuid, timestamp, session_id, cwd, git_branch, text, cut}``.
    ``cut=True`` marks the trailing final-assistant event of a transcript that
    ended without an ``end_turn`` (headless kill / abrupt cut).

    ``include_cut=False`` suppresses that trailing event — used for transcripts
    still being written (an ACTIVE session's tail is a moving target: each run
    would mint a new record-uuid → a new uuid5 point, breaking re-run
    convergence; its stop events belong to the live T-071 hook anyway).
    """
    events: list[dict] = []
    skipped_sidechain = 0
    skipped_empty = 0
    last_text_rec: Optional[dict] = None   # last non-sidechain assistant w/ text
    last_was_event = False                 # did that record already emit an event?

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"assistant"' not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            message = rec.get("message") or {}
            is_end_turn = message.get("stop_reason") == "end_turn"
            if rec.get("isSidechain"):
                if is_end_turn:
                    skipped_sidechain += 1
                continue
            text = stop_telemetry._text_of(rec)
            if text:
                last_text_rec = rec
                last_was_event = False
            if not is_end_turn:
                continue
            if not text:
                skipped_empty += 1
                continue
            events.append(_event_from(rec, text, cut=False))
            last_was_event = True

    if include_cut and last_text_rec is not None and not last_was_event:
        events.append(_event_from(
            last_text_rec, stop_telemetry._text_of(last_text_rec), cut=True
        ))
    return {
        "events": events,
        "skipped_sidechain": skipped_sidechain,
        "skipped_empty": skipped_empty,
    }


def _is_active_transcript(path: Path, *, now: Optional[float] = None) -> bool:
    """Modified within ACTIVE_TRANSCRIPT_WINDOW_S → the session is still writing."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    return ((now if now is not None else time.time()) - mtime
            < ACTIVE_TRANSCRIPT_WINDOW_S)


def _event_from(rec: dict, text: str, *, cut: bool) -> dict:
    return {
        "uuid": rec.get("uuid", "") or "",
        "timestamp": rec.get("timestamp", "") or "",
        "session_id": rec.get("sessionId", "") or "",
        "cwd": rec.get("cwd", "") or "",
        "git_branch": rec.get("gitBranch", "") or "",
        "text": text[:stop_telemetry.MESSAGE_CAP_CHARS],
        "cut": cut,
    }


# --------------------------------------------------------------------------- #
# Live-point index (one scroll up front)
# --------------------------------------------------------------------------- #

def load_stop_point_index() -> dict:
    """Scroll every Stop point once; returns
    ``{"by_session": {sid: [(epoch, point_id, has_message)]}, "ids": set}``."""
    by_session: dict[str, list] = {}
    ids: set[str] = set()
    offset = None
    while True:
        body = {
            "filter": {"must": [
                {"key": "event_type", "match": {"value": "Stop"}}
            ]},
            "limit": 1000,
            "with_payload": {"include": [
                "session_id", "timestamp", "last_assistant_message"
            ]},
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        resp = qdrant_http.scroll(collection=COLLECTION, body=body)
        result = resp.get("result") or {}
        for point in result.get("points") or []:
            pid = str(point.get("id"))
            payload = point.get("payload") or {}
            ids.add(pid)
            sid = payload.get("session_id") or ""
            epoch = _epoch(payload.get("timestamp") or "")
            if sid and epoch is not None:
                by_session.setdefault(sid, []).append(
                    (epoch, pid, bool(payload.get("last_assistant_message")))
                )
        offset = result.get("next_page_offset")
        if offset is None:
            break
    return {"by_session": by_session, "ids": ids}


def match_live_point(index: dict, session_id: str, epoch: float,
                     claimed: set[str]) -> Optional[tuple[str, bool]]:
    """Nearest unclaimed live Stop point within ±5 s → (point_id, has_message)."""
    best: Optional[tuple[float, str, bool]] = None
    for point_epoch, pid, has_message in index["by_session"].get(session_id, []):
        if pid in claimed:
            continue
        delta = abs(point_epoch - epoch)
        if delta <= MATCH_TOLERANCE_S and (best is None or delta < best[0]):
            best = (delta, pid, has_message)
    if best is None:
        return None
    return best[1], best[2]


# --------------------------------------------------------------------------- #
# Backfill walk
# --------------------------------------------------------------------------- #

def _walk_jsonls(projects_dir: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    if not projects_dir.is_dir():
        return out
    for proj in sorted(projects_dir.iterdir()):
        if not proj.is_dir():
            continue
        for f in sorted(proj.iterdir()):
            if f.is_file() and UUID_FILENAME_RE.match(f.name):
                out.append((proj.name, f))
    return out


def _created_point(event: dict) -> dict:
    """The full new-point body for an unmatched stop event (vector added by caller)."""
    cwd = event["cwd"]
    repo_name = Path(cwd).name if cwd else "unknown"
    payload = {
        "event_type": "Stop",
        "session_id": event["session_id"],
        "timestamp": event["timestamp"],
        "repository": {
            "name": repo_name or "unknown",
            "git_branch": event["git_branch"] or "unknown",
            "commit_sha": "unknown",
        },
        "stop_reason": "end_turn",
        "last_assistant_message": event["text"],
        "message_source": "cut" if event["cut"] else "end_turn",
        "turn_uuid": event["uuid"],
        "data_source": "stop_backfill",
    }
    if event["cut"]:
        payload["transcript_cut"] = True
    return payload


def backfill(projects_dir: Path = DEFAULT_PROJECTS_DIR,
             workspace_filter: Optional[str] = None,
             dry_run: bool = False) -> dict:
    files = _walk_jsonls(projects_dir)
    if workspace_filter:
        files = [(ws, f) for ws, f in files if ws == workspace_filter]

    index = ({"by_session": {}, "ids": set()} if dry_run
             else load_stop_point_index())
    claimed: set[str] = set()
    per_dir: dict[str, dict] = {}
    totals = {"files": len(files), "found": 0, "enriched": 0, "created": 0,
              "skipped_sidechain": 0, "already": 0, "skipped_empty": 0,
              "errors": 0}

    def bucket(ws: str) -> dict:
        return per_dir.setdefault(ws, {
            "found": 0, "enriched": 0, "created": 0,
            "skipped_sidechain": 0, "already": 0,
        })

    for ws, path in files:
        entry = bucket(ws)
        try:
            derived = derive_stop_events(
                path, include_cut=not _is_active_transcript(path)
            )
        except Exception as exc:
            print(f"[backfill-stop] derive failed for {path.name}: {exc}",
                  file=sys.stderr)
            totals["errors"] += 1
            continue
        entry["skipped_sidechain"] += derived["skipped_sidechain"]
        totals["skipped_sidechain"] += derived["skipped_sidechain"]
        totals["skipped_empty"] += derived["skipped_empty"]

        for event in derived["events"]:
            entry["found"] += 1
            totals["found"] += 1
            if not event["session_id"] or not event["uuid"]:
                totals["errors"] += 1
                continue
            if dry_run:
                continue
            epoch = _epoch(event["timestamp"])
            match = (match_live_point(index, event["session_id"], epoch, claimed)
                     if epoch is not None else None)
            try:
                if match is not None:
                    pid, has_message = match
                    claimed.add(pid)
                    if has_message:
                        entry["already"] += 1
                        totals["already"] += 1
                        continue
                    # Operator scope: ONLY this field backfills onto live points.
                    qdrant_http.set_payload(
                        {"last_assistant_message": event["text"]}, [pid],
                        collection=COLLECTION,
                    )
                    entry["enriched"] += 1
                    totals["enriched"] += 1
                    continue
                pid = backfill_point_id(event["session_id"], event["uuid"])
                if pid in index["ids"]:
                    entry["already"] += 1
                    totals["already"] += 1
                    continue
                vector = session_substrate.maybe_embed(
                    event["text"], label="stop-backfill"
                )
                vector_map = (
                    {"response_vector": vector} if vector is not None else {}
                )
                qdrant_http.upsert_points(
                    [{"id": pid, "vector": vector_map,
                      "payload": _created_point(event)}],
                    collection=COLLECTION,
                )
                index["ids"].add(pid)
                entry["created"] += 1
                totals["created"] += 1
            except Exception as exc:
                print(f"[backfill-stop] write failed "
                      f"({event['session_id'][:8]}/{event['uuid'][:8]}): {exc}",
                      file=sys.stderr)
                totals["errors"] += 1

    totals["per_dir"] = per_dir
    totals["dry_run"] = dry_run
    return totals


def print_report(report: dict) -> None:
    mode = " (dry-run: derivation only, no Qdrant)" if report["dry_run"] else ""
    print(f"Scanned {report['files']} transcripts across "
          f"{len(report['per_dir'])} project dirs{mode}.")
    print(f"  Stop events found:  {report['found']}")
    print(f"  Enriched (live pt): {report['enriched']}")
    print(f"  Created (backfill): {report['created']}")
    print(f"  Already done:       {report['already']}")
    print(f"  Skipped sidechain:  {report['skipped_sidechain']}")
    print(f"  Skipped empty-text: {report['skipped_empty']}")
    if report["errors"]:
        print(f"  Errors:             {report['errors']}")
    if not report["per_dir"]:
        return
    print()
    widest = max(len(ws) for ws in report["per_dir"])
    header = (f"  {'project dir'.ljust(widest)}  found  enriched  created  "
              f"already  sidechain")
    print(header)
    for ws, e in sorted(report["per_dir"].items()):
        if not any(e.values()):
            continue
        print(f"  {ws.ljust(widest)}  {e['found']:>5}  {e['enriched']:>8}  "
              f"{e['created']:>7}  {e['already']:>7}  {e['skipped_sidechain']:>9}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--projects-dir", type=Path, default=DEFAULT_PROJECTS_DIR,
                    help="Root of ~/.claude/projects/ (default: %(default)s)")
    ap.add_argument("--workspace", help="Only walk this project-dir name")
    ap.add_argument("--dry-run", action="store_true",
                    help="Derive and report, but do not touch Qdrant")
    args = ap.parse_args(argv)

    # Fail loudly up front if the write path has no credentials (PROJ-033/T-016).
    if not args.dry_run:
        try:
            qdrant_http.resolve_api_key()
        except qdrant_http.QdrantAuthError as exc:
            print(f"[backfill-stop] {exc}", file=sys.stderr)
            return 1

    report = backfill(projects_dir=args.projects_dir,
                      workspace_filter=args.workspace,
                      dry_run=args.dry_run)
    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
