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
* trainee: the updater runs immediately AFTER trainee-session-branch.py,
  because the trainee's branch switch happens inside a SessionStart hook
  rather than pre-launch, and updating before it would commit onto main. It
  is no longer last (v2): the v3 trainee chain continues with
  trainee-materialise.py + trainee-telemetry.py, which must see the updated
  tooling (PROJ-011/T-030).

Since PROJ-011/T-125 (CC-519) this tool carries a SECOND structural
normalisation of the same file for the ``trainee`` role: the ``python3`` shell
guard on the preflight hook command (§ GUARDED_PREFLIGHT_COMMAND). Same
argument, same mechanism — a per-repo file that cannot be byte-copied still
gets its safety-critical bits enforced structurally.

Exit codes: 0 = wired (already or now); 1 = usage/parse error; 2 = ``--check``
found the wiring absent/out of position, or (trainee) the preflight guard
missing.

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


TRAINEE_ANCHOR = "trainee-session-branch.py"

# --- Trainee python3 shell guard (PROJ-011/T-125, CC-519) ---
# T-121 fixed the failure Martin hit on a fresh Windows 11 install: the very first
# session died with a raw Python error because ``python3`` was absent. Part of that
# fix is a SHELL guard on the preflight hook COMMAND — a Python-based preflight
# structurally cannot catch its own interpreter being missing, so the guard has to
# live in the command string. That string lives in ``.claude/settings.json``, which
# can never join the byte-identity set, so it joins the sync set STRUCTURALLY here,
# exactly like the updater wiring above: the preflight command is normalised to the
# canonical guarded form in place, leaving position, env and every other hook alone.
#
# Keep byte-for-byte in lockstep with scaffold.sh's role_settings_json trainee block
# (tests/proj039/test_wire_update_tooling.py pins the two together by scaffolding a
# real trainee repo and comparing).
PREFLIGHT_MARKER = "trainee-preflight.py"
GUARDED_PREFLIGHT_COMMAND = (
    "command -v python3 >/dev/null 2>&1 && python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/trainee-preflight.py || echo 'Python 3 is not installed on this machine, so none of this training repo automation can run (no session branch, no saved work, no progress records). Nothing here is broken and you can still talk to the trainee, but say so plainly at the start of the session and ask them to install Python 3 with their trainer before the next one - see docs/workstation-setup.md.'"
)


def guard_trainee_preflight(settings: dict) -> bool:
    """Normalise the trainee preflight SessionStart command to the guarded form.

    Returns True if anything changed. Position and any sibling keys (``timeout``)
    are preserved — only the command string is rewritten, and only when it differs.
    A repo with no preflight entry is left alone (nothing to guard).
    """
    try:
        commands = settings["hooks"]["SessionStart"][0]["hooks"]
    except (KeyError, IndexError, TypeError):
        return False
    changed = False
    for entry in commands:
        cmd = str(entry.get("command", ""))
        if PREFLIGHT_MARKER in cmd and cmd != GUARDED_PREFLIGHT_COMMAND:
            entry["command"] = GUARDED_PREFLIGHT_COMMAND
            changed = True
    return changed


def check_trainee_preflight(settings: dict) -> str | None:
    """None if the preflight command carries the canonical guard (or is absent),
    else a human-readable defect description."""
    try:
        commands = settings["hooks"]["SessionStart"][0]["hooks"]
    except (KeyError, IndexError, TypeError):
        return None
    for entry in commands:
        cmd = str(entry.get("command", ""))
        if PREFLIGHT_MARKER in cmd and cmd != GUARDED_PREFLIGHT_COMMAND:
            return ("trainee-preflight.py command is missing the python3 shell "
                    "guard (T-125) — a machine without python3 gets a raw error "
                    "instead of the plain-English message")
    return None


def _anchor_index(commands: list) -> int | None:
    for i, c in enumerate(commands):
        if TRAINEE_ANCHOR in str(c.get("command", "")):
            return i
    return None


def wire(settings: dict, role: str) -> tuple[dict, bool]:
    """Return (patched settings, changed?). Normalises: exactly one updater
    entry, canonical shape, canonical position (first; trainee: right after
    the session-branch hook — appended if the anchor is missing)."""
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
        anchor = _anchor_index(commands)
        pos = len(commands) if anchor is None else anchor + 1
        commands.insert(pos, dict(UPDATER_ENTRY))
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
    if role == "trainee":
        anchor = _anchor_index(commands)
        want = len(commands) - 1 if anchor is None else anchor + 1
    else:
        want = 0
    if positions != [want]:
        return (f"update-tooling.py at position(s) {positions}, expected "
                f"[{want}] for role {role!r}")
    if role == "trainee":
        return check_trainee_preflight(settings)
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
    # The trainee python3 shell guard is a second, independent normalisation of the
    # same file (T-125) — reported separately so each says what it actually did.
    guarded = args.role == "trainee" and guard_trainee_preflight(settings)
    if changed or guarded:
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    if changed:
        print(f"wire-update-tooling: WIRED update-tooling.py into {path} (role={args.role})")
    else:
        print(f"wire-update-tooling: OK — already wired ({path})")
    if guarded:
        print("wire-update-tooling: GUARDED trainee-preflight.py with the python3 "
              f"shell guard (T-125) in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
