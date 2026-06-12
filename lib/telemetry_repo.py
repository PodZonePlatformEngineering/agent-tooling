"""
telemetry_repo.py — git-versioned JSONL backstop (PROJ-039 R-015 / AC-011, § 2.5).

`agent-telemetry.git` is a git repo over the Claude session-log directory
(``~/.claude/projects/`` by default, overridable via ``PODZONE_TELEMETRY_REPO``).
The session-end hook commits and pushes after each session; the committed JSONL is
the durable, immutable audit backstop (C-007) that makes R-013 raw-event deletion
safe — the raw events survive in committed JSONL even after the mutable Qdrant
points are pruned.

This module is the *behaviour* the DTD requires (commit + push, clone-recoverable);
it is deliberately git-shape-agnostic (the "init in place" vs "sibling + symlink"
choice of § 2.5 is the operator's). Stdlib + git subprocess only — no dependency.

Push failure is **non-fatal to the session but blocks raw-event deletion**
(§ 2.4 step 5 / C-006): the caller must check ``pushed`` before pruning.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

DEFAULT_REPO = os.path.expanduser("~/.claude/projects")


def _git(repo_dir: str, *args: str, check: bool = True,
         timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_dir, *args],
        capture_output=True, text=True, timeout=timeout, check=check,
    )


def resolve_repo_dir(repo_dir: Optional[str] = None) -> str:
    return repo_dir or os.environ.get("PODZONE_TELEMETRY_REPO") or DEFAULT_REPO


def ensure_repo(repo_dir: Optional[str] = None, *, remote: Optional[str] = None,
                branch: str = "main") -> dict:
    """Ensure ``repo_dir`` is a git repo on ``branch`` with ``remote`` as origin.

    Idempotent. Returns ``{"repo_dir", "initialised", "ok"}``. Never raises for
    an already-correct repo; a genuine git failure propagates.
    """
    repo_dir = resolve_repo_dir(repo_dir)
    Path(repo_dir).mkdir(parents=True, exist_ok=True)
    initialised = False
    if not (Path(repo_dir) / ".git").exists():
        _git(repo_dir, "init")
        _git(repo_dir, "checkout", "-B", branch, check=False)
        initialised = True
    if remote:
        existing = _git(repo_dir, "remote", check=False)
        if "origin" not in (existing.stdout or "").split():
            _git(repo_dir, "remote", "add", "origin", remote, check=False)
        else:
            _git(repo_dir, "remote", "set-url", "origin", remote, check=False)
    return {"repo_dir": repo_dir, "initialised": initialised, "ok": True}


def commit_and_push(
    session_id: str,
    *,
    repo_dir: Optional[str] = None,
    date: Optional[str] = None,
    branch: str = "main",
    push: bool = True,
) -> dict:
    """``git add -A`` → commit → push (§ 2.5). Commit is no-op-safe when nothing
    changed. Returns ``{"committed", "pushed", "reason", "repo_dir"}``.

    ``pushed`` is the gate the caller checks before pruning raw events (C-006):
    a push failure leaves ``pushed=False`` and the backstop incomplete, so the
    source must not be deleted.
    """
    repo_dir = resolve_repo_dir(repo_dir)
    result = {"committed": False, "pushed": False, "reason": "", "repo_dir": repo_dir}

    if not (Path(repo_dir) / ".git").exists():
        result["reason"] = "not a git repo (call ensure_repo first)"
        return result

    try:
        _git(repo_dir, "add", "-A")
        msg = f"session {session_id} {date or ''}".strip()
        commit = _git(repo_dir, "commit", "-m", msg, check=False)
        # commit returns non-zero when there is nothing to commit — that's fine.
        result["committed"] = commit.returncode == 0
        if commit.returncode != 0 and "nothing to commit" not in (
            commit.stdout + commit.stderr
        ):
            result["reason"] = f"commit failed: {commit.stderr.strip()}"
            return result
    except Exception as exc:
        result["reason"] = f"commit error: {exc}"
        return result

    if not push:
        result["reason"] = "push disabled"
        return result

    try:
        pushed = _git(repo_dir, "push", "origin", branch, check=False)
        result["pushed"] = pushed.returncode == 0
        if pushed.returncode != 0:
            result["reason"] = f"push failed: {pushed.stderr.strip()}"
    except Exception as exc:
        result["reason"] = f"push error: {exc}"
    return result
