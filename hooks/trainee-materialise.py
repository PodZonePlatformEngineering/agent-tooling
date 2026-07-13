#!/usr/bin/env python3
"""
trainee-materialise.py — SessionStart operational-brief materialise for the
trainee runtime (PROJ-011/T-030, trainee-repo template v3).

Config-driven from the committed ``training-config.yaml`` (R2-2): reads the
repo's OPERATIONAL brief — point ``uuid5(operational_brief_id)`` in
``training_briefs`` — and, when one is ``active``, injects it as SessionStart
context and appends the runtime sid to the brief point's ``session_ids[]``.
This replaces the v2 first-prompt-brief path for the trainee role: the brief
id lives in the config file, not the first prompt.

OFFLINE-FIRST (R2-4): the briefing files (AGENTS.md / trainee-brief.md), not
this hook, carry the agent's operating knowledge. Qdrant unreachable, config
unfilled, or no active brief are all NORMAL — the hook logs, says at most one
quiet line, and exits 0. It never blocks a session and never touches any
fleet collection (the config loader cannot name one — R2-3).

``--ack <revision>`` mode (agent-invoked, in-session, after the trainee
approves the applied instruction — R2-1): writes the ``message_type: ack``
write-back point to ``training_briefs`` so the trainer sees the instruction
landed. Idempotent per (session, revision).

Always exits 0. Reads stdin JSON: ``session_id``, ``cwd``.
Tested by tests/proj011/test_trainee_routing.py.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

QDRANT_TIMEOUT = 6.0  # SessionStart must stay snappy — offline is normal


def _log(message: str, session_id: str = "") -> None:
    try:
        from lib.runtime_log import log_library
        log_library("training", message, session_id=session_id or None)
    except Exception:
        pass


def _emit_context(text: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }))


def _repo_root(cwd: str) -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR") or cwd or os.getcwd()


def fetch_operational_brief(cfg: dict, *, session_id: str) -> dict | None:
    """The active operational brief payload, or ``None`` (absent/paused/
    retired). Appends the runtime sid to ``session_ids[]`` on a hit."""
    from lib import qdrant_http, training_config, training_substrate

    brief_id = cfg["operational_brief_id"]
    point_id = training_substrate.brief_point_id(brief_id)
    kwargs = training_config.qdrant_kwargs(cfg)
    payload = qdrant_http.get_point(
        point_id, collection=cfg["briefs_collection"],
        timeout=QDRANT_TIMEOUT, **kwargs)
    if not payload or payload.get("status") != "active":
        return None
    sids = list(payload.get("session_ids") or [])
    if session_id and session_id not in sids:
        sids.append(session_id)
        try:
            qdrant_http.set_payload(
                {"session_ids": sids,
                 "updated_at": training_substrate.now_iso()},
                [point_id], collection=cfg["briefs_collection"],
                timeout=QDRANT_TIMEOUT, **kwargs)
        except Exception as exc:
            _log(f"session_ids append failed (soft): {exc}", session_id)
    return payload


def write_ack(cfg: dict, *, session_id: str, revision: int) -> dict:
    """The R2-1 round-trip close: an ``ack`` message point on the operational
    thread. seq=revision → idempotent per (session, revision)."""
    from lib import qdrant_http, training_config, training_substrate

    point = training_substrate.build_message_point(
        brief_id=cfg["operational_brief_id"], trainee=cfg["trainee"],
        session_id=session_id, seq=revision, message_type="ack",
        channel="operational", ack_of_revision=revision,
        summary=f"operational brief revision {revision} applied",
        body=f"Applied with in-session trainee approval (revision {revision}).")
    return qdrant_http.upsert_points(
        [point], collection=cfg["briefs_collection"],
        timeout=QDRANT_TIMEOUT, **training_config.qdrant_kwargs(cfg))


def _load_config(repo_root: str, session_id: str = ""):
    """Config, or None with the reason logged — every caller degrades soft."""
    from lib import training_config
    try:
        cfg = training_config.load(repo_root)
    except training_config.TrainingConfigError as exc:
        _log(f"materialise skipped: {exc}", session_id)
        return None
    if not training_config.is_configured(cfg):
        _log("materialise skipped: training-config.yaml not filled yet "
             "(take-on Phase A pending)", session_id)
        return None
    return cfg


def _ack_main(revision_arg: str) -> int:
    try:
        revision = int(revision_arg)
    except ValueError:
        print(f"--ack needs an integer revision, got {revision_arg!r}",
              file=sys.stderr)
        return 1
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    cfg = _load_config(_repo_root(""), session_id)
    if cfg is None:
        print("training-config.yaml missing/unfilled — cannot ack", file=sys.stderr)
        return 1
    try:
        write_ack(cfg, session_id=session_id or "no-session", revision=revision)
    except Exception as exc:
        print(f"ack write failed: {exc}", file=sys.stderr)
        return 1
    print(f"ack recorded for operational brief revision {revision}")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--ack":
        return _ack_main(argv[1] if len(argv) > 1 else "")

    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}
    session_id = str(data.get("session_id", ""))
    cwd = str(data.get("cwd", "") or "")

    cfg = _load_config(_repo_root(cwd), session_id)
    if cfg is None:
        return 0

    try:
        brief = fetch_operational_brief(cfg, session_id=session_id)
    except Exception as exc:
        # Qdrant unreachable / auth drift — offline-first, one quiet line.
        _log(f"operational brief unavailable (soft): {exc}", session_id)
        _emit_context(
            "ℹ️  Operational brief channel unavailable (offline or not "
            "configured) — proceed with AGENTS.md + trainee-brief.md alone; "
            "that is normal, not an error.")
        return 0

    if brief is None:
        _log("no active operational brief — offline-first proceed", session_id)
        return 0

    revision = brief.get("revision", 1)
    _log(f"operational brief materialised: {cfg['operational_brief_id']} "
         f"revision {revision}", session_id)
    _emit_context(
        f"📋 OPERATIONAL BRIEF (revision {revision}, updated "
        f"{brief.get('updated_at', 'unknown')}) — "
        f"{brief.get('summary') or 'repo update instruction'}\n\n"
        f"{brief.get('body', '')}\n\n"
        "Apply the above WITH the trainee's in-session approval before "
        "continuing the programme. Once applied, acknowledge with:\n"
        f"  python3 .claude/hooks/trainee-materialise.py --ack {revision}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # fail-soft: never wall a trainee session (R2-4)
