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

Tested by DT-010 (success) and DT-011 (failure-halt).

Reads stdin JSON: ``session_id``, ``cwd``, ``transcript_path``. Always exits 0
(a SessionStart hook must not break startup); the halt is carried in the sentinel
and the additionalContext, not in the exit code.
"""

from __future__ import annotations

import json
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
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "brief.md").write_text(brief["text"], encoding="utf-8")
    (workspace / "tasks.json").write_text(json.dumps(tasks, indent=2), encoding="utf-8")
    (workspace / "identity.json").write_text(json.dumps({
        "agent": point_agent,
        "work_item": point.get("work_item"),
        "session_id": session_id,
    }, indent=2), encoding="utf-8")

    # 5. Success sentinel.
    status = {
        "ok": True,
        "ts": _now(),
        "source": "qdrant",
        "counts": {"brief": 1, "tasks": len(tasks)},
    }
    _write_status(workspace, status)
    return status


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        print(f"[session-materialise] bad stdin: {exc}", file=sys.stderr)
        return 0

    session_id = data.get("session_id", "")
    cwd = data.get("cwd", ".")

    # Best-effort identity (the session point is authoritative for agent/work_item).
    agent = "unknown"
    work_item = None
    try:
        sys.path.insert(0, str(REPO_ROOT / "hooks"))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "session_context", str(REPO_ROOT / "hooks" / "session-context.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore
        agent, _role, _scope = mod.resolve_agent(cwd)
    except Exception:
        pass

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
