#!/usr/bin/env python3
"""
stop-telemetry.py — enriched CST Stop observability point (PROJ-039/T-071, CC-381).

The thin hook entry for the Stop dual-write's observability half (R-008):
reads the hook stdin JSON, assembles the enriched payload via
``lib/stop_telemetry.py`` (last_assistant_message + background_tasks +
session_crons — the extractors are fixture-tested there), embeds the
head-truncated message text (T-027 bounding; the embed switch away from the
constant ``"Stop: end_turn"`` was operator-confirmed 2026-07-07) and writes the
CST point. Replaces the inline bash→python payload assembly that lived in
``stop.sh``, which now just pipes stdin here.

Best-effort: never raises out (a Stop hook must not break the session). On a
missing API key / unreachable Ollama or Qdrant it logs to stderr and exits 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Dependency root: parents[1] of this hook — the directory holding `lib/`. The
# hook ships sibling to `lib/` under a common parent in BOTH layouts: the
# canonical agent-tooling repo (hooks/ + lib/ at the root) and a self-contained
# home repo (.claude/hooks/ + .claude/lib/). No AGENT_TOOLING_DIR override —
# self-containment per ADR-008 D2 / PROJ-039 C2-v2.1.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

COLLECTION = "claude_session_telemetry"


def main() -> int:
    try:
        hook_input = json.loads(sys.stdin.read() or "{}")
    except Exception:
        hook_input = {}
    if not isinstance(hook_input, dict) or not hook_input.get("session_id"):
        print("[stop-telemetry] no session_id on stdin; skipped", file=sys.stderr)
        return 0

    try:
        from lib import stop_telemetry

        payload = stop_telemetry.build_payload(hook_input)
    except Exception as exc:
        print(f"[stop-telemetry] payload assembly failed: {exc}", file=sys.stderr)
        return 0

    try:
        from lib import qdrant_http, session_substrate

        vector = session_substrate.embed_text(
            session_substrate._bound_embed_input(
                stop_telemetry.embed_input_for(payload), label="stop-message"
            )
        )
        point_id = stop_telemetry.point_id_for_stop(
            payload["session_id"], payload.get("turn_uuid", "")
        )
        qdrant_http.upsert_points(
            [{"id": point_id, "vector": {"response_vector": vector},
              "payload": payload}],
            collection=COLLECTION,
        )
        print(
            f"[stop-telemetry] CST Stop point {point_id} written "
            f"(message {len(payload['last_assistant_message'])} chars, "
            f"source={payload['message_source'] or 'none'}, "
            f"bg={len(payload['background_tasks'])}, "
            f"crons={len(payload['session_crons'])})",
            file=sys.stderr,
        )
    except Exception as exc:
        # Most common benign cases: PODZONE_QDRANT_APIKEY absent in a bare
        # subprocess, or Ollama not running on this host.
        print(f"[stop-telemetry] CST write skipped: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
