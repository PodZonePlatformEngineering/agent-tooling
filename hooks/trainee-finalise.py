#!/usr/bin/env python3
"""
trainee-finalise.py — the trainee runtime's SessionEnd finalise
(PROJ-011/T-030, trainee-repo template v3).

The trainee sibling of session-end-finalise.py, deliberately smaller. Steps
(finalise-ledger tracked, idempotent, re-runnable):

  1. ``log_copy``   — copy the session transcript into ``logs/`` so it rides
                      the session PR (R-14: in-repo, trainee's own repo).
  2. ``telemetry``  — one ``SessionEnd`` point to ``training_session_telemetry``
                      via the committed training-config.yaml routing.
  3. ``session_pr`` — commit the live working tree to the session branch,
                      push, open the R-3 review PR (lib.session_finalise.
                      author_trainee_session_pr; trainee runs no git).

What it must NEVER do (R2-5): push to the fleet agent-telemetry repo, prune
fleet CST, write any fleet collection, or touch the apex clone. Trainee
observability is training_session_telemetry points; the durable artefacts
are the session PR + committed logs in the trainee's OWN repo.

``--guard`` mode (wired early on SessionStart): recover a truncated close —
re-run the missing steps for any unfinalised ledger entry whose cwd is this
repo, then mark it complete. A crash between commit and PR therefore heals on
the next launch instead of stranding a session branch.

Degrades soft everywhere (R2-4): offline Qdrant skips step 2 with a log line;
a missing gh leaves work committed + pushed. Always exits 0. Reads stdin
JSON: ``session_id``, ``transcript_path``, ``cwd``, ``reason``.
Tested by tests/proj011/test_trainee_routing.py (routing/offline) and
tests/proj039/test_trainee_finalise.py (PR body + authoring).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

QDRANT_TIMEOUT = 6.0
STEPS = ("log_copy", "telemetry", "session_pr")


def _log(message: str, session_id: str = "") -> None:
    try:
        from lib.runtime_log import log_library
        log_library("training", message, session_id=session_id or None)
    except Exception:
        pass


def _repo_root(cwd: str = "") -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR") or cwd or os.getcwd()


def _copy_session_log(transcript_path: str, repo_dir: str, session_id: str) -> bool:
    """R-14: the session log rides the session PR in the trainee's own repo —
    there is no fleet telemetry remote to push to. Best-effort."""
    if not (transcript_path and repo_dir and os.path.isfile(transcript_path)):
        return False
    sid8 = (session_id or "session")[:8]
    logs_dir = os.path.join(repo_dir, "logs")
    try:
        os.makedirs(logs_dir, exist_ok=True)
        shutil.copyfile(transcript_path,
                        os.path.join(logs_dir, f"session-{sid8}.jsonl"))
        return True
    except Exception:
        return False


def _write_close_telemetry(repo_dir: str, session_id: str, reason: str) -> bool:
    """One SessionEnd point to the training telemetry collection. Soft."""
    from lib import qdrant_http, training_config, training_substrate
    try:
        cfg = training_config.load(repo_dir)
    except training_config.TrainingConfigError as exc:
        _log(f"close telemetry skipped: {exc}", session_id)
        return False
    if not training_config.is_configured(cfg):
        _log("close telemetry skipped: training-config.yaml not filled yet",
             session_id)
        return False
    payload = {
        "event_type": "SessionEnd",
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cwd": repo_dir,
        "reason": reason,
    }
    point = training_substrate.build_telemetry_point(
        payload, trainee=cfg["trainee"],
        point_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"training-end/{session_id}")))
    try:
        qdrant_http.upsert_points(
            [point], collection=cfg["telemetry_collection"],
            timeout=QDRANT_TIMEOUT, **training_config.qdrant_kwargs(cfg))
        return True
    except Exception as exc:
        _log(f"close telemetry write failed (soft): {exc}", session_id)
        return False


def _session_summary(transcript_path: str) -> str:
    """The last assistant message as the PR summary — best-effort."""
    try:
        from lib import stop_telemetry
        _records, message = stop_telemetry.read_transcript_for_stop(transcript_path)
        return (message.get("text") or "").strip()
    except Exception:
        return ""


def _author_session_pr(repo_dir: str, session_id: str,
                       transcript_path: str = "") -> dict:
    from lib import session_finalise
    summary = _session_summary(transcript_path)
    return session_finalise.author_trainee_session_pr(
        {"work_item": "training session"},
        session_id=session_id, repo_dir=repo_dir,
        summary_override=summary or None)


def finalise(session_id: str, transcript_path: str, repo_dir: str,
             reason: str = "") -> None:
    """The ordered, ledger-tracked close. Each step records ok/failed; a step
    that already recorded ok is skipped on a re-run (guard recovery)."""
    from lib import finalise_ledger as ledger

    ledger.begin(session_id, transcript_path=transcript_path, cwd=repo_dir)

    if ledger.step_status(session_id, "log_copy") != "ok":
        wrote = _copy_session_log(transcript_path, repo_dir, session_id)
        ledger.record_step(session_id, "log_copy", "ok" if wrote else "skipped")
        _log(f"session log {'copied to logs/' if wrote else 'copy skipped'}",
             session_id)

    if ledger.step_status(session_id, "telemetry") != "ok":
        wrote = _write_close_telemetry(repo_dir, session_id, reason)
        ledger.record_step(session_id, "telemetry", "ok" if wrote else "skipped")

    if ledger.step_status(session_id, "session_pr") != "ok":
        res = _author_session_pr(repo_dir, session_id, transcript_path)
        ledger.record_step(session_id, "session_pr",
                           "ok" if res.get("ok") else "failed")
        _log(f"trainee session PR [{res.get('disposition')}]: "
             f"ok={res.get('ok')} pr={res.get('pr_url') or '-'} "
             f"({res.get('reason')})", session_id)

    # Complete only once the load-bearing step (the session PR) landed —
    # otherwise leave the entry for --guard to retry, capped by the ledger's
    # attempt counter so a permanently broken close cannot loop forever.
    if ledger.step_status(session_id, "session_pr") == "ok" \
            or ledger.attempts(session_id) >= 3:
        ledger.complete(session_id)


def guard(repo_dir: str) -> None:
    """SessionStart recovery: finish any truncated close recorded for THIS
    repo, so a crash between steps never strands a session branch."""
    from lib import finalise_ledger as ledger

    recovered = []
    for sid, entry in ledger.unfinalised():
        entry_cwd = os.path.realpath(entry.get("cwd") or "")
        if entry_cwd != os.path.realpath(repo_dir):
            continue  # another repo's business
        _log(f"guard: recovering truncated close for {sid[:8]}", sid)
        finalise(sid, entry.get("transcript_path", ""), repo_dir,
                 reason="guard-recovery")
        recovered.append(sid[:8])
    if recovered:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext":
                    "♻️  Recovered a previous session's truncated close "
                    f"({', '.join(recovered)}) — its work is committed and its "
                    "review PR raised (or queued). Check open PRs during the "
                    "review ritual.",
            }
        }))


def main() -> int:
    if "--guard" in sys.argv[1:]:
        try:
            sys.stdin.read()  # drain the SessionStart JSON
        except Exception:
            pass
        try:
            guard(_repo_root())
        except Exception as exc:
            _log(f"guard skipped (soft): {exc}")
        return 0

    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}
    session_id = str(data.get("session_id", ""))
    transcript_path = str(data.get("transcript_path", "") or "")
    repo_dir = _repo_root(str(data.get("cwd", "") or ""))
    if not session_id:
        _log("finalise skipped: no session_id on stdin")
        return 0
    try:
        finalise(session_id, transcript_path, repo_dir,
                 reason=str(data.get("reason", "") or ""))
    except Exception as exc:
        _log(f"finalise error (soft): {exc}", session_id)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # fail-soft: teardown must never trap a trainee (R2-4)
