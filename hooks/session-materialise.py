#!/usr/bin/env python3
"""
session-materialise.py — SessionStart materialise + halt (PROJ-039 § 2.2,
R-005/R-006/AC-010).

Materialises the agent's working context from the cloud `session_substrate`
collection into ``<cwd>/.workspace/`` (brief.md, tasks.json, identity.json) and
writes a ``.materialise-status.json`` sentinel. This runs on the same hook
env-propagation path the write path distrusts (C-004), so the **failure mode is
load-bearing**: on Qdrant unreachable / empty brief / API-key not propagated it
writes ``ok:false`` with a reason and emits an explicit HALT, and it never
fabricates a `.workspace` from a stale team-repo read (SD-3-002).

The halt is a two-part contract: this hook writes the sentinel; the agent's
orientation procedure (session-start skill / READMEFIRST) MUST read
``.materialise-status.json`` first and refuse to proceed when it is absent or
``ok:false``. A SessionStart hook cannot itself block the agent.

Tested by DT-010 (success) and DT-011 (failure-halt). A team-lead session with no
`BRIEF_ID` is a distinct third case (F15, PROJ-039/T-078): not a failure, since team
leads don't generally start against a worker-style tasking brief — it skips the
legacy path with an informational note instead of HALTing.

Reads stdin JSON: ``session_id``, ``cwd``, ``transcript_path``. Always exits 0
(a SessionStart hook must not break startup); the halt is carried in the sentinel
and the additionalContext, not in the exit code.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

HALT_MESSAGE = (
    "⛔ MATERIALISE FAILED — .workspace is empty/stale. Do NOT begin tasking "
    "work. Resolve Qdrant reachability / API-key propagation (PROJ-033/T-016) "
    "and re-run session-start."
)


def resumed_after_finalise(session_id: str, cwd: str) -> dict | None:
    """T-075 F14 resume-guard (plan D-3, operator-confirmed): detect a session
    resumed AFTER its finalise already completed.

    The Athena T-065 failure (sid ``0e0d37f0``): a CLI restart fired the
    SessionEnd finalise (result PR raised, session branch deleted, lock
    released, clone returned to ``main``) — then the resumed conversation came
    back on the SAME sid, on ``main``, unbranched and unlocked, and post-resume
    work committed straight to ``main`` outside the guarded lifecycle.

    Halt condition — ALL of: the finalise ledger records ``complete: true`` for
    the incoming sid, AND the (resolved) home clone is on ``main``, AND no live
    lock is held for it. A resumed sid still on its live session branch with
    its lock held (a normal mid-session restart BEFORE finalise) returns
    ``None`` and passes through unchanged.

    Returns the halt status dict (``ok: false``, reason
    ``resumed-after-finalise``, re-arm instructions) or ``None`` to proceed."""
    if not session_id:
        return None
    try:
        from lib import finalise_ledger, session_guard
    except Exception:
        return None  # guard is best-effort; materialise's own paths still run
    try:
        if not finalise_ledger.is_complete(session_id):
            return None
        home_repo = session_guard.resolve_home_repo(cwd)
        if not home_repo:
            return None
        if session_guard.current_branch(home_repo) != "main":
            return None  # live session branch — mid-session restart, pass through
        if session_guard.lock_holder(home_repo) is not None:
            return None  # a live lock is held — not the after-finalise state
    except Exception:
        return None
    brief_id = os.environ.get("BRIEF_ID", "").strip()
    rearm_branch = f"session/{{agent}}-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-{{slug}}"
    return {
        "ok": False,
        "reason": "resumed-after-finalise",
        "session_id": session_id,
        "ts": _now(),
        "rearm": {
            "branch": rearm_branch,
            "lock_sid": brief_id or session_id,
            "steps": [
                f"git -C {home_repo} checkout -b {rearm_branch}  # re-branch, keyed to the same brief",
                f"python3 ~/workspace/agent-tooling/lib/session_guard.py lock "
                f"--repo {home_repo} --sid '{brief_id or session_id}'  # re-lock",
                "then continue the brief's work on the new branch",
            ],
        },
    }


def _resume_halt_message(status: dict) -> str:
    steps = "\n".join(f"  {i}. {s}" for i, s in
                      enumerate(status.get("rearm", {}).get("steps", []), 1))
    return (
        "⛔ RESUMED-AFTER-FINALISE — this session's finalise already completed "
        "(result PR raised, session branch deleted, lock released, clone "
        "returned to main). This resumed conversation is on `main`, UNBRANCHED "
        "and UNLOCKED — do NOT accept or commit any work in this state. "
        "Re-arm the guarded lifecycle first:\n"
        f"{steps}"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit_context(text: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }))


def _write_status(workspace: Path, status: dict) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".materialise-status.json").write_text(
        json.dumps(status, indent=2), encoding="utf-8"
    )


def _materialise_files(workspace: Path, *, brief_text: str, tasks: list,
                       agent: str, work_item: str | None, session_id: str,
                       brief_id: str | None = None,
                       tooling_update: str | None = None) -> None:
    """Write the three `.workspace` files from resolved substrate content.

    Shared by the legacy (session-point-keyed) and brief-first paths so the
    on-disk shape the agent orients against is byte-identical between them.

    ``tooling_update`` (PROJ-039/T-056) is surfaced into ``identity.json``
    purely for visibility/audit — the resident ``update-tooling.py`` is
    triggered by the launch-time ``TOOLING_UPDATE`` env var, not this file, so
    a materialise ordered after (or never run, e.g. legacy path) never blocks
    the self-update.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "brief.md").write_text(brief_text, encoding="utf-8")
    (workspace / "tasks.json").write_text(json.dumps(tasks, indent=2), encoding="utf-8")
    identity = {"agent": agent, "work_item": work_item, "session_id": session_id}
    if brief_id is not None:
        identity["brief_id"] = brief_id
    if tooling_update:
        identity["tooling_update"] = tooling_update
    (workspace / "identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")


