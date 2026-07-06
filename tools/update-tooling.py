#!/usr/bin/env python3
"""
update-tooling.py — brief-gated home-repo self-update (PROJ-039/T-056).

Resident, **role-neutral** tool shipped at ``.claude/tools/update-tooling.py``
in every home repo (NOT a skill — build agents are hooks-only; a thin skill
wrapper may be added for the team-lead variant only). Wired as the FIRST
SessionStart hook command (before session-start.sh / session-materialise.py)
so it can update the very hook set that runs after it, with no ordering
coupling to materialise.

Trigger: the ``TOOLING_UPDATE`` env var — ``"<tag>"`` (e.g. ``"v1.1.0"``) or
``"latest"`` — set the same way ``BRIEF_ID`` is (inline on the launch command,
or a ``settings.local.json`` ``env``-only block; see skills/launch-session/
SKILL.md). This is the deliberate design choice for "visible at session
start" (brief §1): the brief also carries a ``tooling_update`` payload field
(create-brief.py --tooling-update, brief_substrate.create_brief) as the
durable audit record of what a dispatch asked for, and materialise surfaces
it into ``.workspace/identity.json`` for visibility — but that path requires
a Qdrant round trip AND runs after this tool in the hook chain, so it is never
the live trigger. ``TOOLING_UPDATE`` unset → no-op, exit 0 (every ordinary
launch).

Flow:
  1. refuse if the home repo's working tree is dirty (beyond a stray
     ``.DS_Store``) — a self-update must never ride on top of uncommitted work.
  2. resolve the tag: ``git ls-remote --tags`` (no clone) against the canonical
     agent-tooling remote; ``"latest"`` -> highest semver tag.
  3. shallow-clone agent-tooling at that tag into ``.workspace/agent-tooling``
     (deleted again at the end — never left resident).
  4. refuse if the clone's own ``VERSION`` file doesn't match the resolved tag
     (protects against a mistagged release).
  5. run the clone's ``sync-agent-tooling.sh --role {role} --yes`` against the
     home repo — this both applies the update AND re-proves the byte-identity
     invariant; a sync failure (byte-identity FAIL or script error) is a
     refusal, not a partial apply.
  6. if the sync produced a diff, commit it on the CURRENT branch (the session
     branch when run at session start) — this rides the session/result PR to
     `main` exactly like any other in-session commit (PROJ-039/T-060).
  7. delete the ``.workspace/agent-tooling`` clone.

All refusals are loud (printed to both stdout — so the agent sees it in the
SessionStart context — and stderr) but the process always exits 0: a
SessionStart hook must never break startup (same contract as
session-materialise.py's HALT).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_REMOTE = "https://github.com/PodZonePlatformEngineering/agent-tooling.git"
_TAG_RE = re.compile(r"^refs/tags/(v\d+\.\d+\.\d+)$")
_ROLE_RE = re.compile(r"^role_class:\s*.*roles/([^/]+)/?\s*$")


class UpdateRefusal(Exception):
    """A loud, halting refusal — dirty tree / unknown tag / VERSION mismatch /
    sync failure. Caught by main(); never propagates past the hook process."""


def _run(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def _git(repo: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return _run(["git", "-C", str(repo), *args], check=check)


def working_tree_dirty(repo: str) -> bool:
    """True if `repo` carries any uncommitted change beyond a stray .DS_Store
    (macOS cruft that must never block a launch — mirrors lib.session_guard)."""
    cp = _git(repo, "status", "--porcelain", check=False)
    for line in (cp.stdout or "").splitlines():
        if not line.strip():
            continue
        if line.startswith("?? ") and os.path.basename(line[3:].strip()) == ".DS_Store":
            continue
        return True
    return False


def list_semver_tags(remote: str) -> list[str]:
    """All ``vX.Y.Z`` tags on `remote`, ascending, via `git ls-remote --tags`
    (no clone required to resolve)."""
    cp = _run(["git", "ls-remote", "--tags", remote], check=False)
    if cp.returncode != 0:
        raise UpdateRefusal(f"remote-unreachable: git ls-remote --tags {remote} failed: {cp.stderr.strip()}")
    tags: set[str] = set()
    for line in cp.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        m = _TAG_RE.match(parts[1])
        if m:
            tags.add(m.group(1))
    return sorted(tags, key=lambda t: tuple(int(x) for x in t.lstrip("v").split(".")))


def resolve_tag(requested: str, remote: str) -> str:
    """``"latest"`` -> highest semver tag; otherwise `requested` must be an
    exact existing tag. Raises UpdateRefusal on no match (`unknown-tag`)."""
    tags = list_semver_tags(remote)
    if not tags:
        raise UpdateRefusal(f"unknown-tag: no semver tags found on {remote!r}")
    if requested == "latest":
        return tags[-1]
    if requested not in tags:
        raise UpdateRefusal(
            f"unknown-tag: {requested!r} not found on {remote!r} (known: {tags})"
        )
    return requested


def detect_role(home_repo: str) -> str:
    """Auto-detect role-class from workspaces/identity/*.identity.yaml, mirroring
    sync-agent-tooling.sh's own auto-detect so the two never diverge."""
    ident_dir = Path(home_repo) / "workspaces" / "identity"
    if ident_dir.is_dir():
        for f in sorted(ident_dir.glob("*.identity.yaml")):
            for line in f.read_text(encoding="utf-8").splitlines():
                m = _ROLE_RE.match(line.strip())
                if m:
                    return m.group(1)
    raise UpdateRefusal(
        f"role-undetected: no workspaces/identity/*.identity.yaml with role_class "
        f"under {ident_dir} — pass TOOLING_UPDATE_ROLE explicitly"
    )


def run_update(home_repo: str, role: str, requested: str, *, remote: str = DEFAULT_REMOTE) -> dict:
    """Execute the self-update flow (§ module docstring). Returns a result dict
    on success; raises UpdateRefusal on any refusal condition."""
    if working_tree_dirty(home_repo):
        raise UpdateRefusal("dirty-tree: refusing to self-update over uncommitted changes")

    tag = resolve_tag(requested, remote)

    workspace = Path(home_repo) / ".workspace"
    clone_dir = workspace / "agent-tooling"
    if clone_dir.exists():
        shutil.rmtree(clone_dir, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)

    cp = _run(["git", "clone", "--depth", "1", "--branch", tag, remote, str(clone_dir)], check=False)
    if cp.returncode != 0:
        raise UpdateRefusal(f"clone-failed: git clone --branch {tag} {remote} failed: {cp.stderr.strip()}")

    version_file = clone_dir / "VERSION"
    clone_version = version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else ""
    if clone_version != tag.lstrip("v"):
        shutil.rmtree(clone_dir, ignore_errors=True)
        raise UpdateRefusal(
            f"version-mismatch: tag {tag!r} resolves to a clone whose VERSION is "
            f"{clone_version!r} — refusing a mistagged release"
        )

    sync_script = clone_dir / "sync-agent-tooling.sh"
    cp = _run(
        ["bash", str(sync_script), "--role", role,
         "--home-repo", str(home_repo), "--agent-tooling", str(clone_dir), "--yes"],
        check=False,
    )
    sync_output = (cp.stdout or "") + (cp.stderr or "")
    if cp.returncode != 0:
        shutil.rmtree(clone_dir, ignore_errors=True)
        raise UpdateRefusal(f"sync-failed (byte-identity FAIL or sync error):\n{sync_output}")

    changed = working_tree_dirty(home_repo)
    if changed:
        _git(home_repo, "add", "-A")
        _git(home_repo, "commit", "-m",
             f"chore(update-tooling): sync agent-tooling to {tag} (role={role})")

    shutil.rmtree(clone_dir, ignore_errors=True)

    commit = _git(home_repo, "rev-parse", "HEAD", check=False).stdout.strip()
    return {"ok": True, "tag": tag, "role": role, "changed": changed, "commit": commit,
            "sync_output": sync_output}


def _resolve_home_repo() -> str:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if project_dir:
        return project_dir
    cp = _run(["git", "rev-parse", "--show-toplevel"], check=False)
    return cp.stdout.strip() or os.getcwd()


def main() -> int:
    requested = os.environ.get("TOOLING_UPDATE", "").strip()
    if not requested:
        return 0  # no-op — the default for every ordinary launch

    home_repo = _resolve_home_repo()
    remote = os.environ.get("AGENT_TOOLING_REMOTE", "").strip() or DEFAULT_REMOTE

    try:
        role = os.environ.get("TOOLING_UPDATE_ROLE", "").strip() or detect_role(home_repo)
        result = run_update(home_repo, role, requested, remote=remote)
    except UpdateRefusal as exc:
        msg = f"⛔ update-tooling REFUSED ({home_repo}): {exc}"
        print(msg, file=sys.stderr)
        print(msg)
        return 0  # a SessionStart hook must never break startup

    msg = (
        f"✅ update-tooling: {home_repo} synced to {result['tag']} "
        f"(role={result['role']}) — "
        f"{'committed ' + result['commit'][:8] if result['changed'] else 'already current, no-op'}"
    )
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
