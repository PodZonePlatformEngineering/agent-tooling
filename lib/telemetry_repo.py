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
DEFAULT_REMOTE_ENV = "PODZONE_TELEMETRY_REMOTE"

# Scope (PROJ-039/T-032): the default telemetry repo IS the workstation-wide Claude
# session-log dir (``~/.claude/projects``), which also holds the operator's unrelated
# personal/other-project transcripts. The agent-telemetry backstop must carry only
# *agent* session transcripts, so ensure_repo lays down a .gitignore that ignores
# everything and re-includes only the agent session dirs. ``~/.claude/projects`` is
# flat (one dir per cwd, slug = cwd path with ``/`` -> ``-``), so a top-level allowlist
# is sufficient. Agent sessions always run under ``~/sessions/`` (launch-session
# worktrees) or the resident agent clones under ``~/workspace`` (home-* repos + the
# team repos + agent-tooling) — flat layout — or, increasingly, under the teamed
# layout ``~/workspace/{team}/…`` (home-* repos + the team repo, per team dir;
# training reorg + PROJ-043 both hand-patched the deployed .gitignore for this before
# it was canonical here). Both layouts coexist until T-104 retires the flat patterns
# fleet-wide. Everything else (personal code, cluster-repos, academy…) stays ignored
# and never leaves the workstation.
def _scope_patterns(home: Optional[str] = None) -> list[str]:
    home = home or os.path.expanduser("~")
    # Claude encodes the cwd's absolute path into the dir name by replacing every
    # "/" with "-", so the leading "/" becomes a LEADING "-": /Users/x -> -Users-x.
    pfx = home.replace("/", "-")  # e.g. -Users-martincolley
    return [
        "# agent-telemetry scope (PROJ-039/T-032): track AGENT session transcripts only.",
        "# Ignore everything, then re-include the agent session dirs. Personal/other-",
        "# project transcripts stay out of the backstop.",
        "/*",
        "!/.gitignore",
        f"!{pfx}-sessions-*/",            # launch-session worktrees (all agents)
        f"!{pfx}-workspace-home-*/",      # resident migrated home repos (flat layout)
        f"!{pfx}-workspace-podzoneTeam/",
        f"!{pfx}-workspace-trainingTeam/",
        f"!{pfx}-workspace-roadmapTeam/",
        f"!{pfx}-workspace-agent-tooling/",
        # Teamed workspace layout (~/workspace/{team}/…): agent repos under the team
        # dirs. Flat patterns above stay until T-104 retires them fleet-wide.
        f"!{pfx}-workspace-training-home-*/",
        f"!{pfx}-workspace-training-trainingTeam/",
        f"!{pfx}-workspace-roadmap-home-*/",
        f"!{pfx}-workspace-roadmap-roadmapTeam/",
        f"!{pfx}-workspace-podzone-home-*/",
        f"!{pfx}-workspace-podzone-podzoneTeam/",
        f"!{pfx}-workspace-podzone-agent-tooling/",
    ]


def _git(repo_dir: str, *args: str, check: bool = True,
         timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_dir, *args],
        capture_output=True, text=True, timeout=timeout, check=check,
    )


def resolve_repo_dir(repo_dir: Optional[str] = None) -> str:
    return repo_dir or os.environ.get("PODZONE_TELEMETRY_REPO") or DEFAULT_REPO


def resolve_remote(remote: Optional[str] = None) -> Optional[str]:
    return remote or os.environ.get(DEFAULT_REMOTE_ENV) or None


def write_scope_gitignore(repo_dir: str, *, force: bool = False) -> bool:
    """Write the agent-session scope .gitignore into ``repo_dir`` (default repo only).

    Returns True if (re)written. Never overwrites an existing .gitignore unless
    ``force`` — an operator may have customised the scope. Only applied when
    ``repo_dir`` is the default ``~/.claude/projects`` (a custom, already-scoped
    telemetry dir is left untouched).
    """
    if os.path.realpath(repo_dir) != os.path.realpath(DEFAULT_REPO):
        return False
    gi = Path(repo_dir) / ".gitignore"
    if gi.exists() and not force:
        return False
    gi.write_text("\n".join(_scope_patterns()) + "\n", encoding="utf-8")
    return True


def ensure_repo(repo_dir: Optional[str] = None, *, remote: Optional[str] = None,
                branch: str = "main") -> dict:
    """Ensure ``repo_dir`` is a git repo on ``branch`` with ``remote`` as origin.

    Bootstraps the agent-telemetry backstop (PROJ-039/T-032): inits the repo if
    absent, lays down the agent-session scope .gitignore (default repo only), and
    wires ``origin`` to ``remote`` (resolved from ``PODZONE_TELEMETRY_REMOTE`` if not
    passed). Idempotent — safe to call lazily on every session-end (self-heal for
    existing repos) and at scaffold time (new home repos). Returns
    ``{"repo_dir", "initialised", "remote", "ok"}``. Never raises for an
    already-correct repo; a genuine git failure propagates.
    """
    repo_dir = resolve_repo_dir(repo_dir)
    remote = resolve_remote(remote)
    Path(repo_dir).mkdir(parents=True, exist_ok=True)
    initialised = False
    if not (Path(repo_dir) / ".git").exists():
        _git(repo_dir, "init")
        _git(repo_dir, "checkout", "-B", branch, check=False)
        initialised = True
    # Scope BEFORE any add/commit so personal transcripts are never staged.
    write_scope_gitignore(repo_dir)
    if remote:
        existing = _git(repo_dir, "remote", check=False)
        if "origin" not in (existing.stdout or "").split():
            _git(repo_dir, "remote", "add", "origin", remote, check=False)
        else:
            _git(repo_dir, "remote", "set-url", "origin", remote, check=False)
    return {"repo_dir": repo_dir, "initialised": initialised,
            "remote": remote or "", "ok": True}


def copy_session_logs(home_repo: str, transcript_path: str, session_id: str) -> list[str]:
    """Copy THIS session's sid-keyed logs (``{home_repo}/logs/*-{sid8}.{log,jsonl}``)
    alongside its transcript JSONL in the telemetry repo (PROJ-039/T-093 item 4).

    ``logs/`` durability for agent home repos moved here from the retired
    committed-logs machinery (``session_guard.stage_session_logs`` no longer runs
    on the home-repo result paths — see ``scaffold/gitignore.template``); this is
    the replacement: best-effort, same repo/push as the transcript, so a failure
    here must not fail the finalise. ``transcript_path``'s directory is this
    session's per-cwd dir under the telemetry repo (already re-included by the
    scope .gitignore, since the transcript itself lives there) — logs land in the
    same directory rather than needing a new scope pattern.

    Returns the destination paths written (``[]`` if there is nothing to copy —
    not an error).
    """
    sid8 = (session_id or "")[:8]
    if not sid8 or not home_repo or not transcript_path:
        return []
    src_logs = Path(home_repo) / "logs"
    if not src_logs.is_dir():
        return []
    dest_dir = Path(transcript_path).parent
    written: list[str] = []
    try:
        matches = sorted(
            p for p in src_logs.iterdir()
            if p.is_file() and sid8 in p.name and p.suffix in (".log", ".jsonl")
        )
    except Exception:
        return []
    for p in matches:
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dst = dest_dir / p.name
            dst.write_bytes(p.read_bytes())
            written.append(str(dst))
        except Exception:
            continue
    return written


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
