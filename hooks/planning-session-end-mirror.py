#!/usr/bin/env python3
"""
planning-session-end-mirror.py — ``SessionEnd`` hook, the authoritative half
of the ``.planning/`` mirror (PROJ-029 plannerapi BCP mechanism, spec
§6.2.2/§6.2.3, build item 5/6).

Runs the full materialise pass unconditionally, plus the Qdrant briefs
mirror (spec §6.2.3 — coarse-frequency by design, briefs are comparatively
rare) — the same T-122 precedent as ``trainee-finalise.py``'s ``SessionEnd``
copy: a final, authoritative overwrite that fixes up whatever the interim
``PostToolUse`` pass (``planning-postwrite-mirror.py``) missed or left
stale.

Wired standalone alongside (not folded into) ``session-end-finalise.py`` —
this repo's existing finalise hook is about the *session's own* result/PR,
a separate concern from the *planning-schema* mirror. Runs after it in
``settings.json``'s hook list.

Degrades soft — never raises, and in particular never lets a Qdrant or Neon
outage break the ``SessionEnd`` step, matching this fleet's standing
degrade-soft/log-don't-break-the-session contract (every other
``SessionEnd`` hook in this fleet follows it; the BCP mechanism especially
must not be the thing that breaks a session during the exact outage it
exists to survive).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def log(msg: str) -> None:
    print(f"[planning-session-end-mirror] {msg}", file=sys.stderr)


def main() -> int:
    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw) if raw.strip() else {}
    except Exception:
        hook_input = {}

    repo_dir = os.environ.get("CLAUDE_PROJECT_DIR") or str(hook_input.get("cwd") or os.getcwd())

    try:
        from lib import planning_mirror
    except Exception as exc:
        log(f"could not import lib.planning_mirror (degrading soft): {exc}")
        return 0

    try:
        result = planning_mirror.materialise(repo_dir)
        if result["ok"]:
            log(f"authoritative materialise ok: {result['counts']}")
        else:
            log(f"authoritative materialise failed (degrading soft): {result['error']}")
    except Exception as exc:
        log(f"materialise raised unexpectedly (degrading soft): {exc}")

    try:
        briefs_result = planning_mirror.mirror_briefs(repo_dir)
        if briefs_result["ok"]:
            log(f"briefs mirror ok: {briefs_result['count']} written")
        else:
            log(f"briefs mirror failed (degrading soft): {briefs_result['error']}")
    except Exception as exc:
        log(f"briefs mirror raised unexpectedly (degrading soft): {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
