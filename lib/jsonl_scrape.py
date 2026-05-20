"""
jsonl_scrape.py — read a Claude Code session JSONL and return a sessions payload.

Pure function: returns the T-001 payload dict. Caller is responsible for setting
`data_source` / `updated_at` and upserting to Qdrant.

Critical (D1): sum every assistant entry with `.message.usage` regardless of
`stop_reason`. Filtering on stop_reason discards ~96% of usage in a real session
because intra-turn tool pauses report `stop_reason: tool_use` with usage attached.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import session_metadata


USAGE_FIELDS_FLAT = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _zero_model_bucket() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_5m_input_tokens": 0,
        "cache_creation_1h_input_tokens": 0,
        "web_search_requests": 0,
        "web_fetch_requests": 0,
        "iterations": 0,
    }


def _add_usage(bucket: dict, usage: dict) -> None:
    for f in USAGE_FIELDS_FLAT:
        v = usage.get(f)
        if isinstance(v, int):
            bucket[f] += v

    cache_creation = usage.get("cache_creation")
    if isinstance(cache_creation, dict):
        for src, dst in (
            ("ephemeral_5m_input_tokens", "cache_creation_5m_input_tokens"),
            ("ephemeral_1h_input_tokens", "cache_creation_1h_input_tokens"),
        ):
            v = cache_creation.get(src)
            if isinstance(v, int):
                bucket[dst] += v

    server_tool_use = usage.get("server_tool_use")
    if isinstance(server_tool_use, dict):
        for src, dst in (
            ("web_search_requests", "web_search_requests"),
            ("web_fetch_requests", "web_fetch_requests"),
        ):
            v = server_tool_use.get(src)
            if isinstance(v, int):
                bucket[dst] += v


def _sum_totals(model_usage: dict) -> dict:
    totals = _zero_model_bucket()
    for bucket in model_usage.values():
        for k, v in bucket.items():
            totals[k] += v
    return totals


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # JSONL timestamps are ISO 8601 with a trailing Z.
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def scrape(jsonl_path: str | Path) -> dict:
    """Read a session JSONL and return the sessions-collection payload."""
    path = Path(jsonl_path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    session_id = path.stem
    cwd: str | None = None
    first_ts: str | None = None
    last_ts: str | None = None
    model_usage: dict[str, dict] = {}
    message_counts: dict[str, int] = {}
    malformed = 0

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                print(
                    f"[jsonl_scrape] malformed line in {path.name}",
                    file=sys.stderr,
                )
                continue

            if not isinstance(entry, dict):
                continue

            etype = entry.get("type")
            if isinstance(etype, str):
                message_counts[etype] = message_counts.get(etype, 0) + 1

            ts = entry.get("timestamp")
            if isinstance(ts, str):
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts

            if cwd is None:
                c = entry.get("cwd")
                if isinstance(c, str) and c:
                    cwd = c

            # D1: assistant + .message.usage present, no stop_reason filter.
            if etype == "assistant":
                message = entry.get("message")
                if isinstance(message, dict) and "usage" in message:
                    usage = message.get("usage")
                    if isinstance(usage, dict):
                        model = message.get("model") or "unknown"
                        bucket = model_usage.setdefault(model, _zero_model_bucket())
                        _add_usage(bucket, usage)
                        bucket["iterations"] += 1

    if cwd is None:
        # Fall back to decoding the parent dir name (~/.claude/projects/-Users-...).
        cwd = session_metadata._decode_project_dirname(path.parent.name)

    meta = session_metadata.resolve(cwd=cwd, jsonl_path=path)

    duration = 0
    first_dt = _parse_ts(first_ts)
    last_dt = _parse_ts(last_ts)
    if first_dt and last_dt:
        duration = int((last_dt - first_dt).total_seconds())

    mtime_iso = datetime.fromtimestamp(
        os.stat(path).st_mtime, tz=timezone.utc
    ).isoformat()

    payload: dict[str, Any] = {
        "session_id": session_id,
        "workspace": meta["workspace"],
        "agent": meta["agent"],
        "cwd": meta["cwd"],
        "first_message_ts": first_ts,
        "last_message_ts": last_ts,
        "duration_seconds": duration,
        "model_usage": model_usage,
        "total_tokens": _sum_totals(model_usage),
        "message_counts": message_counts,
        "jsonl_path": str(path.resolve()),
        "jsonl_mtime": mtime_iso,
    }

    if malformed:
        payload["_warnings"] = {"malformed_lines": malformed}

    return payload
