"""sessions_reader.py — shared reader library for PROJ-034 Phase 2.

Used by:
  - tools/rollup-report.py     (T-011 — project/programme rollups)
  - tools/efficiency-report.py (T-012 — model efficiency cross-tabs)

Scrolls the cloud Qdrant `sessions` collection by HTTP and yields payload
dicts. Designed for ad-hoc CLI invocation: reads `PODZONE_QDRANT_APIKEY` from
env (set in `~/.claude/settings.json` and inherited by Claude-Code-spawned
subprocesses). Pass `api_key=` to override for tests / unusual environments.

If the env var is missing, callers should rely on the `mcp__secrets__secret_run`
wrapper instead — see STATUS.md "Workstation Qdrant key returns 403 on scroll"
for the fallback design. Empirical evidence (Hermes 2026-05-21 consolidation
Step 7b) shows direct invocation works from Claude-Code subprocesses; the
fallback is only needed for shells without env propagation.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterator, Optional

try:
    import requests  # type: ignore
except ImportError:
    requests = None  # type: ignore[assignment]

from lib.sessions_upsert import CLOUD_QDRANT_URL, SESSIONS_COLLECTION


DEFAULT_BATCH_SIZE = 100
SCROLL_TIMEOUT = 30.0


class SessionsReaderError(RuntimeError):
    """Raised on Qdrant HTTP errors that the caller should surface."""


def _resolve_api_key(api_key: Optional[str]) -> str:
    if api_key:
        return api_key
    env_key = os.environ.get("PODZONE_QDRANT_APIKEY", "")
    if not env_key:
        raise ValueError(
            "PODZONE_QDRANT_APIKEY not set and no api_key= override provided. "
            "Set the env var (see ~/.claude/settings.json) or pass api_key=."
        )
    return env_key


def scroll_all_sessions(
    api_key: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: Optional[int] = None,
    qdrant_url: str = CLOUD_QDRANT_URL,
    collection: str = SESSIONS_COLLECTION,
    scroll_filter: Optional[dict] = None,
) -> Iterator[dict]:
    """Yield session payload dicts from the cloud Qdrant `sessions` collection.

    api_key: PODZONE_QDRANT_APIKEY override. If None, reads from env.
             Raises ValueError if neither provided.
    batch_size: Qdrant scroll batch size.
    limit: stop after this many points (None = all).
    scroll_filter: optional Qdrant filter dict applied server-side.
    Yields: payload dicts (vector excluded).
    """
    if requests is None:
        raise SessionsReaderError("requests library not available")

    resolved_key = _resolve_api_key(api_key)
    url = f"{qdrant_url}/collections/{collection}/points/scroll"
    headers = {"api-key": resolved_key}

    yielded = 0
    offset = None
    while True:
        body: dict = {
            "limit": batch_size,
            "with_payload": True,
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        if scroll_filter is not None:
            body["filter"] = scroll_filter

        r = requests.post(url, headers=headers, json=body, timeout=SCROLL_TIMEOUT)
        if r.status_code == 403:
            raise SessionsReaderError(
                "Qdrant scroll returned 403. The current API key is not "
                "authorised for scroll on `sessions`. See STATUS.md "
                "'Workstation Qdrant key returns 403 on scroll' — fall back to "
                "mcp__secrets__secret_run if invoked outside Claude Code."
            )
        r.raise_for_status()

        result = r.json().get("result", {}) or {}
        points = result.get("points") or []
        for p in points:
            payload = p.get("payload") or {}
            yield payload
            yielded += 1
            if limit is not None and yielded >= limit:
                return

        offset = result.get("next_page_offset")
        if not offset:
            return


def query_sessions_in_window(
    start_iso: str,
    end_iso: Optional[str] = None,
    api_key: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    qdrant_url: str = CLOUD_QDRANT_URL,
    collection: str = SESSIONS_COLLECTION,
) -> list[dict]:
    """Return sessions with `last_message_ts` in [start_iso, end_iso].

    end_iso defaults to now() UTC. Uses a server-side range filter on the
    string field (ISO 8601 sorts correctly lexicographically).
    """
    if end_iso is None:
        end_iso = datetime.now(timezone.utc).isoformat()

    scroll_filter = {
        "must": [
            {
                "key": "last_message_ts",
                "range": {"gte": start_iso, "lte": end_iso},
            }
        ]
    }

    return list(
        scroll_all_sessions(
            api_key=api_key,
            batch_size=batch_size,
            qdrant_url=qdrant_url,
            collection=collection,
            scroll_filter=scroll_filter,
        )
    )
