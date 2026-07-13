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

import shutil
import subprocess
import sys
from pathlib import Path

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


def _config_filled() -> bool:
    # The single trainee credential lives in the COMMITTED training-config.yaml
    # (T-030 R2-2) — the granular Database API Key scoped to the two training
    # collections. "Filled" (no {{PLACEHOLDER}} left) is all we check offline;
    # we never print its value.
    try:
        lib_root = Path(__file__).resolve().parents[1]  # .claude/ in a home repo
        sys.path.insert(0, str(lib_root))
        from lib import training_config
        repo_root = lib_root.parent if lib_root.name == ".claude" else lib_root
        return training_config.is_configured(training_config.load(str(repo_root)))
    except Exception:
        return False


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
        ("training-config.yaml (Database API Key)", _config_filled(),
         "fill qdrant_api_key (and your handle) in training-config.yaml — "
         f"take-on Phase A, see {SETUP_GUIDE}", True),
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