def materialise_brief_first(session_id: str, cwd: str, brief_id: str) -> dict:
    """Brief-first materialise (PROJ-039/T-043): resolve a first-class `briefs`
    point by ``brief_id``, stand up the session point keyed by the RUNTIME
    ``session_id``, and materialise `.workspace` from the brief body.

    The flow that kills the pinned-sid ceremony (brief § Flow step 3):
      1. resolve the brief; refuse if absent/empty or not past the execution gate
         (``approved`` — ADR-008 D5). No stale fabrication on failure (SD-3-002).
      2. create the session point under the runtime sid with a ``brief_id`` ref
         (creation only; skipped idempotently if the point already exists, e.g. a
         resume — never a re-upsert that would null the `response` vector).
      3. append the runtime sid to ``briefs.session_ids[]`` (idempotent) — the
         reverse link; two distinct sessions accumulate two ids.
      4. advance the brief ``approved → in_progress`` on first materialise.

    Returns the status dict; ``{"ok": False, "reason": …}`` on any failure.
    """
    workspace = Path(cwd) / ".workspace"
    try:
        from lib import session_substrate, brief_substrate
    except Exception as exc:  # pragma: no cover — import failure is fatal
        status = {"ok": False, "reason": f"lib-import: {exc}", "ts": _now()}
        _write_status(workspace, status)
        return status

    # 1. Resolve the brief point.
    try:
        brief = brief_substrate.get_brief(brief_id)
    except Exception as exc:
        status = {"ok": False, "reason": f"qdrant-unreachable: {exc}", "ts": _now()}
        _write_status(workspace, status)
        return status
    if not brief or not (brief.get("body") or "").strip():
        status = {"ok": False, "reason": f"brief-not-found: {brief_id}", "ts": _now()}
        _write_status(workspace, status)
        return status
    if brief.get("status") not in brief_substrate.EXECUTABLE_STATUSES:
        status = {"ok": False, "reason": f"brief-not-approved: {brief_id} "
                  f"(status={brief.get('status')})", "ts": _now()}
        _write_status(workspace, status)
        return status

    body = brief["body"]
    agent = brief.get("assignee", "unknown")
    work_items = brief.get("work_items") or []
    work_item = work_items[0] if work_items else None

    # 2. Create the session point under the runtime sid (creation only; skip on
    #    resume so a re-materialise never re-upserts an existing point).
    try:
        existing = session_substrate.get_session_point(session_id)
        if existing is None:
            session_substrate.create_session_point(
                session_id=session_id, agent=agent,
                work_item=work_item or brief_id, brief_text=body,
                brief_id=brief_id,
            )
    except Exception as exc:
        status = {"ok": False, "reason": f"session-point-create: {exc}", "ts": _now()}
        _write_status(workspace, status)
        return status

    # 3. Append the runtime sid to the brief's reverse link (idempotent).
    try:
        brief_substrate.append_session_id(brief_id, session_id)
    except Exception as exc:
        status = {"ok": False, "reason": f"append-session-id: {exc}", "ts": _now()}
        _write_status(workspace, status)
        return status

    # 4. Advance approved → in_progress on first materialise (idempotent).
    try:
        brief_substrate.start_brief(brief_id)
    except Exception as exc:
        status = {"ok": False, "reason": f"brief-start: {exc}", "ts": _now()}
        _write_status(workspace, status)
        return status

    # 5. Active work items for the agent (non-fatal — the brief is the gate).
    try:
        tasks = session_substrate.active_work_items(agent)
    except Exception:
        tasks = []

    # 6. Materialise files + success sentinel.
    _materialise_files(workspace, brief_text=body, tasks=tasks, agent=agent,
                       work_item=work_item, session_id=session_id, brief_id=brief_id,
                       tooling_update=brief.get("tooling_update"))
    status = {
        "ok": True,
        "ts": _now(),
        "source": "brief",
        "brief_id": brief_id,
        "counts": {"brief": 1, "tasks": len(tasks)},
    }
    _write_status(workspace, status)
    return status


