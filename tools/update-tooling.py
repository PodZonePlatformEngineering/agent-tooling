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
  1. refuse if the home repo's working tree is dirty **within the sync's own
     write-set** (beyond a stray ``.DS_Store``) — a self-update must never
     ride on top of uncommitted work in the paths it is about to overwrite
     (``.claude/hooks|primitives|lib|skills|tools``, ``.claude/settings.json``,
     ``.claude/tooling-manifest.json``, root ``.gitignore``; see
     ``WRITE_SET_PREFIXES``). Dirt OUTSIDE the write-set — ``logs/**`` above
     all, self-dirtied by concurrently-running sibling SessionStart hooks
     (PROJ-039/T-092: every command in a SessionStart hook array runs
     concurrently, not serialised by position — ``session-start.sh``'s first
     ``log_primitive`` line can land in ``logs/primitives.log`` before this
     script's own ``git status`` scan runs) — is reported but never blocks
     the update.
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
  6. one-time untracking migration (PROJ-039/T-069, T-065 F6): any file the
     freshly-synced ``.gitignore`` now covers but git still tracks (the T-068
     log files in every pre-1.3.2 clone) is ``git rm --cached``-ed so the
     divergence class it names (return_to_main conflicts on shared live logs)
     ends here rather than lying latent. Idempotent — a clean repo is a no-op.
  7. if the sync (or the untracking) produced a diff, commit it on the CURRENT
     branch (the session branch when run at session start) — this rides the
     session/result PR to `main` exactly like any other in-session commit
     (PROJ-039/T-060).
  8. delete the ``.workspace/agent-tooling`` clone.

Outcome sentinel (PROJ-039/T-069, T-065 F1/F12): whenever ``TOOLING_UPDATE``
is set, the run's outcome — success OR refusal — is written to
``.workspace/.tooling-update-status.json``, keyed to the startup's
``session_id`` (read from the SessionStart hook stdin JSON; empty on a manual
run). session-start.sh guards on it: env set but no ok:true sentinel for THIS
session id → HALT loudly. That kills the silent-no-op class the T-065
shakedown caught live (env set + updater unwired = stale tooling while the
brief's audit field claims the new version was delivered).

All refusals are loud (printed to both stdout — so the agent sees it in the
SessionStart context — and stderr) but the process always exits 0: a
SessionStart hook must never break startup (same contract as
session-materialise.py's HALT).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_REMOTE = "https://github.com/PodZonePlatformEngineering/agent-tooling.git"
_TAG_RE = re.compile(r"^refs/tags/(v\d+\.\d+\.\d+)$")
_ROLE_RE = re.compile(r"^role_class:\s*.*roles/([^/]+)/?\s*$")

# PROJ-039/T-092: the exact set of paths sync-agent-tooling.sh writes to —
# hooks/primitives/lib/skills/tools under .claude/, the settings.json wiring,
# the shipped tooling-manifest.json itself, and the root .gitignore (mirrors
# sync-agent-tooling.sh's HOOKS_DST/DEP_DIRS/TOOLS_DST/GITIGNORE_DST +
# the tooling-manifest.json `files`/`root_files` write, kept in lockstep by
# hand since the sync writes the manifest AFTER the dirty check, so the
# manifest cannot be read as the source of truth for its own guard). Dirt
# ANYWHERE ELSE (logs/**, .workspace/, results/, etc.) must never refuse a
# self-update — those paths are never touched by the sync, and logs/** in
# particular is self-dirtied by concurrently-running sibling SessionStart
# hooks (T-048 committed logs) on every single launch.
WRITE_SET_PREFIXES = (
    ".claude/hooks/",
    ".claude/primitives/",
    ".claude/lib/",
    ".claude/skills/",
    ".claude/tools/",
    ".claude/settings.json",
    ".claude/tooling-manifest.json",
    ".claude/current-agent",  # deleted by the T-099 retired-identity prune
    ".gitignore",
)


class UpdateRefusal(Exception):
    """A loud, halting refusal — dirty tree / unknown tag / VERSION mismatch /
    sync failure. Caught by main(); never propagates past the hook process."""


def _run(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def _git(repo: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return _run(["git", "-C", str(repo), *args], check=check)


def working_tree_dirty(repo: str) -> bool:
    """True if `repo` carries any uncommitted change beyond a stray .DS_Store
    (macOS cruft that must never block a launch — mirrors lib.session_guard).
    Unscoped — used post-sync to detect whether the sync itself produced a
    diff to commit, where "any change at all" is exactly the right question.
    The pre-sync refusal gate uses the scoped `dirty_write_set_paths` instead
    (§ WRITE_SET_PREFIXES, PROJ-039/T-092)."""
    cp = _git(repo, "status", "--porcelain", check=False)
    for line in (cp.stdout or "").splitlines():
        if not line.strip():
            continue
        if line.startswith("?? ") and os.path.basename(line[3:].strip()) == ".DS_Store":
            continue
        return True
    return False


def _porcelain_path(line: str) -> str:
    """Extract the path from one `git status --porcelain` line — handles the
    ` -> ` rename form (destination path is what matters) and quoted paths."""
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1]
    return path


def _in_write_set(path: str) -> bool:
    return any(
        path == prefix.rstrip("/") or path.startswith(prefix)
        for prefix in WRITE_SET_PREFIXES
    )


def dirty_write_set_paths(repo: str) -> tuple[list[str], list[str]]:
    """Split `repo`'s dirt (`git status --porcelain`, stray `.DS_Store`
    excluded) into (write_set, ignored) — paths inside the sync's own
    write-set (§ WRITE_SET_PREFIXES) vs. everywhere else. Only `write_set`
    dirt is grounds for refusal (PROJ-039/T-092)."""
    # `--untracked-files=all` (not the default "normal"): an untracked
    # directory must be listed file-by-file, not summarised as one `?? dir/`
    # line — otherwise an untracked `.claude/hooks/` (or `logs/`) collapses to
    # a single entry and prefix-matching against WRITE_SET_PREFIXES can't tell
    # a write-set file from a sibling one under the same never-yet-tracked dir.
    cp = _git(repo, "status", "--porcelain", "--untracked-files=all", check=False)
    write_set: list[str] = []
    ignored: list[str] = []
    for line in (cp.stdout or "").splitlines():
        if not line.strip():
            continue
        path = _porcelain_path(line)
        if line.startswith("?? ") and os.path.basename(path) == ".DS_Store":
            continue
        (write_set if _in_write_set(path) else ignored).append(path)
    return write_set, ignored


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


def untrack_newly_ignored(home_repo: str) -> list[str]:
    """One-time untracking migration (PROJ-039/T-069, T-065 F6): a sync can
    deliver a ``.gitignore`` rule but git keeps already-tracked files tracked,
    so every pre-T-068 clone carries the tracked-live-log divergence latently
    (Athena's manual fix, home-training-athena ``ef628be``, is the reference).
    ``git rm --cached`` every tracked file the CURRENT ignore rules cover; the
    staged deletions ride the same update commit. Returns the untracked paths
    (empty on a clean repo — idempotent)."""
    cp = _git(home_repo, "ls-files", "--cached", "--ignored", "--exclude-standard", check=False)
    files = [line for line in (cp.stdout or "").splitlines() if line.strip()]
    if files:
        _git(home_repo, "rm", "--cached", "--quiet", "--", *files)
    return files


def run_update(home_repo: str, role: str, requested: str, *, remote: str = DEFAULT_REMOTE) -> dict:
    """Execute the self-update flow (§ module docstring). Returns a result dict
    on success; raises UpdateRefusal on any refusal condition."""
    write_set_dirt, ignored_dirt = dirty_write_set_paths(home_repo)
    if write_set_dirt:
        raise UpdateRefusal(
            "dirty-tree: refusing to self-update over uncommitted changes in "
            f"the sync write-set: {', '.join(write_set_dirt)}"
        )

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

    untracked = untrack_newly_ignored(home_repo)

    changed = working_tree_dirty(home_repo)
    if changed:
        _git(home_repo, "add", "-A")
        msg = f"chore(update-tooling): sync agent-tooling to {tag} (role={role})"
        if untracked:
            msg += f"\n\nuntracked newly-ignored files (T-069/F6 migration): {', '.join(untracked)}"
        _git(home_repo, "commit", "-m", msg)

    shutil.rmtree(clone_dir, ignore_errors=True)

    commit = _git(home_repo, "rev-parse", "HEAD", check=False).stdout.strip()
    return {"ok": True, "tag": tag, "role": role, "changed": changed, "commit": commit,
            "untracked": untracked, "sync_output": sync_output, "ignored_dirt": ignored_dirt}


def _resolve_home_repo() -> str:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if project_dir:
        return project_dir
    cp = _run(["git", "rev-parse", "--show-toplevel"], check=False)
    return cp.stdout.strip() or os.getcwd()


def _read_session_id_from_stdin() -> str:
    """The SessionStart hook stdin JSON carries the session_id the sentinel is
    keyed to. A manual (terminal) run has no hook payload — return "" so the
    guard never mistakes a pre-launch manual run for this startup's run."""
    try:
        if sys.stdin.isatty():
            return ""
        raw = sys.stdin.read()
        if not raw.strip():
            return ""
        return str(json.loads(raw).get("session_id", "") or "")
    except Exception:
        return ""


def write_sentinel(home_repo: str, payload: dict) -> None:
    """Best-effort outcome sentinel at .workspace/.tooling-update-status.json
    (.workspace/ is gitignored — never rides a commit). session-start.sh's
    T-069 guard reads it; the orientation ritual refuses on ok:false."""
    try:
        ws = Path(home_repo) / ".workspace"
        ws.mkdir(parents=True, exist_ok=True)
        payload = dict(payload)
        payload.setdefault("written_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        (ws / ".tooling-update-status.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as exc:  # sentinel failure must never break startup
        print(f"update-tooling: sentinel write failed: {exc}", file=sys.stderr)


def main() -> int:
    requested = os.environ.get("TOOLING_UPDATE", "").strip()
    if not requested:
        return 0  # no-op — the default for every ordinary launch (no sentinel)

    home_repo = _resolve_home_repo()
    remote = os.environ.get("AGENT_TOOLING_REMOTE", "").strip() or DEFAULT_REMOTE
    session_id = _read_session_id_from_stdin()

    try:
        role = os.environ.get("TOOLING_UPDATE_ROLE", "").strip() or detect_role(home_repo)
        result = run_update(home_repo, role, requested, remote=remote)
    except UpdateRefusal as exc:
        write_sentinel(home_repo, {
            "ok": False, "reason": str(exc), "requested": requested,
            "session_id": session_id,
        })
        msg = f"⛔ update-tooling REFUSED ({home_repo}): {exc}"
        print(msg, file=sys.stderr)
        print(msg)
        return 0  # a SessionStart hook must never break startup

    write_sentinel(home_repo, {
        "ok": True, "requested": requested, "tag": result["tag"],
        "role": result["role"], "changed": result["changed"],
        "commit": result["commit"], "untracked": result["untracked"],
        "ignored_dirt": result["ignored_dirt"],
        "session_id": session_id,
    })
    msg = (
        f"✅ update-tooling: {home_repo} synced to {result['tag']} "
        f"(role={result['role']}) — "
        f"{'committed ' + result['commit'][:8] if result['changed'] else 'already current, no-op'}"
    )
    if result["ignored_dirt"]:
        msg += f" (ignored out-of-write-set dirt: {', '.join(result['ignored_dirt'])})"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
