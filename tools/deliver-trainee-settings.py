#!/usr/bin/env python3
"""
deliver-trainee-settings.py — deliver the structural settings.json fixes to the
ALREADY-LIVE trainee repos (PROJ-011/T-125, CC-519).

Why this exists
---------------

`wire-update-tooling.py` makes the safety-critical bits of a trainee repo's
committed `.claude/settings.json` — the updater wiring (T-069) and the `python3`
shell guard on the preflight command (T-121/T-125) — *structurally* enforceable.
But nothing ever runs it against the six live trainee repos:

* `scaffold.sh` applies it to **new** repos only;
* `sync-agent-tooling.sh` applies it, but on a trainee repo the sync is only ever
  invoked by `update-tooling.py`, which **no-ops unless `TOOLING_UPDATE` is set** —
  and nothing sets it on a trainee launch (their `env` block is `TRAINEE_RUNTIME`
  only). The trainee self-update channel is inert by default, by design: trainees
  do not get handed fleet-release tags.

So the live repos need one operator-run pass. This is that pass: a branch + PR per
repo against the org remote, opened by an operator with `gh` — never by a trainee,
and never touching a trainee's own machine.

How it reaches the trainee's laptop
-----------------------------------

The trainee repos are org-owned (`PodZonePlatformEngineering/home-training-*`), and
every trainee session starts with `trainee-finalise.py --guard` →
`session_guard.preflight()` → `ff_main()`, which **fetches `origin/main` and
fast-forwards the local clone** before branching. So once a PR here is merged, the
fix lands on the trainee's machine at their next session start with no action from
them — and takes effect the session AFTER that, because Claude Code reads
settings.json before hooks run.

The one case this does NOT reach: a machine with **no python3 at all** runs no
hooks, so it never fast-forwards. That trainee only gets the guard by pulling (or
re-cloning) by hand — which is exactly the population the guard is for. Say so
plainly rather than assuming this script covered them.

Usage (operator)
----------------

    # inspect — clones to a temp dir, prints the diff, writes nothing anywhere
    python3 tools/deliver-trainee-settings.py --dry-run

    # open one PR per repo that needs it
    python3 tools/deliver-trainee-settings.py --apply

    # a subset, or a local clone instead of a fresh clone of the remote
    python3 tools/deliver-trainee-settings.py --repo home-training-eben --apply
    python3 tools/deliver-trainee-settings.py --local-clone /path/to/repo --dry-run

Requires `git` and (for `--apply`) `gh` authenticated with push access to the org.
Idempotent: a repo already carrying both fixes is reported `ok` and skipped.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ORG = "PodZonePlatformEngineering"

# The six trainee repos live at 2026-07-31 (PROJ-011). Keep this list explicit:
# a wildcard sweep over the org would pick up the template and any future repo
# whose settings must NOT be rewritten without a decision.
LIVE_TRAINEE_REPOS = (
    "home-training-martin",
    "home-training-norma",
    "home-training-charles",
    "home-training-thandi",
    "home-training-collette",
    "home-training-eben",
)

BRANCH = "chore/t125-python3-shell-guard"
COMMIT_MSG = """chore(t125): python3 shell guard on the preflight hook command

Without this, a workstation with no python3 dies at session start with a raw
Python error (the failure a trainee hit on a fresh Windows 11 install). The
guard is a shell-level `command -v python3 || echo <plain-English message>` on
the hook command itself — a Python preflight cannot catch its own interpreter
being missing.

Applied by agent-tooling tools/deliver-trainee-settings.py (PROJ-011/T-125).
"""
PR_BODY = """Delivers the PROJ-011/T-121 `python3` shell guard (and, if missing, the
PROJ-039/T-069 updater wiring) to this already-live trainee repo.

`.claude/settings.json` is per-repo, so it is not in the byte-identity sync set;
`tools/wire-update-tooling.py` enforces its safety-critical bits structurally.
New repos get this from `scaffold.sh`; live repos need this one pass.

Effect once merged: the trainee's clone fast-forwards `main` at their next
session start (`session_guard.preflight`), and the guard is live the session
after that. **A machine with no python3 runs no hooks and so never
fast-forwards — that trainee must pull by hand.**

