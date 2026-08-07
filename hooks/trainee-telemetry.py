#!/usr/bin/env python3
"""
trainee-telemetry.py — the trainee runtime's ONLY telemetry writer
(PROJ-011/T-030, trainee-repo template v3).

Wired on SessionStart (baseline), UserPromptSubmit, PostCompact and Stop, it
writes one CST-shaped point per event to ``training_session_telemetry`` —
routed exclusively by the committed ``training-config.yaml`` (R2-2/R2-3).
Trainee observability is these points ONLY: no fleet CST write, no
agent-telemetry repo push, no log pushes (R2-5).

Point construction follows the training-collections schema
(docs/training-collections-schema.md): the event payload for the class, plus
the additive ``trainee`` field via ``training_substrate.build_telemetry_point``;
payload-only writes (T-002 hooks-never-embed); deterministic point ids so a
re-fired hook converges —

  Stop              uuid5("stop/{sid}/{turn_uuid}")   (the CST construction)
  SessionStart      uuid5("training-start/{sid}")     (one baseline per session)
  UserPromptSubmit  uuid5("training-prompt/{sid}/{sha1(prompt)[:16]}")
  PostCompact       uuid5("training-compact/{sid}/{trigger}/{minute}")

Degrades soft when Qdrant is unreachable or the config is unfilled (log +
continue, R2-4). Always exits 0. Reads the hook stdin JSON (needs
``hook_event_name``, ``session_id``). Tested by
tests/proj011/test_trainee_routing.py.

On ``Stop`` this module additionally mirrors the live transcript into the
trainee's own ``logs/session-{sid8}.jsonl`` via ``lib.session_log_mirror``
(PROJ-011/T-122 Build A) — local-file-only, no Qdrant write, independent of
the telemetry point write below.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

QDRANT_TIMEOUT = 6.0
PROMPT_SNIPPET_CHARS = 4000  # trainer-visible prompt record, bounded

HANDLED_EVENTS = ("SessionStart", "UserPromptSubmit", "PostCompact", "Stop")


def _log(message: str, session_id: str = "") -> None:
    try:
        from lib.runtime_log import log_library
        log_library("training", message, session_id=session_id or None)
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid5(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, key))


def build_event(hook_input: dict) -> tuple[dict, str] | None:
    """(event_payload, point_id) for a handled event, else ``None``.

    Stop reuses the enriched fleet CST builder (same shape → PROJ-042
    enrichment and CST readers work over both collections unchanged); the
    other classes are light, schema-consistent envelopes.
    """
    event = str(hook_input.get("hook_event_name", ""))
    sid = str(hook_input.get("session_id", ""))
    if event not in HANDLED_EVENTS or not sid:
        return None

    if event == "Stop":
        from lib import stop_telemetry
        payload = stop_telemetry.build_payload(hook_input)
        return payload, stop_telemetry.point_id_for_stop(
            sid, payload.get("turn_uuid", ""))

    payload = {
        "event_type": event,
        "session_id": sid,
        "timestamp": _now_iso(),
        "cwd": str(hook_input.get("cwd", "") or ""),
    }
    if event == "SessionStart":
        payload["source"] = str(hook_input.get("source", "") or "")
        return payload, _uuid5(f"training-start/{sid}")
    if event == "UserPromptSubmit":
        prompt = str(hook_input.get("prompt", "") or "")
        payload["prompt"] = prompt[:PROMPT_SNIPPET_CHARS]
        digest = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:16]
        return payload, _uuid5(f"training-prompt/{sid}/{digest}")
    # PostCompact — rare; minute-bucketed id is convergent enough for re-fires.
    trigger = str(hook_input.get("trigger", "") or "unknown")
    payload["trigger"] = trigger
    minute = payload["timestamp"][:16]
    return payload, _uuid5(f"training-compact/{sid}/{trigger}/{minute}")


def _mirror_session_log(hook_input: dict, repo_root: str) -> None:
    """T-122 Build A: on every Stop, overwrite the trainee's own
    ``logs/session-{sid8}.jsonl`` from the current transcript path (plus a
    ``.heartbeat`` stamp) so a hang after turn N leaves turns 1..N
    diagnosable, instead of only the thin per-hook summary log. Interim
    safety net between here and ``trainee-finalise.py``'s SessionEnd copy,
    which remains the final, authoritative overwrite. Soft — never raises."""
    sid = str(hook_input.get("session_id", ""))
    transcript_path = str(hook_input.get("transcript_path", "") or "")
    try:
        from lib import session_log_mirror
        wrote = session_log_mirror.mirror_session_log(
            transcript_path, repo_root, sid)
        _log(f"session log {'mirrored' if wrote else 'mirror skipped'}", sid)
    except Exception as exc:
        _log(f"session log mirror failed (soft): {exc}", sid)


def write_event(hook_input: dict) -> bool:
    """Build + upsert one telemetry point. True on a write, False on any
    soft-skip (unhandled event, config unfilled, Qdrant unreachable)."""
    from lib import qdrant_http, training_config, training_substrate

    sid = str(hook_input.get("session_id", ""))
    repo_root = os.environ.get("CLAUDE_PROJECT_DIR") or \
        str(hook_input.get("cwd", "") or "") or os.getcwd()

    if str(hook_input.get("hook_event_name", "")) == "Stop":
        _mirror_session_log(hook_input, repo_root)

    try:
        cfg = training_config.load(repo_root)
    except training_config.TrainingConfigError as exc:
        _log(f"telemetry skipped: {exc}", sid)
        return False
    if not training_config.is_configured(cfg):
        _log("telemetry skipped: training-config.yaml not filled yet", sid)
        return False

    built = build_event(hook_input)
    if built is None:
        return False
    payload, point_id = built
    point = training_substrate.build_telemetry_point(
        payload, trainee=cfg["trainee"], point_id=point_id)
    try:
        qdrant_http.upsert_points(
            [point], collection=cfg["telemetry_collection"],
            timeout=QDRANT_TIMEOUT, **training_config.qdrant_kwargs(cfg))
    except Exception as exc:
        _log(f"telemetry write failed (soft): {exc}", sid)
        return False
    _log(f"telemetry point written: {payload['event_type']} {point_id}", sid)
    return True


def main() -> int:
    try:
        hook_input = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    try:
        write_event(hook_input)
    except Exception as exc:
        _log(f"telemetry hook error (soft): {exc}",
             str(hook_input.get("session_id", "")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # fail-soft: telemetry must never wall a session (R2-4)
