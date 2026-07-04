#!/usr/bin/env python3
"""Trainee first-run preflight (PROJ-011/T-025 R-13).

Two jobs, one file:

* **Standalone** (`python3 .claude/hooks/trainee-preflight.py`, or a terminal): print a
  per-dependency OK/MISSING report with the exact next action for anything missing.
* **SessionStart hook** (trainee runtime, wired first in the chain): if the workstation
  is not configured yet, inject ONE friendly "not configured yet — see the setup guide"
  pointer into session context and stay quiet otherwise. It NEVER fails and NEVER blocks
  orientation — the point of R-13 fail-soft.

It is deliberately offline + side-effect-free: it only *reports*. The full dependency
rationale is in `docs/dependency-analysis.md`; the trainer-assisted fix is
`docs/workstation-setup.md`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

SETUP_GUIDE = "docs/workstation-setup.md"
DEP_DOC = "docs/dependency-analysis.md"


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _gh_authed() -> bool:
    if not _has("gh"):
        return False
    try:
        r = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=8,
        )
        return r.returncode == 0
    except Exception:
        return False


def _key_present() -> bool:
    # The single trainee secret. Present in the launch env (settings.local.json env
    # block or secrets MCP binding) is all we can check offline — never read its value.
    return bool(os.environ.get("PODZONE_QDRANT_APIKEY", "").strip())


def checks() -> list[tuple[str, bool, str, bool]]:
    """(label, ok, next-action, required) — required=False means "trainer will do it"."""
    return [
        ("git", _has("git"),
         "install the Xcode Command Line Tools / your package manager", True),
        ("python3", _has("python3"),
         "install python3", True),
        ("gh (authenticated)", _gh_authed(),
         "brew install gh && gh auth login  (optional — without it, open the session PR by hand)",
         False),
        ("PODZONE_QDRANT_APIKEY", _key_present(),
         f"set it in .claude/settings.local.json env block — see {SETUP_GUIDE}", True),
    ]


def _report(results) -> int:
    print("Trainee preflight — first-run dependency check\n")
    all_required_ok = True
    for label, ok, action, required in results:
        mark = "OK  " if ok else "MISS"
        print(f"  [{mark}] {label}")
        if not ok:
            print(f"         → {action}")
            if required:
                all_required_ok = False
    print()
    print("  Trainer actions (not auto-checked): invite the training lead as a repo")
    print("  collaborator, and author + --approve the trainee brief. See "
          f"{SETUP_GUIDE}.")
    print()
    if all_required_ok:
        print("All required dependencies present — paste your `Brief:` first prompt to begin.")
    else:
        print(f"Some prep required before a brief will materialise — see {SETUP_GUIDE}.")
    return 0


def _hook_pointer(results) -> int:
    # SessionStart mode: one concise pointer if anything required is missing; silent
    # (all good) otherwise. Never blocks — orientation always works.
    missing = [label for label, ok, _a, required in results if required and not ok]
    if missing:
        print(
            "ℹ️  Training repo not configured on this workstation yet "
            f"({', '.join(missing)} missing). This is expected on a fresh clone — "
            f"the hooks fail soft, nothing is broken. Work through {SETUP_GUIDE} with "
            "your trainer, then run `python3 .claude/hooks/trainee-preflight.py` to "
            f"confirm. Full dependency list: {DEP_DOC}."
        )
    return 0


def main() -> int:
    results = checks()
    interactive = sys.stdin.isatty() or "--report" in sys.argv[1:]
    if interactive:
        return _report(results)
    # Hook mode: drain any JSON stdin the harness passed, then emit the soft pointer.
    try:
        sys.stdin.read()
    except Exception:
        pass
    return _hook_pointer(results)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Fail-soft contract: a preflight failure must never wall the session.
        sys.exit(0)
