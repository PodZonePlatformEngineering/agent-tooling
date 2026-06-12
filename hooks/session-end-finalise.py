#!/usr/bin/env python3
"""
session-end-finalise.py — session-end Qdrant write path (PROJ-039 § 2.4 steps 1-3).

Ordered contract realised here — **order is load-bearing** (§ 2.4):
  1. Upsert `response = {text, status_transition, event_refs, end_ts}` via
     set_payload, then patch the `response` named vector via update-vectors.
  2. Attach `event_refs` linking measurement points (R-012).
  3. Compute `rollup = {tool_usage, cost_tokens}` from the session JSONL and
     attach via set_payload (R-013/R-014; reconciles with a fresh re-scrape, DT-006).
  4. Commit + push the JSONL to `agent-telemetry.git` FIRST (R-015, § 2.5).
  5. ONLY if the push landed, delete the raw PreToolUse/PostToolUse points for
     this session from CST (R-013, § 2.4 step 5). If the push failed, skip the
     delete and warn — deletion safety depends on the backstop existing (C-006).

session-finalise (step 6) and the brief-result PR (step 7) remain a follow-on
(the consolidate-tasks `session-finalise` refactor, DTD § 1.4).

Best-effort: logs and exits 0 on any failure (a session-end hook must not break
teardown). Reads stdin JSON: ``session_id``, ``cwd``, ``transcript_path``.

The MVP response.text is derived from the transcript's final assistant turn; the
rich session-result authoring is the brief-result PR (PR-B / post-MVP). The key
substrate properties (response present + vector set + brief preserved + rollups
reconcile) hold regardless of how the text is authored.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _last_assistant_text(transcript_path: str) -> str:
    """Return the last assistant turn's text (best-effort; '' on any error)."""
    if not transcript_path or not Path(transcript_path).exists():
        return ""
    last = ""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "assistant":
                    continue
                message = entry.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                parts: list[str] = []
                if isinstance(content, str):
                    parts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            t = block.get("text")
                            if isinstance(t, str):
                                parts.append(t)
                turn = "\n".join(parts).strip()
                if turn:
                    last = turn
    except Exception:
        return last
    return last


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        print(f"[session-end-finalise] bad stdin: {exc}", file=sys.stderr)
        return 0

    session_id = data.get("session_id")
    transcript_path = data.get("transcript_path", "")
    if not session_id:
        print("[session-end-finalise] no session_id — nothing to finalise", file=sys.stderr)
        return 0

    try:
        from lib import session_substrate
    except Exception as exc:
        print(f"[session-end-finalise] lib import failed: {exc}", file=sys.stderr)
        return 0

    # 1. Response upsert (set_payload) + response-vector patch (update-vectors).
    response_text = _last_assistant_text(transcript_path) or f"session {session_id} ended"
    response_text = response_text[:20000]  # generous cap; payload has no hard limit
    try:
        session_substrate.upsert_response(
            session_id,
            text=response_text,
            status_transition=None,  # MVP: enriched post-MVP from session-finalise
            event_refs=[],
            ollama_host=None,
        )
        print("[session-end-finalise] response upserted + vector patched", file=sys.stderr)
    except Exception as exc:
        print(f"[session-end-finalise] response upsert skipped: {exc}", file=sys.stderr)

    # 3. Rollups (tool_usage + cost_tokens) from the JSONL — reconciles (DT-006).
    if transcript_path:
        try:
            rollup = session_substrate.compute_rollup(transcript_path)
            session_substrate.attach_rollup(session_id, rollup)
            print(
                f"[session-end-finalise] rollup attached "
                f"(tools={len(rollup['tool_usage'])}, models={len(rollup['cost_tokens'])})",
                file=sys.stderr,
            )
        except Exception as exc:
            print(f"[session-end-finalise] rollup skipped: {exc}", file=sys.stderr)

    # 4. Commit + push the telemetry JSONL FIRST (R-015) — the backstop that
    #    makes step 5 safe. Push failure blocks deletion (C-006).
    pushed = False
    try:
        from lib import telemetry_repo

        from datetime import datetime, timezone
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        res = telemetry_repo.commit_and_push(session_id, date=date)
        pushed = res.get("pushed", False)
        print(
            f"[session-end-finalise] telemetry: committed={res.get('committed')} "
            f"pushed={pushed} ({res.get('reason') or 'ok'})",
            file=sys.stderr,
        )
    except Exception as exc:
        print(f"[session-end-finalise] telemetry push skipped: {exc}", file=sys.stderr)

    # 5. Delete raw PreToolUse/PostToolUse from CST — ONLY if the push landed.
    if pushed:
        try:
            from lib import cst_cleanup

            res = cst_cleanup.delete_raw_tool_events(session_id)
            print(
                f"[session-end-finalise] CST raw events deleted "
                f"(was {res['deleted_before_count']})",
                file=sys.stderr,
            )
        except Exception as exc:
            print(f"[session-end-finalise] CST delete skipped: {exc}", file=sys.stderr)
    else:
        print(
            "[session-end-finalise] CST raw events RETAINED — telemetry push did "
            "not land; deletion gated on the backstop (C-006).",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
