#!/usr/bin/env python3
"""
backfill-prompt-logs.py — walk ~/.claude/projects/ and embed each user turn
into the cloud Qdrant `prompt_logs` collection.

Mirrors `backfill-sessions.py` for layout/filters but writes to a different
collection. Schema must match `~/.claude/hooks/ingest-transcript.py` so that
existing RAG retrieval works against the backfilled corpus.

Use cases:
  - Populate roadmapTeam prompt corpus (the hook never fires reliably in VSCode).
  - One-shot historical take-on after PROJ-034 ships.

Best-effort: failures on individual turns (Ollama, Qdrant) are logged and the
walker continues. Idempotent: deterministic point IDs (uuid5(session_id:turn)).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib import session_metadata  # noqa: E402

try:
    import requests  # type: ignore
except ImportError:
    requests = None  # type: ignore[assignment]


DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
UUID_FILENAME_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl$",
    re.IGNORECASE,
)

CLOUD_QDRANT_URL = (
    "https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io"
)
PROMPT_LOGS_COLLECTION = "prompt_logs"
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
EMBED_CHAR_LIMIT = 2000
PROMPT_TEXT_CHAR_LIMIT = 1000


def _log(msg: str) -> None:
    print(f"[backfill-prompt-logs] {msg}", file=sys.stderr)


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
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return datetime.fromisoformat(f"{s}T00:00:00+00:00")
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _file_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _extract_user_turns(jsonl_path: Path) -> list[dict]:
    """Return list of {turn_number, text, timestamp} dicts, matching the existing hook."""
    turns: list[dict] = []
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("type") != "user":
                    continue
                msg = obj.get("message") or {}
                content = msg.get("content", "")
                if isinstance(content, list):
                    text = " ".join(
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                else:
                    text = str(content) if content is not None else ""
                text = text.strip()
                if not text:
                    continue
                turns.append(
                    {
                        "turn_number": len(turns),
                        "text": text,
                        "timestamp": obj.get("timestamp")
                        or datetime.now(timezone.utc).isoformat(),
                    }
                )
    except OSError as exc:
        _log(f"read failed for {jsonl_path}: {exc}")
    return turns


def _embed(text: str, timeout: float = 30.0) -> Optional[list[float]]:
    if requests is None:
        return None
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text[:EMBED_CHAR_LIMIT]},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json().get("embedding")
    except Exception as exc:
        _log(f"embed failed: {exc}")
        return None


def _qdrant_headers() -> dict:
    api_key = os.environ.get("PODZONE_QDRANT_APIKEY", "")
    return {"api-key": api_key} if api_key else {}


def _point_id(session_id: str, turn_number: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{session_id}:{turn_number}"))


def _point_exists(pid: str) -> bool:
    if requests is None:
        return False
    try:
        r = requests.get(
            f"{CLOUD_QDRANT_URL}/collections/{PROMPT_LOGS_COLLECTION}/points/{pid}",
            headers=_qdrant_headers(),
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False


def _upsert(pid: str, vector: list[float], payload: dict) -> bool:
    if requests is None:
        return False
    try:
        r = requests.put(
            f"{CLOUD_QDRANT_URL}/collections/{PROMPT_LOGS_COLLECTION}/points",
            headers=_qdrant_headers(),
            json={"points": [{"id": pid, "vector": vector, "payload": payload}]},
            timeout=15,
        )
        r.raise_for_status()
        return True
    except Exception as exc:
        _log(f"upsert failed: {exc}")
        return False


def process_file(
    jsonl_path: Path,
    dry_run: bool = False,
    skip_existing: bool = False,
    max_turns: Optional[int] = None,
    progress_every: int = 50,
) -> dict:
    """Process one JSONL file. Returns counts dict."""
    session_id = jsonl_path.stem
    meta = session_metadata.resolve(jsonl_path=jsonl_path)
    workspace = meta["workspace"]
    agent = meta["agent"]
    cwd = meta["cwd"]

    turns = _extract_user_turns(jsonl_path)
    if max_turns is not None:
        turns = turns[:max_turns]

    result = {
        "turns_found": len(turns),
        "upserted": 0,
        "skipped_existing": 0,
        "skipped_empty": 0,
        "skipped_embed_fail": 0,
        "workspace": workspace,
    }

    for turn in turns:
        if not turn["text"].strip():
            result["skipped_empty"] += 1
            continue

        pid = _point_id(session_id, turn["turn_number"])

        if dry_run:
            result["upserted"] += 1
            continue

        if skip_existing and _point_exists(pid):
            result["skipped_existing"] += 1
            continue

        vector = _embed(turn["text"])
        if vector is None:
            result["skipped_embed_fail"] += 1
            continue

        payload = {
            "session_id": session_id,
            "agent": agent,
            "turn_number": turn["turn_number"],
            "timestamp": turn["timestamp"],
            "prompt_text": turn["text"][:PROMPT_TEXT_CHAR_LIMIT],
            "cwd": cwd,
            "workspace": workspace,
        }
        if _upsert(pid, vector, payload):
            result["upserted"] += 1
        else:
            result["skipped_embed_fail"] += 1

        if (
            progress_every
            and result["upserted"]
            and result["upserted"] % progress_every == 0
        ):
            _log(
                f"{jsonl_path.name}: {result['upserted']}/{len(turns)} turns upserted"
            )

    return result


def backfill(
    projects_dir: Path = DEFAULT_PROJECTS_DIR,
    workspace_filter: Optional[str] = None,
    since: Optional[datetime] = None,
    dry_run: bool = False,
    skip_existing: bool = False,
    max_turns: Optional[int] = None,
) -> dict:
    files = _walk_jsonls(projects_dir)
    per_workspace: dict[str, dict] = {}
    totals = {
        "scanned": len(files),
        "turns_found": 0,
        "upserted": 0,
        "skipped_existing": 0,
        "skipped_empty": 0,
        "skipped_embed_fail": 0,
        "skipped_workspace": 0,
        "skipped_since": 0,
    }

    for f in files:
        if since is not None:
            try:
                if _file_mtime(f) < since:
                    totals["skipped_since"] += 1
                    continue
            except OSError:
                continue

        try:
            meta = session_metadata.resolve(jsonl_path=f)
        except Exception as exc:
            _log(f"resolve failed for {f.name}: {exc}")
            continue
        ws = meta["workspace"]
        if workspace_filter and ws != workspace_filter:
            totals["skipped_workspace"] += 1
            continue

        result = process_file(
            f,
            dry_run=dry_run,
            skip_existing=skip_existing,
            max_turns=max_turns,
        )

        for k in (
            "turns_found",
            "upserted",
            "skipped_existing",
            "skipped_empty",
            "skipped_embed_fail",
        ):
            totals[k] += result[k]
        bucket = per_workspace.setdefault(
            ws, {"turns": 0, "upserted": 0}
        )
        bucket["turns"] += result["turns_found"]
        bucket["upserted"] += result["upserted"]

    return {"totals": totals, "per_workspace": per_workspace, "dry_run": dry_run}


def print_report(report: dict) -> None:
    t = report["totals"]
    mode = " (dry-run)" if report["dry_run"] else ""
    print(
        f"Scanned: {_format_int(t['scanned'])} JSONL files across "
        f"{len(report['per_workspace'])} workspaces{mode}"
    )
    print(f"  User turns found:     {_format_int(t['turns_found'])}")
    print(f"  Embedded + upserted:  {_format_int(t['upserted'])}")
    if t["skipped_existing"]:
        print(f"  Skipped (existing):   {_format_int(t['skipped_existing'])}")
    if t["skipped_empty"]:
        print(f"  Skipped (empty text): {_format_int(t['skipped_empty'])}")
    if t["skipped_embed_fail"]:
        print(f"  Skipped (failures):   {_format_int(t['skipped_embed_fail'])}")
    if t["skipped_workspace"]:
        print(f"  Skipped (filter):     {_format_int(t['skipped_workspace'])}")
    if t["skipped_since"]:
        print(f"  Skipped (--since):    {_format_int(t['skipped_since'])}")

    if report["per_workspace"]:
        print()
        print("Per-workspace turn counts:")
        widest = max(len(ws) for ws in report["per_workspace"])
        for ws, entry in sorted(report["per_workspace"].items()):
            secs = int(entry["upserted"] * 0.15)  # rough 150 ms/turn estimate
            print(
                f"  {ws.ljust(widest)}  turns={_format_int(entry['upserted'])}"
                f"  est. embed time: ~{secs // 60} min wall"
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
    ap.add_argument("--since", help="Only walk JSONLs modified after this date")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse + count; no embed, no upsert",
    )
    ap.add_argument(
        "--skip-existing",
        action="store_true",
        help="Query Qdrant for each point ID; skip turns already present",
    )
    ap.add_argument(
        "--max-turns",
        type=int,
        help="Safety ceiling per JSONL (default: unlimited)",
    )
    args = ap.parse_args(argv)

    since_dt = _parse_since(args.since) if args.since else None
    report = backfill(
        projects_dir=args.projects_dir,
        workspace_filter=args.workspace,
        since=since_dt,
        dry_run=args.dry_run,
        skip_existing=args.skip_existing,
        max_turns=args.max_turns,
    )
    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
