#!/usr/bin/env python3
"""
wire-update-tooling.py — settings.json SessionStart wiring for the tooling
self-updater (PROJ-039/T-069, T-065 F1).

The T-065 shakedown proved the chicken-and-egg the T-056 design left open:
update-tooling.py ships manifest-managed to every home repo, but the wiring
that invokes it lives in the committed ``.claude/settings.json`` — a file the
sync never managed — so the release that ships the updater cannot wire it and
``TOOLING_UPDATE`` set at launch silently no-ops fleet-wide.

settings.json can never join the byte-copy/hash set (its ``env`` block is
per-repo), so the wiring joins the sync set STRUCTURALLY instead: this tool
patches (default) or verifies (``--check``) the hooks block in place, leaving
everything else in the file untouched.

Canonical wiring (matches scaffold.sh role_settings_json byte-for-byte):

* non-trainee roles: the updater is the FIRST command of the first
  SessionStart matcher group — ahead of session-start.sh, whose T-069 guard
  expects the updater's sentinel to exist by the time it runs, and ahead of
  any logging hook (the only race-free slot vs live-log appends, T-065 F4).
* trainee: the updater runs LAST — after trainee-session-branch.py, because
  the trainee's branch switch happens inside a SessionStart hook rather than
  pre-launch, and updating before it would commit onto main.

Exit codes: 0 = wired (already or now); 1 = usage/parse error; 2 = ``--check``
found the wiring absent or out of position.

Called by sync-agent-tooling.sh (patch step + byte-identity ``--check``) and
usable standalone against any home repo:

    python3 tools/wire-update-tooling.py --settings <repo>/.claude/settings.json --role coder
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

UPDATER_MARKER = "update-tooling.py"
UPDATER_ENTRY = {
    "type": "command",
    "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/tools/update-tooling.py",
    # A cold clone+sync can outlive the default 60s hook budget; a killed
    # updater leaves no sentinel and the session-start.sh guard HALTs the
    # launch — so give the real path room to finish.
    "timeout": 300,
}


def wire(settings: dict, role: str) -> tuple[dict, bool]:
    """Return (patched settings, changed?). Normalises: exactly one updater
    entry, canonical shape, canonical position (first, or last for trainee)."""
    hooks = settings.setdefault("hooks", {})
    session_start = hooks.setdefault(
        "SessionStart", [{"matcher": "startup|resume", "hooks": []}]
    )
    if not session_start:
        session_start.append({"matcher": "startup|resume", "hooks": []})
    group = session_start[0]
    before = json.dumps(group, sort_keys=True)
    commands = [
        c for c in group.get("hooks", [])
        if UPDATER_MARKER not in str(c.get("command", ""))
    ]
    if role == "trainee":
        commands.append(dict(UPDATER_ENTRY))
    else:
        commands.insert(0, dict(UPDATER_ENTRY))
    group["hooks"] = commands
    return settings, json.dumps(group, sort_keys=True) != before


def check(settings: dict, role: str) -> str | None:
    """None if correctly wired, else a human-readable defect description."""
    try:
        commands = settings["hooks"]["SessionStart"][0]["hooks"]
    except (KeyError, IndexError, TypeError):
        return "no SessionStart hook block in settings.json"
    positions = [i for i, c in enumerate(commands)
                 if UPDATER_MARKER in str(c.get("command", ""))]
    if not positions:
        return "update-tooling.py not wired in SessionStart"
    want = len(commands) - 1 if role == "trainee" else 0
    if positions != [want]:
        return (f"update-tooling.py at position(s) {positions}, expected "
                f"[{want}] for role {role!r}")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--settings", required=True,
                        help="path to the home repo's committed .claude/settings.json")
    parser.add_argument("--role", required=True, help="role-class (position rule)")
    parser.add_argument("--check", action="store_true",
                        help="verify only — exit 2 on missing/out-of-position wiring")
    args = parser.parse_args()

    path = Path(args.settings)
    if not path.is_file():
        print(f"wire-update-tooling: settings file not found: {path}", file=sys.stderr)
        return 1
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"wire-update-tooling: {path} is not valid JSON: {exc}", file=sys.stderr)
        return 1

    if args.check:
        defect = check(settings, args.role)
        if defect:
            print(f"wire-update-tooling: CHECK FAIL — {defect}")
            return 2
        print("wire-update-tooling: CHECK OK — updater wired at canonical position")
        return 0

    settings, changed = wire(settings, args.role)
    if changed:
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        print(f"wire-update-tooling: WIRED update-tooling.py into {path} (role={args.role})")
    else:
        print(f"wire-update-tooling: OK — already wired ({path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