def materialise(session_id: str, cwd: str, *, agent: str = "unknown",
                work_item: str | None = None) -> dict:
    """Materialise `.workspace` from session_substrate. Returns the status dict.

    On any failure returns ``{"ok": False, "reason": …}`` and writes the sentinel
    WITHOUT populating brief/tasks/identity (no stale fabrication, SD-3-002).
    """
    workspace = Path(cwd) / ".workspace"
    try:
        from lib import session_substrate
    except Exception as exc:  # pragma: no cover — import failure is fatal
        status = {"ok": False, "reason": f"lib-import: {exc}", "ts": _now()}
        _write_status(workspace, status)
        return status

    # 2. Resolve the session point (by session_id, or pre-dispatch by work_item).
    try:
        point = session_substrate.get_session_point(session_id)
        if point is None and agent != "unknown" and work_item:
            resolved = session_substrate.find_session_id_by_work_item(agent, work_item)
            if resolved:
                point = session_substrate.get_session_point(resolved)
    except Exception as exc:
        status = {"ok": False, "reason": f"qdrant-unreachable: {exc}", "ts": _now()}
        _write_status(workspace, status)
        return status

    if not point or not (point.get("brief") or {}).get("text"):
        status = {"ok": False, "reason": "empty-brief", "ts": _now()}
        _write_status(workspace, status)
        return status

    brief = point["brief"]
    point_agent = point.get("agent", agent)

    # 3. Active work items for the agent.
    try:
        tasks = session_substrate.active_work_items(point_agent)
    except Exception:
        tasks = []  # tasks are non-fatal; the brief is the gating context

    # 4. Materialise files.
    _materialise_files(workspace, brief_text=brief["text"], tasks=tasks,
                       agent=point_agent, work_item=point.get("work_item"),
                       session_id=session_id, brief_id=point.get("brief_id"))

    # 5. Success sentinel.
    status = {
        "ok": True,
        "ts": _now(),
        "source": "qdrant",
        "counts": {"brief": 1, "tasks": len(tasks)},
    }
    _write_status(workspace, status)
    return status


IDENTITY_HALT_MESSAGE = (
    "⛔ IDENTITY UNRESOLVED — this home repo's identity YAML "
    "(workspaces/identity/*.identity.yaml) did not resolve to an (agent, role). "
    "Do NOT begin tasking work, and do NOT chase Qdrant reachability / API-key "
    "propagation — this is a repo-local identity problem (PROJ-039/T-099). "
    "Fix the YAML (agent + role_class, no placeholders), then re-run "
    "session-start."
)


