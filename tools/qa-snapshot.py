#!/usr/bin/env python3
"""QA snapshot ritual for a generated trainee repo (PROJ-011/T-025 R-11).

After a test generation, capture the generated repo as a snapshot branch + PR in
`PodZonePlatformEngineering/podzone-training-qa` so its diff against `main` (the last
accepted baseline) is the measurement. Merging a snapshot PR promotes the baseline.

Steps (per the plan doc R-11):
  1. `snapshot/{date}-{slug}` branch off the QA repo's main.
  2. Clean the working tree (keep `.git/`).
  3. Copy the generated repo's content in (excluding its `.git/`).
  4. add / commit / push.
  5. Open a PR to main.

It also runs an **artifact-hygiene check** (R-8/R-7 acceptance) over the generated repo
before snapshotting: no template-named files, no surviving FILL-IN placeholders, no
scaffold/provenance text in README/AGENTS, no session work at the repo root. Use
`--check-only` to run just the hygiene gate (no git).

Usage:
  python3 tools/qa-snapshot.py --source <generated-repo> --slug <test-slug> [--date YYYY-MM-DD]
  python3 tools/qa-snapshot.py --source <generated-repo> --check-only
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import shutil
import subprocess
import sys
import tempfile

QA_REPO = "PodZonePlatformEngineering/podzone-training-qa"

# --- Artifact-hygiene check (R-8/R-7) ---

# Files whose *name* betrays the template (baseline leaks: home-training-template.
# code-workspace, martin-template-coder.identity.yaml). NB: a doc that legitimately
# *is* a template (e.g. docs/trainee-profile-template.md) is not a leak — only
# generation-artifact file kinds count.
def _is_template_named(base: str) -> bool:
    low = base.lower()
    if "martin-template" in low:
        return True
    if low.endswith(".code-workspace"):
        return True  # a trainee repo emits no workspace file at all (R-8)
    if low.endswith(".identity.yaml") and "template" in low:
        return True
    return False
# Surviving fill-in placeholders (R-8).
_FILL_IN = re.compile(r"FILL[ _-]?IN|\(fill in", re.IGNORECASE)
# Provenance text that must not appear in a trainee's README/AGENTS (R-7): task/req
# numbers, scaffold history, cohort/handoff discussion.
_PROVENANCE = re.compile(
    r"\bR-\d+\b|\bT-\d+\b|PROJ-\d+|CC-\d+|scaffold|provenance|Athena handoff|cohort trade",
    re.IGNORECASE,
)


def _iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def hygiene_violations(source: str) -> list[str]:
    v: list[str] = []
    root = os.path.realpath(source)

    for path in _iter_files(root):
        rel = os.path.relpath(path, root)
        base = os.path.basename(path)
        # 1. template-named generation artifacts (workspace / identity leaks).
        if _is_template_named(base):
            v.append(f"template-named file: {rel}")

    # 2. session work leaked to the repo root (baseline defect: answer.md at root).
    #    Anything at root that is not a known scaffold artifact is suspect.
    allowed_root = {
        "README.md", "AGENTS.md", ".gitignore", "memory", "results",
        "session-reports", "logs", "docs", "workspaces", ".claude",
    }
    for entry in os.listdir(root):
        if entry == ".git":
            continue
        # The trainee dir is the only other top-level dir (named for the trainee).
        full = os.path.join(root, entry)
        if entry in allowed_root:
            continue
        if os.path.isdir(full):
            continue  # the {Trainee}/ dir — content lives here, correct
        v.append(f"unexpected file at repo root (session work must live in the trainee dir): {entry}")

    # 3. FILL-IN placeholders anywhere text-readable.
    for path in _iter_files(root):
        rel = os.path.relpath(path, root)
        try:
            text = open(path, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        if _FILL_IN.search(text):
            v.append(f"surviving FILL-IN placeholder: {rel}")

    # 4. provenance text in the trainee-facing README/AGENTS (R-7).
    for name in ("README.md", "AGENTS.md"):
        p = os.path.join(root, name)
        if not os.path.isfile(p):
            continue
        text = open(p, encoding="utf-8").read()
        for m in set(_PROVENANCE.findall(text)):
            v.append(f"{name}: provenance text present ('{m}') — trainee docs are operating-manual only")
    return v


def run_hygiene(source: str) -> int:
    violations = hygiene_violations(source)
    if not violations:
        print(f"Artifact-hygiene check: PASS ({source})")
        return 0
    print(f"Artifact-hygiene check: FAIL ({source})")
    for x in violations:
        print(f"  - {x}")
    return 1


# --- Snapshot ritual ---

def _run(cmd, cwd=None, check=True):
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check, text=True)


def snapshot(source: str, slug: str, date: str, qa_repo: str,
             raise_pr: bool = True) -> int:
    tmp = tempfile.mkdtemp(prefix="qa-snapshot-")
    clone = os.path.join(tmp, "podzone-training-qa")
    branch = f"snapshot/{date}-{slug}"
    print(f"==> Cloning {qa_repo} → {clone}")
    _run(["git", "clone", "--quiet", f"https://github.com/{qa_repo}.git", clone])

    print(f"==> Branch {branch} off main")
    _run(["git", "-C", clone, "checkout", "-b", branch])

    print("==> Cleaning working tree (keep .git/)")
    for entry in os.listdir(clone):
        if entry == ".git":
            continue
        p = os.path.join(clone, entry)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)

    print(f"==> Copying generated content from {source}")
    src = os.path.realpath(source)
    for entry in os.listdir(src):
        if entry == ".git":
            continue
        s = os.path.join(src, entry)
        d = os.path.join(clone, entry)
        shutil.copytree(s, d) if os.path.isdir(s) else shutil.copy2(s, d)

    _run(["git", "-C", clone, "add", "-A"])
    msg = f"Snapshot: {slug} generation ({date})"
    _run(["git", "-C", clone, "commit", "-m", msg])
    print(f"==> Pushing {branch}")
    _run(["git", "-C", clone, "push", "-u", "origin", branch])

    if raise_pr:
        body = (
            f"QA snapshot of a `{slug}` trainee-repo generation ({date}).\n\n"
            "Diff vs `main` (the last accepted baseline) is the measurement; merging "
            "promotes this to the baseline. Generated via the R-11 snapshot ritual "
            "(`tools/qa-snapshot.py`).\n\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
        )
        print("==> Opening PR to main")
        _run(["gh", "pr", "create", "--repo", qa_repo, "--base", "main",
              "--head", branch, "--title", f"Snapshot: {slug} ({date})",
              "--body", body])
    print(f"\nSnapshot complete: {branch} (clone: {clone})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="QA snapshot ritual (R-11)")
    ap.add_argument("--source", required=True, help="generated trainee repo dir")
    ap.add_argument("--slug", help="test slug for the snapshot branch")
    ap.add_argument("--date", default=_dt.date.today().isoformat())
    ap.add_argument("--qa-repo", default=QA_REPO)
    ap.add_argument("--check-only", action="store_true",
                    help="run the artifact-hygiene gate only (no git)")
    ap.add_argument("--no-pr", action="store_true", help="push the branch but skip the PR")
    ap.add_argument("--skip-hygiene", action="store_true",
                    help="snapshot even if the hygiene gate fails (records the state)")
    args = ap.parse_args()

    if not os.path.isdir(args.source):
        print(f"Error: source not found: {args.source}", file=sys.stderr)
        return 2

    hy = run_hygiene(args.source)
    if args.check_only:
        return hy
    if hy != 0 and not args.skip_hygiene:
        print("\nHygiene gate failed — fix the generation or pass --skip-hygiene to "
              "snapshot the current (non-clean) state anyway.", file=sys.stderr)
        return hy
    if not args.slug:
        print("Error: --slug is required to snapshot", file=sys.stderr)
        return 2
    return snapshot(args.source, args.slug, args.date, args.qa_repo,
                    raise_pr=not args.no_pr)


if __name__ == "__main__":
    sys.exit(main())
