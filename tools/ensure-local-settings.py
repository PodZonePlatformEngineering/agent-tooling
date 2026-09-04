#!/usr/bin/env python3
"""
ensure-local-settings.py — headless write-capability posture for a home-repo
clone (PROJ-039/T-132; the durable half of T-103).

A headless (`claude -p`) session has no interactive permission prompt: a
launch clone whose `.claude/settings.local.json` does not grant write
capability burns the whole dispatch as a no-op (Athena's PROJ-046/T-001 sid
2932eae9 — Write never grantable, shell writes sandbox-blocked, `git commit`
stuck at "requires approval" forever). The operator ruling (2026-08-01) and
the T-103 Part A proposal converge on one durable mechanism: the gitignored
per-clone `.claude/settings.local.json` carries

    "permissions": { "defaultMode": "bypassPermissions" }

set ONCE per clone by the scaffold / the launching Team Lead — never by an
agent on its own clone mid-session (self-elevation is an operator action;
this is why sync-agent-tooling.sh / update-tooling.py deliberately do NOT
call this tool).

Modes (both take --repo PATH, the clone root):

  apply (default)   merge-not-clobber: parse the existing file if present,
                    preserve every existing key (hooks / env / _comment /
                    permissions.allow — the Athena shape), and set
                    permissions.defaultMode = bypassPermissions. Creates the
                    file (and .claude/) when absent. Idempotent.
  --check           verify only, no writes. Exit 0 iff the clone's posture is
                    headless-write-sufficient; exit 1 with a loud message
                    otherwise. /launch-session headless prep runs this and
                    refuses to emit the launch command on failure.

"Sufficient" means `permissions.defaultMode == "bypassPermissions"`, or an
allow-list that covers Write, Edit and Bash unrestricted (`"Write"` /
`"Write(*)"` style entries) with none of them denied. Anything else fails the
check — an interactive prompt cannot resolve it in a headless session.

Exit codes: 0 ok · 1 insufficient (--check) or usage/IO error · 2 existing
file present but not valid JSON (never clobbered — fix it by hand).

Stdlib only (home-repo python3 has no third-party packages).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

BYPASS = "bypassPermissions"
# Unrestricted grants of all three are the minimum a headless build session
# needs (file writes + git/gh via Bash) when defaultMode is not bypass.
WRITE_TOOLS = ("Write", "Edit", "Bash")


def settings_path(repo: Path) -> Path:
    return repo / ".claude" / "settings.local.json"


def load_settings(path: Path) -> dict:
    """Parse the existing file. SystemExit(2) on invalid JSON — never clobber."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"ERROR: {path} exists but is not valid JSON ({exc}) — "
              "refusing to touch it; fix or remove it by hand.", file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(data, dict):
        print(f"ERROR: {path} is valid JSON but not an object — "
              "refusing to touch it.", file=sys.stderr)
        raise SystemExit(2)
    return data


def _grants_unrestricted(allow: list, tool: str) -> bool:
    return any(entry in (tool, f"{tool}(*)") for entry in allow
               if isinstance(entry, str))


def is_sufficient(data: dict) -> bool:
    perms = data.get("permissions")
    if not isinstance(perms, dict):
        return False
    if perms.get("defaultMode") == BYPASS:
        deny = perms.get("deny") or []
        return not any(_grants_unrestricted(deny, t) for t in WRITE_TOOLS)
    allow = perms.get("allow") or []
    deny = perms.get("deny") or []
    if not isinstance(allow, list) or not isinstance(deny, list):
        return False
    return (all(_grants_unrestricted(allow, t) for t in WRITE_TOOLS)
            and not any(_grants_unrestricted(deny, t) for t in WRITE_TOOLS))


def _is_git_ignored(repo: Path, rel_path: str) -> bool | None:
    """True/False if `git check-ignore` can answer, None if git isn't usable
    here (not a repo, no git binary) — caller falls back to a text check."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "-q", rel_path],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode in (0, 1):
        return result.returncode == 0
    return None  # e.g. not a git repo — rc 128


def ensure_gitignored(repo: Path) -> None:
    """Guard against the settings file landing in a commit (found live
    2026-09-04: a dispatched session's own `git add` swept up a freshly
    -created settings.local.json into its PR — academy-frontend#137 — because
    that repo's .gitignore, like several others in the fleet, never actually
    excluded `.claude/settings.local.json`; this tool only ever assumed it
    was gitignored, never verified or enforced it). Appends one line to
    .gitignore, creating the file if absent. Idempotent: does nothing if
    `git check-ignore` (or, failing that, a literal text match) already
    covers the path — never duplicates an entry or fights a broader
    existing pattern like `.claude/`.
    """
    rel = ".claude/settings.local.json"
    already = _is_git_ignored(repo, rel)
    gitignore = repo / ".gitignore"
    if already is None:
        existing = gitignore.read_text() if gitignore.exists() else ""
        already = rel in existing.splitlines() or ".claude/" in existing.splitlines()
    if already:
        return
    existing = gitignore.read_text() if gitignore.exists() else ""
    needs_leading_newline = bool(existing) and not existing.endswith("\n")
    with gitignore.open("a") as f:
        if needs_leading_newline:
            f.write("\n")
        f.write(f"{rel}\n")
    print(f"appended: {gitignore} ({rel} was not previously gitignored)")


def apply(repo: Path) -> int:
    ensure_gitignored(repo)
    path = settings_path(repo)
    data = load_settings(path)
    if is_sufficient(data):
        print(f"ok: {path} already grants headless write capability")
        return 0
    perms = data.setdefault("permissions", {})
    if not isinstance(perms, dict):
        print(f"ERROR: {path} has a non-object 'permissions' key — "
              "refusing to replace it; fix by hand.", file=sys.stderr)
        return 2
    perms["defaultMode"] = BYPASS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote: {path} (permissions.defaultMode = {BYPASS}; "
          "existing keys preserved)")
    return 0


def check(repo: Path) -> int:
    path = settings_path(repo)
    if not path.exists():
        print(f"HALT: {path} is missing — a headless session in this clone "
              "cannot write (no interactive prompt to grant Write/Bash).\n"
              f"Fix (operator/Team Lead action, NOT in-session):\n"
              f"  python3 {Path(__file__).resolve()} --repo {repo}",
              file=sys.stderr)
        return 1
    data = load_settings(path)
    if is_sufficient(data):
        print(f"ok: {path} grants headless write capability")
        return 0
    print(f"HALT: {path} does not grant headless write capability — "
          f"needs permissions.defaultMode: {BYPASS} (or an allow-list "
          f"covering {', '.join(WRITE_TOOLS)} unrestricted).\n"
          f"Fix (operator/Team Lead action, NOT in-session):\n"
          f"  python3 {Path(__file__).resolve()} --repo {repo}",
          file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ensure/verify a clone's .claude/settings.local.json "
                    "grants headless write capability (PROJ-039/T-132).")
    parser.add_argument("--repo", required=True,
                        help="clone root (the directory containing .claude/)")
    parser.add_argument("--check", action="store_true",
                        help="verify only — exit 1 (loud) when insufficient")
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser()
    if not repo.is_dir():
        print(f"ERROR: --repo {repo} is not a directory", file=sys.stderr)
        return 1
    return check(repo) if args.check else apply(repo)


if __name__ == "__main__":
    sys.exit(main())
