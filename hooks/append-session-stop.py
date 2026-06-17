#!/usr/bin/env python3
"""
append-session-stop.py — per-Stop tasking write (PROJ-039 R-009, § 2.3).

Appends one entry to ``session_stop[]`` on the canonical `session` point via
set_payload (read-modify-write) — NEVER a full upsert, which would null the
`brief`/`response` named vectors (SD-3-001 / OT-006). This is the *tasking* half
of the Stop dual-write; ``stop.sh`` retains the CST observability point (R-008).

Best-effort: never raises out (a Stop hook must not break the session). On a
missing API key / unreachable Qdrant / absent session point it logs to stderr and
exits 0 — the session point may not exist yet pre-dispatch, which is fine.

Entry shape (DTD § 2.3): ``{ ts, response_delta, tool_uses }``.
  - ``ts``            — this Stop's timestamp.
  - ``tool_uses``     — cumulative tool_use count observed in the transcript so
                        far (best-effort; 0 when no transcript is available).
  - ``response_delta``— reserved for the per-turn response text delta; null at
                        MVP (post-MVP enrichment), key always present.

Usage: append-session-stop.py <session_id> <ts> [stop_reason] [transcript_path]
"""

from __future__ import annotations

import sys
from pathlib import Path

# Dependency root: parents[1] of this hook — the directory holding `lib/`. The
# hook ships sibling to `lib/` under a common parent in BOTH layouts: the
# canonical agent-tooling repo (hooks/ + lib/ at the root) and a self-contained
# home repo (.claude/hooks/ + .claude/lib/). No AGENT_TOOLING_DIR override —
# self-containment per ADR-008 D2 / PROJ-039 C2-v2.1.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _tool_uses_from_transcript(transcript_path: str) -> int:
    """Cumulative tool_use count in the transcript (best-effort; 0 on any error)."""
    if not transcript_path:
        return 0
    try:
        from lib import jsonl_scrape

        scraped = jsonl_scrape.scrape(transcript_path)
        return sum(scraped.get("tool_usage", {}).values())
    except Exception:
        return 0


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: append-session-stop.py <session_id> <ts> [stop_reason] [transcript_path]",
              file=sys.stderr)
        return 0  # best-effort: do not break the Stop hook
    session_id = argv[1]
    ts = argv[2]
    stop_reason = argv[3] if len(argv) > 3 else "end_turn"
    transcript_path = argv[4] if len(argv) > 4 else ""

    entry = {
        "ts": ts,
        "stop_reason": stop_reason,
        "response_delta": None,
        "tool_uses": _tool_uses_from_transcript(transcript_path),
    }

    try:
        from lib import session_substrate

        result = session_substrate.append_session_stop(session_id, entry)
        print(f"[append-session-stop] session_stop[] len={result['length']}", file=sys.stderr)
    except Exception as exc:
        # Most common benign case: session point not created yet (pre-dispatch),
        # or PODZONE_QDRANT_APIKEY absent in a bare subprocess.
        print(f"[append-session-stop] skipped: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
