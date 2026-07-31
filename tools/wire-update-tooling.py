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
normalisation of the same file for the ``trainee`` role: the ``python3`` guard.
Same argument, same mechanism — a per-repo file that cannot be byte-copied
still gets its safety-critical bits enforced structurally.

PROJ-011/T-128 (CC-525) made that guard TOTAL. T-125 shipped it as an inline
``command -v python3 … || echo …`` on the preflight command, which covered 1 of
the trainee's 11 ``python3`` hook invocations: the trainee saw one friendly
message and then a raw ``python3: command not found`` on every prompt and every
tool call. Every trainee hook command now routes through the
``hooks/run-hook.sh`` shim instead (§ SHIM_PREFIX), which does the check once.
The normalisation here is generic — ANY ``python3 "$CLAUDE_PROJECT_DIR"/.claude/…``
command in ANY hook event is rewritten — so a hook added later is covered by
construction rather than by remembering to guard it.

Exit codes: 0 = wired (already or now); 1 = usage/parse error; 2 = ``--check``
found the wiring absent/out of position, or (trainee) an unshimmed ``python3``
hook command.

Called by sync-agent-tooling.sh (patch step + byte-identity ``--check``) and
usable standalone against any home repo:

    python3 tools/wire-update-tooling.py --settings <repo>/.claude/settings.json --role coder
"""

from __future__ import annotations

import argparse
import json
import re
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
# The trainee variant of the same entry: identical in every respect (position,
# 300s timeout) except that it routes through the run-hook.sh shim, like every
# other trainee command (T-128). The timeout is a SIBLING KEY of the entry, not
# part of the command string, so shimming cannot disturb it.
TRAINEE_UPDATER_ENTRY = {
    "type": "command",
    "command": "bash \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/run-hook.sh tools/update-tooling.py",
    "timeout": 300,
}


TRAINEE_ANCHOR = "trainee-session-branch.py"

# --- Trainee python3 guard (PROJ-011/T-125 → made total by T-128) ---
# T-121 fixed the failure Martin hit on a fresh Windows 11 install: the very first
# session died with a raw Python error because ``python3`` was absent. The guard has
# to be at SHELL level — a Python-based preflight structurally cannot catch its own
# interpreter being missing — so it lives in the hook COMMAND strings, which live in
# ``.claude/settings.json``, which can never join the byte-identity set. So it joins
# the sync set STRUCTURALLY here, exactly like the updater wiring above.
#
# T-125 shipped it as an inline `command -v python3 … || echo …` on the PREFLIGHT
# command only. That is 1 of the trainee's 11 python3 invocations: one friendly
# message at session start, then a raw `python3: command not found` on every prompt
# (UserPromptSubmit telemetry) and every tool call (PreToolUse read-guard) for the
# rest of the session. T-128 routes every command through ONE shim instead:
#
#   bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/run-hook.sh [--announce] <rel> [args]
#
# Chosen over eleven inline guards because eleven guards are eleven places to forget
# when hook #12 is added — and because the rewrite below is generic over ANY
# `python3 "$CLAUDE_PROJECT_DIR"/.claude/…` command in ANY event, so hook #12 is
# covered whether or not anyone remembers. It also keeps settings.json legible: one
# inline guard already took the file from 6 lines to 76.
#
# ``--announce`` is carried by exactly ONE command — the first SessionStart hook — so
# the message is emitted once per session. A shim printing on every PreToolUse would
# be worse than the raw error it replaces; every other command is silent on the
# missing-interpreter path (exit 0, no output), which for PreToolUse and
# UserPromptSubmit is the neutral "proceed" answer.
#
# Keep byte-for-byte in lockstep with scaffold.sh's role_settings_json trainee block
# (tests/proj039/test_wire_update_tooling.py pins the two together by scaffolding a
# real trainee repo and comparing).
SHIM_REL = "hooks/run-hook.sh"
SHIM_PREFIX = "bash \"$CLAUDE_PROJECT_DIR\"/.claude/" + SHIM_REL
ANNOUNCE_FLAG = " --announce"

#: A bare resident-hook invocation: `python3 "$CLAUDE_PROJECT_DIR"/.claude/<rel> [args]`
_PY_COMMAND = re.compile(
    r'^python3 "\$CLAUDE_PROJECT_DIR"/\.claude/(?P<rel>\S+)(?P<args>.*)$'
)
#: The T-125 inline guard wrapped around one of those. Stripped back to the bare
#: invocation before shimming, so a live repo carrying the T-125 form converges.
_T125_GUARD = re.compile(
    r"^command -v python3 >/dev/null 2>&1 && (?P<inner>.*?) \|\| echo '.*'$",
    re.DOTALL,
)

#: The message the shim prints. Kept here only so the delivery tooling and tests can
#: assert on it; the authoritative copy is in hooks/run-hook.sh.
NO_PYTHON_MESSAGE = (
    "Python 3 is not installed on this machine, so none of this training repo "
    "automation can run (no session branch, no saved work, no progress records). "
    "Nothing here is broken and you can still talk to the trainee, but say so "
    "plainly at the start of the session and ask them to install Python 3 with "
    "their trainer before the next one - see docs/workstation-setup.md."
)


def _iter_hook_entries(settings: dict):
    """Yield every hook command entry in the file, in document order, across every
    event. Generic on purpose: a hook added to a NEW event is still swept."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event in hooks.values():
        if not isinstance(event, list):
            continue
        for group in event:
            if not isinstance(group, dict):
                continue
            for entry in group.get("hooks") or []:
                if isinstance(entry, dict) and "command" in entry:
                    yield entry


