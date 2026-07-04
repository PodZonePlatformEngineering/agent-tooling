#!/usr/bin/env python3
"""Personalise a generated trainee repo for its trainee (PROJ-011/T-025 R-8).

Generation is *Use this template* (a clone of `home-training-template`) + a first-run
personalisation. The template ships with a clean, generic-but-real placeholder name
(`Trainee`) — **no** FILL-IN markers, **no** template-named identity, **no**
template-named workspace file (all baseline R-8 leaks are already gone). This script is
the personalisation step: it rewrites the placeholder to the trainee's handle across the
trainee directory name, the identity YAML, and the README/AGENTS name lines.

It is a **setup-time** tool (run once by the trainer during workstation setup — see
`docs/workstation-setup.md`), NOT an in-session hook: an in-session rename would create
uncommitted changes that trip the SessionStart clean-tree branch guard. Running it here,
before the first session, keeps `main` clean.

Idempotent: re-running with the same handle is a no-op. Derives the handle from the repo
name (`podzone-training-<handle>`) if not given.

Usage:
  python3 .claude/../tools/personalise-trainee.py [--handle <handle>] [--repo <dir>]
"""
from __future__ import annotations

import argparse
import os
import re
import sys

PLACEHOLDER = "Trainee"


def _derive_handle(repo_dir: str) -> str | None:
    base = os.path.basename(os.path.realpath(repo_dir))
    m = re.match(r"podzone-training-(.+)$", base)
    if m:
        return m.group(1)
    return None


def _cap(handle: str) -> str:
    # Directory / display name: a readable capitalised form of the handle.
    return handle[:1].upper() + handle[1:] if handle else handle


def personalise(repo_dir: str, handle: str) -> list[str]:
    changes: list[str] = []
    name = _cap(handle)
    if name == PLACEHOLDER:
        return changes  # nothing to do (already the placeholder-as-name edge case)

    # 1. Rename the trainee directory Trainee/ -> <Name>/
    src_dir = os.path.join(repo_dir, PLACEHOLDER)
    dst_dir = os.path.join(repo_dir, name)
    if os.path.isdir(src_dir) and not os.path.isdir(dst_dir):
        os.rename(src_dir, dst_dir)
        changes.append(f"{PLACEHOLDER}/ -> {name}/")

    # 2. Identity YAML: rename file + set agent:, workspace fields.
    id_dir = os.path.join(repo_dir, "workspaces", "identity")
    if os.path.isdir(id_dir):
        for fn in os.listdir(id_dir):
            if not fn.endswith(".identity.yaml"):
                continue
            p = os.path.join(id_dir, fn)
            text = open(p, encoding="utf-8").read()
            new = re.sub(r"(?m)^agent:\s*.*$", f"agent: {name}", text)
            if new != text:
                open(p, "w", encoding="utf-8").write(new)
                changes.append(f"identity agent: -> {name}")
            target_fn = f"{handle}.identity.yaml"
            if fn != target_fn:
                os.rename(p, os.path.join(id_dir, target_fn))
                changes.append(f"identity file -> {target_fn}")

    # 3. README/AGENTS + .claude md: swap the placeholder token for the trainee name.
    for rel in ("README.md", "AGENTS.md",
                ".claude/instructions.md", ".claude/guardrails.md",
                ".claude/output-format.md"):
        p = os.path.join(repo_dir, rel)
        if not os.path.isfile(p):
            continue
        text = open(p, encoding="utf-8").read()
        new = re.sub(rf"\b{PLACEHOLDER}\b", name, text)
        if new != text:
            open(p, "w", encoding="utf-8").write(new)
            changes.append(f"{rel}: {PLACEHOLDER} -> {name}")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description="Personalise a trainee repo (R-8)")
    ap.add_argument("--repo", default=os.getcwd(), help="trainee repo dir (default: CWD)")
    ap.add_argument("--handle", help="trainee handle (default: from repo name)")
    args = ap.parse_args()

    handle = args.handle or _derive_handle(args.repo)
    if not handle:
        print("Error: could not derive handle from repo name "
              "(expected podzone-training-<handle>); pass --handle.", file=sys.stderr)
        return 2

    changes = personalise(args.repo, handle)
    if changes:
        print(f"Personalised for '{handle}':")
        for c in changes:
            print(f"  - {c}")
    else:
        print(f"Already personalised for '{handle}' (no changes).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