Only the SessionStart hook command strings change; `env` and every other hook
are untouched.
"""


def run(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def patch_settings(repo_dir: Path) -> tuple[bool, str]:
    """Run the structural patcher over `repo_dir`. Returns (changed, output)."""
    settings = repo_dir / ".claude" / "settings.json"
    if not settings.is_file():
        return False, f"no .claude/settings.json in {repo_dir}"
    before = settings.read_text(encoding="utf-8")
    cp = run([sys.executable, str(REPO_ROOT / "tools" / "wire-update-tooling.py"),
              "--settings", str(settings), "--role", "trainee"], check=False)
    if cp.returncode != 0:
        return False, f"patcher failed: {cp.stdout}{cp.stderr}"
    return settings.read_text(encoding="utf-8") != before, cp.stdout.strip()


def process(repo_dir: Path, name: str, *, apply: bool) -> dict:
    settings = repo_dir / ".claude" / "settings.json"
    original = settings.read_text(encoding="utf-8") if settings.is_file() else None
    changed, note = patch_settings(repo_dir)
    if not changed:
        return {"repo": name, "status": "ok", "note": note or "already guarded"}

    diff = run(["git", "-C", str(repo_dir), "diff", "--", ".claude/settings.json"],
               check=False).stdout
    if not apply:
        # --dry-run is READ-ONLY, including against an operator's own clone: the
        # patch was applied only to render the diff, so put the file back exactly.
        if original is not None:
            settings.write_text(original, encoding="utf-8")
        return {"repo": name, "status": "would-patch", "note": note, "diff": diff}

    run(["git", "-C", str(repo_dir), "checkout", "-B", BRANCH])
    run(["git", "-C", str(repo_dir), "add", ".claude/settings.json"])
    run(["git", "-C", str(repo_dir), "commit", "-m", COMMIT_MSG])
    run(["git", "-C", str(repo_dir), "push", "-u", "origin", BRANCH, "--force-with-lease"])
    body = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
    body.write(PR_BODY)
    body.close()
    cp = run(["gh", "pr", "create", "--repo", f"{ORG}/{name}",
              "--head", BRANCH, "--base", "main",
              "--title", "chore(t125): python3 shell guard on the preflight hook command",
              "--body-file", body.name], cwd=str(repo_dir), check=False)
    return {"repo": name, "status": "pr" if cp.returncode == 0 else "pr-failed",
            "note": (cp.stdout + cp.stderr).strip(), "diff": diff}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--repo", action="append", default=[],
                    help="trainee repo name (repeatable); default: all six live repos")
    ap.add_argument("--local-clone", help="patch this existing clone instead of cloning")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="report + diff only; write nothing")
    mode.add_argument("--apply", action="store_true", help="commit, push and open a PR per repo")
    args = ap.parse_args()

    results: list[dict] = []
    if args.local_clone:
        path = Path(args.local_clone).resolve()
        results.append(process(path, path.name, apply=args.apply))
    else:
        repos = args.repo or list(LIVE_TRAINEE_REPOS)
        with tempfile.TemporaryDirectory() as td:
            for name in repos:
                dest = Path(td) / name
                cp = run(["git", "clone", "--depth", "1",
                          f"https://github.com/{ORG}/{name}.git", str(dest)], check=False)
                if cp.returncode != 0:
                    results.append({"repo": name, "status": "clone-failed",
                                    "note": cp.stderr.strip()})
                    continue
                results.append(process(dest, name, apply=args.apply))
                shutil.rmtree(dest, ignore_errors=True)

    for r in results:
        print(f"{r['status']:>13}  {r['repo']}  {r.get('note', '')}")
        if r.get("diff") and args.dry_run:
            print("\n".join("      " + line for line in r["diff"].splitlines()))
    print("\n" + json.dumps({"mode": "apply" if args.apply else "dry-run",
                             "results": [{k: v for k, v in r.items() if k != "diff"}
                                         for r in results]}, indent=2))
    return 0 if all(r["status"] in ("ok", "would-patch", "pr") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