def shim_command(cmd: str, *, announce: bool) -> str | None:
    """Return the canonical shimmed form of ``cmd``, or None if it is not a
    python3 resident-hook invocation (a `bash .../session-start.sh` command, say,
    needs no guard and must be left exactly as it is).

    Idempotent: an already-shimmed command comes back unchanged apart from the
    ``--announce`` flag, which is normalised onto the announce carrier and off
    everything else.
    """
    if cmd.startswith(SHIM_PREFIX):
        rest = cmd[len(SHIM_PREFIX):]
        if rest.startswith(ANNOUNCE_FLAG):
            rest = rest[len(ANNOUNCE_FLAG):]
        return SHIM_PREFIX + (ANNOUNCE_FLAG if announce else "") + rest
    bare = cmd
    m125 = _T125_GUARD.match(bare)
    if m125:
        bare = m125.group("inner")
    m = _PY_COMMAND.match(bare.strip())
    if not m:
        return None
    return (SHIM_PREFIX + (ANNOUNCE_FLAG if announce else "")
            + " " + m.group("rel") + m.group("args"))


def _announce_carrier(settings: dict) -> dict | None:
    """The one entry that prints the message: the FIRST SessionStart hook command.
    SessionStart runs once per session, and its stdout is surfaced to the tutor —
    which is exactly the once-per-session delivery the message wants."""
    try:
        commands = settings["hooks"]["SessionStart"][0]["hooks"]
    except (KeyError, IndexError, TypeError):
        return None
    return commands[0] if commands else None


def guard_trainee_hooks(settings: dict) -> bool:
    """Route every python3 hook command in ``settings`` through the shim.

    Returns True if anything changed. Only the command STRINGS are rewritten:
    position, sibling keys (``timeout``), matchers, ``env`` and any non-python3
    command are left exactly as they are.
    """
    carrier = _announce_carrier(settings)
    changed = False
    for entry in _iter_hook_entries(settings):
        cmd = str(entry["command"])
        shimmed = shim_command(cmd, announce=entry is carrier)
        if shimmed is not None and shimmed != cmd:
            entry["command"] = shimmed
            changed = True
    return changed


def check_trainee_hooks(settings: dict) -> str | None:
    """None if EVERY python3 hook invocation is shimmed and the message fires
    exactly once, else a human-readable defect description.

    This is the coverage assertion (T-128 task 3) in its load-bearing place: it is
    count-based over the whole file, so adding a hook without coverage fails
    ``--check`` and, through it, the sync and the test suite.
    """
    unshimmed = [str(e["command"]) for e in _iter_hook_entries(settings)
                 if "python3" in str(e["command"])]
    if unshimmed:
        return (f"{len(unshimmed)} hook command(s) invoke python3 without the "
                f"run-hook.sh shim (T-128) — a machine without python3 gets a raw "
                f"error on every one of them; first: {unshimmed[0][:80]}")
    announcers = [e for e in _iter_hook_entries(settings)
                  if ANNOUNCE_FLAG.strip() in str(e["command"])]
    if len(announcers) > 1:
        return (f"{len(announcers)} hook commands carry --announce — the "
                f"no-python3 message must fire ONCE per session, not per hook")
    shimmed = [e for e in _iter_hook_entries(settings)
               if str(e["command"]).startswith(SHIM_PREFIX)]
    if shimmed and not announcers:
        return ("no hook command carries --announce — a trainee with no python3 "
                "would get silence instead of the plain-English message")
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
        commands.insert(pos, dict(TRAINEE_UPDATER_ENTRY))
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
        return check_trainee_hooks(settings)
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
    # The trainee python3 guard is a second, independent normalisation of the same
    # file (T-125/T-128) — reported separately so each says what it actually did.
    guarded = args.role == "trainee" and guard_trainee_hooks(settings)
    if changed or guarded:
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    if changed:
        print(f"wire-update-tooling: WIRED update-tooling.py into {path} (role={args.role})")
    else:
        print(f"wire-update-tooling: OK — already wired ({path})")
    if guarded:
        shimmed = sum(1 for e in _iter_hook_entries(settings)
                      if str(e["command"]).startswith(SHIM_PREFIX))
        print(f"wire-update-tooling: GUARDED {shimmed} trainee hook command(s) "
              f"through hooks/run-hook.sh (T-128) in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