def _resolve_agent_and_role(cwd: str) -> tuple[str, str]:
    """(agent, role) from the home repo's identity YAML — the single source
    (PROJ-039/T-099, CC-409). Replaces the legacy `session-context.py`
    spec-load chain, which SUBSTRATE_BASE never shipped to home repos, so every
    resolution decayed to ("unknown", "unknown") — masking the F15 team-lead
    skip and misdirecting the 2026-07-11 home-podzone-hermes halt to
    PROJ-033/T-016.

    Raises `lib.agent_identity.IdentityUnresolved` — fail loud, never
    "unknown". Split out from `main()` so tests can monkeypatch it directly.
    """
    from lib import agent_identity
    agent, role, _scope = agent_identity.resolve(cwd)
    return agent, role


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        print(f"[session-materialise] bad stdin: {exc}", file=sys.stderr)
        return 0

    session_id = data.get("session_id", "")
    cwd = data.get("cwd", ".")

    # Resume-guard (PROJ-039/T-075 F14): a sid whose finalise already completed
    # must never accept work unbranched on main — HALT before EITHER materialise
    # path can overwrite the sentinel with ok:true. A mid-session restart (live
    # session branch + lock held) passes through to the normal paths below.
    halt = resumed_after_finalise(session_id, cwd)
    if halt is not None:
        _write_status(Path(cwd) / ".workspace", halt)
        _emit_context(_resume_halt_message(halt))
        return 0

    # Brief-first path (PROJ-039/T-043): a BRIEF_ID env var (settings.local.json
    # env block or shell env at launch) selects the decoupled flow — resolve the
    # brief, stand up the session point under the runtime sid, append the reverse
    # link. BRIEF_ID unset → the legacy session-point-keyed path below, unchanged
    # (backwards compatible; no fleet re-wiring, coexistence per C-003).
    brief_id = os.environ.get("BRIEF_ID", "").strip()
    if brief_id:
        status = materialise_brief_first(session_id, cwd, brief_id)
        if status.get("ok"):
            counts = status.get("counts", {})
            _emit_context(
                f"✅ Session materialised from brief `{brief_id}` "
                f"(briefs collection) — .workspace populated (brief + "
                f"{counts.get('tasks', 0)} active tasks), session point keyed to "
                f"the runtime sid, sid appended to session_ids[]. "
                f"Authoritative context is .workspace/, not this digest."
            )
        else:
            _emit_context(f"{HALT_MESSAGE}\n(reason: {status.get('reason')})")
        return 0

    # Single-source identity (PROJ-039/T-099): resolved AFTER the brief-first
    # branch — a BRIEF_ID session takes its agent from the brief's assignee and
    # must never be blocked by a repo-local identity problem. On the no-BRIEF_ID
    # paths below the role drives the team-lead skip (F15) and the agent keys the
    # legacy pre-dispatch lookup, so an unresolved identity HALTs loud here with
    # its own sentinel instead of decaying to ("unknown", "unknown") and then
    # surfacing as a misleading `empty-brief` (the 2026-07-11 hermes halt).
    from lib.agent_identity import IdentityUnresolved
    work_item = None
    try:
        agent, role = _resolve_agent_and_role(cwd)
    except IdentityUnresolved as exc:
        reason = exc.reason
    except Exception as exc:  # never break SessionStart — but still fail loud
        reason = f"identity-unresolved: unexpected error: {exc}"
    else:
        reason = None
    if reason is not None:
        status = {"ok": False, "reason": reason, "ts": _now()}
        _write_status(Path(cwd) / ".workspace", status)
        _emit_context(f"{IDENTITY_HALT_MESSAGE}\n(reason: {reason})")
        return 0

    # F15 (PROJ-039/T-078): a team-lead session with no BRIEF_ID is NOT a failure —
    # team leads don't generally start against a worker-style tasking brief (that's
    # what BRIEF_ID/materialise_brief_first is for, a distinct inter-team-lead
    # dispatch — item 2 of the T-078 brief). Skip the legacy session-substrate path
    # entirely so it never fabricates/HALTs on a brief that was never expected, and
    # surface an informational note instead. Non-team-lead roles are unchanged —
    # a missing/unresolvable brief still HALTs for them.
    if role == "team-lead":
        status = {"ok": True, "ts": _now(), "reason": "team-lead-no-brief"}
        _write_status(Path(cwd) / ".workspace", status)
        _emit_context(
            "ℹ️ No BRIEF_ID set for this team-lead session — no tasking brief "
            "expected. Run orientation via the `session-start` skill."
        )
        return 0

    status = materialise(session_id, cwd, agent=agent, work_item=work_item)

    if status.get("ok"):
        counts = status.get("counts", {})
        _emit_context(
            f"✅ Session materialised from session_substrate — "
            f".workspace populated (brief + {counts.get('tasks', 0)} active tasks). "
            f"Authoritative context is .workspace/, not this digest."
        )
    else:
        _emit_context(f"{HALT_MESSAGE}\n(reason: {status.get('reason')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
