#!/usr/bin/env python3
"""
trainee-session-branch.py — SessionStart hook: put the trainee clone on a session
branch (PROJ-011/T-021 R-2, CC-351).

A trainee's LLM never runs git ceremony (that was the manual
``git checkout -b trainee/{name}`` in the old Alex CLAUDE.md, stripped in
trainingTeam TT-002). This hook owns it: on SessionStart it creates/checks out
``session/{YYYY-MM-DD}-{sid8}`` off ``main``, guarded by the T-045
:mod:`session_guard` preflight (clean tree + on main + ff; a crash-leftover session
branch is recovered via the finalise ledger or the launch HALTs with guidance).

Trainee repos are single-repo — no multi-clone loop; the one clone the session runs
in is the whole surface. Wired **only** in the training-template ``settings.json``
(apex agents branch via the launch-session skill, not a hook).

Behaviour:
  * Already on this session's branch (``…-{sid8}``) → **no-op** (a resume, or this
    hook already ran). Idempotent.
  * ``preflight`` ``ready``/``recovered`` → create + checkout the session branch off
    the ff'd ``main``.
  * ``preflight`` ``halt`` → do NOT branch; surface the recovery message as
    SessionStart context so the trainee (and the review ritual) can act. The clone is
    left exactly as found.

Always exits 0 (a SessionStart hook must not break startup). Reads stdin JSON:
``session_id``, ``cwd``. Tested by tests/proj039/test_trainee_session_branch.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def session_branch_name(session_id: str, *, date: str | None = None) -> str:
    """``session/{YYYY-MM-DD}-{sid8}`` — the session-branch grammar session_guard
    recognises (safe to reap once its work is pushed)."""
    d = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sid8 = (session_id or "unknown")[:8]
    return f"session/{d}-{sid8}"


def _emit_context(text: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }))


def ensure_session_branch(cwd: str, session_id: str,
                          *, date: str | None = None) -> dict:
    """Put ``cwd`` on this session's branch. Returns
    ``{"action", "branch", "message"}`` where ``action`` is one of
    ``noop`` | ``created`` | ``halt`` | ``error``."""
    from lib import session_guard

    target = session_branch_name(session_id, date=date)
    current = session_guard.current_branch(cwd)

    # Resume / re-fire: already on our own session branch — nothing to do.
    if current == target:
        return {"action": "noop", "branch": target,
                "message": f"already on session branch '{target}'"}

    guard = session_guard.preflight(cwd)
    if guard["decision"] not in ("ready", "recovered"):
        return {"action": "halt", "branch": guard.get("branch", ""),
                "message": guard["message"]}

    # preflight left the clone on a ff'd main — branch away.
    cp = subprocess.run(
        ["git", "-C", str(cwd), "checkout", "-b", target],
        capture_output=True, text=True, check=False,
    )
    if cp.returncode != 0:
        # A pre-existing local branch of the same name (e.g. re-run same day+sid):
        # just check it out rather than fail.
        co = subprocess.run(
            ["git", "-C", str(cwd), "checkout", target],
            capture_output=True, text=True, check=False,
        )
        if co.returncode == 0:
            return {"action": "noop", "branch": target,
                    "message": f"checked out existing session branch '{target}'"}
        return {"action": "error", "branch": target,
                "message": f"could not create session branch '{target}': "
                           f"{cp.stderr.strip()}"}
    return {"action": "created", "branch": target,
            "message": f"created session branch '{target}' off main "
                       f"({guard['reason']})"}


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        print(f"[trainee-session-branch] bad stdin: {exc}", file=sys.stderr)
        return 0

    session_id = data.get("session_id", "")
    cwd = data.get("cwd", ".")

    try:
        res = ensure_session_branch(cwd, session_id)
    except Exception as exc:  # never break startup
        print(f"[trainee-session-branch] skipped: {exc}", file=sys.stderr)
        return 0

    if res["action"] == "halt":
        _emit_context(
            "⛔ SESSION BRANCH GUARD — the clone is not ready to branch a new "
            f"session:\n{res['message']}\nResolve, then re-launch. Do NOT begin work."
        )
    elif res["action"] == "created":
        _emit_context(
            f"🌿 On session branch `{res['branch']}` (off main). Your work commits + "
            f"PRs to it automatically at session end — you never run git."
        )
    # noop / error: nothing load-bearing to surface as context.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
