"""lifecycle_mode.py — read a home/team repo's ``lifecycle_mode`` flag (PROJ-039/T-084).

The flag lives in the shipped ``.claude/tooling-manifest.json`` (written by
``sync-agent-tooling.sh``, T-055) as an operator-set field, default absent. Absent
or unreadable means ``"branch"`` — the pre-T-084 behaviour every repo has today —
so a repo is only ever moved onto the new trunk-mode finalise/pull path by an
explicit migration (T-086) writing the field, never by this reader's default.

Stdlib-only, no ``lib/`` imports — same closure-leaf discipline as
:mod:`runtime_log` (home-repo ``python3`` has no ``requests``, and this module is
called from both the finalise hook and (indirectly, via a bash JSON probe) from
``session-start.sh``, so it must stay dependency-free).
"""

from __future__ import annotations

import json
from pathlib import Path

BRANCH = "branch"
TRUNK = "trunk"


def read_lifecycle_mode(repo_dir: str) -> str:
    """Return ``"trunk"`` or ``"branch"`` for ``repo_dir`` (default ``"branch"``).

    Never raises: a missing manifest, a missing key, or a corrupt file all read as
    ``"branch"`` — the safe default that keeps existing behaviour for every repo
    until it is explicitly migrated."""
    try:
        manifest = json.loads(
            (Path(repo_dir) / ".claude" / "tooling-manifest.json")
            .read_text(encoding="utf-8")
        )
        mode = manifest.get("lifecycle_mode")
        return TRUNK if mode == TRUNK else BRANCH
    except Exception:
        return BRANCH


def is_trunk_mode(repo_dir: str) -> bool:
    """True if ``repo_dir`` is flagged ``lifecycle_mode: trunk``."""
    return read_lifecycle_mode(repo_dir) == TRUNK
