#!/usr/bin/env python3
"""Trainee context-containment read guard (PROJ-011/T-025 R-9).

PreToolUse hook (trainee runtime): session context must come only from **this** repo
plus the `.workspace/` task repos the brief materialises. In test sessions, documents
from outside the training repo leaked into context and had to be moved into the trainee
dir by hand. This guard catches a Read/Glob/Grep whose target is **outside the repo
root** and, by default, **reports** it (advisory, non-blocking) — the trainee is told to
copy the material into `{Trainee}/sourceDocs/` first.

Mode is operator-selectable via `PODZONE_READ_GUARD`:
  * unset / `report` (default) — advise, allow the read (operator decides block later).
  * `block` — deny the read with the same guidance.

Fail-soft: any parsing/error path allows the tool (never wall a session over the guard).
"""
from __future__ import annotations

import json
import os
import sys

GUARDED_TOOLS = {"Read", "Glob", "Grep"}


def _repo_root() -> str:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.realpath(root)


def _target_path(tool_name: str, tool_input: dict) -> str | None:
    if tool_name == "Read":
        return tool_input.get("file_path")
    # Glob/Grep: `path` scopes the search; absent means the repo root (in-repo → allow).
    return tool_input.get("path")


def _is_outside(target: str, root: str) -> bool:
    if not target:
        return False
    # Only absolute out-of-tree targets are out-of-repo; relative paths resolve under
    # the repo and are fine. The materialised task repos live under `<root>/.workspace/`,
    # so they are inside the root and never flagged.
    cand = os.path.realpath(os.path.join(root, target)) if not os.path.isabs(target) \
        else os.path.realpath(target)
    return os.path.commonpath([cand, root]) != root


def _advice(target: str) -> str:
    return (
        f"Context containment (R-9): `{target}` is outside this training repo. Session "
        "context should come only from this repo (+ the .workspace/ task repos your "
        "brief materialised). If you need this material, copy it into "
        "`{Trainee}/sourceDocs/` first, then read it from there."
    )


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0  # unparseable → allow

    tool_name = data.get("tool_name", "")
    if tool_name not in GUARDED_TOOLS:
        return 0

    tool_input = data.get("tool_input", {}) or {}
    target = _target_path(tool_name, tool_input)
    try:
        outside = _is_outside(target, _repo_root())
    except Exception:
        return 0

    if not outside:
        return 0

    mode = os.environ.get("PODZONE_READ_GUARD", "report").strip().lower()
    advice = _advice(target)
    if mode == "block":
        # PreToolUse deny: exit 2 with the reason on stderr is the block contract.
        print(advice, file=sys.stderr)
        return 2
    # report mode (default): advise + allow.
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": advice,
        }
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # fail-soft: never wall a session over the read guard
