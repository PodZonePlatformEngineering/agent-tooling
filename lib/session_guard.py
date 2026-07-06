"""session_guard.py — serial simple-repo mode guards (PROJ-039/T-045, CC-345).

With worktree isolation retired, sessions run directly in the **primary clone**
(``~/workspace/{repo}``) on a session branch. Isolation no longer enforces serial
safety — these three guards do, at the two lifecycle edges plus a lock:

  * :func:`preflight` — launch-time, **before** branching. The clone must be clean
    and on ``main``; fetch + ff-only. A leftover session branch from a prior crash
    HALTs with a recovery message — **unless** the T-030 finalise ledger shows that
    clone's last session already finalised, in which case the branch is a harmless
    leftover (the end-guard's delete step didn't land) and is auto-recovered by
    resetting to ``main``.
  * :func:`return_to_main` — session-end (finalise), **after** PRs are pushed: ff the
    clone to ``origin/main`` and delete the local session branch. A crash before this
    leaves the branch in place — :func:`preflight` catches it at the next launch.
  * :class:`SessionLock` — one-session-at-a-time per clone
    (``~/.claude/session-locks/{repo}.lock``), so a concurrent launch against the
    same clone refuses cleanly. Cheap insurance for the serial assumption.

Strict where it guards correctness (:func:`preflight` HALTs rather than branch off an
unsafe clone); best-effort where it must not break teardown (:func:`return_to_main`
and the linked-worktree escape hatch for the legacy path). Stdlib-only; the only
``lib/`` import is :mod:`finalise_ledger` (itself a closure leaf).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:  # closure-leaf import; both live in .claude/lib/ in a home repo
    from lib import finalise_ledger
except Exception:  # pragma: no cover - direct-run / relocated
    import finalise_ledger  # type: ignore


# A session branch is either the home/PAT form ``session/{agent}-{date}-{slug}``
# or a task-repo form ``{agent}/{YYYY-MM-DD}-{slug}``. Both are safe to delete once
# their work is pushed; ``main`` and long-lived branches are not.
_SESSION_BRANCH_RE = re.compile(r"^session/.+|^[a-z0-9][a-z0-9-]*/\d{4}-\d{2}-\d{2}-.+")

LOCK_DIR = Path.home() / ".claude" / "session-locks"


# ---------------------------------------------------------------------------
# Home-repo resolution (PROJ-039/T-054 — the operator-clone hijack class)
# ---------------------------------------------------------------------------
#
# The finalise must operate on the AGENT'S OWN HOME REPO regardless of where the
# shell happened to end. In serial simple-repo mode an agent `cd`s freely inside
# the primary clone — notably into a `.workspace/<subrepo>` clone it stood up for
# the task. The SessionEnd hook receives that wandered path as stdin `cwd`. Deriving
# the finalise target from bare `cwd` then made it mutate the WRONG clone: Athena
# T-026 (sid 2b5156d0) ended in `.workspace/academy-admin`, `return_to_main` ran
# against that clone, the result PR was raised from the operator's live apex clone,
# and the real home repo stayed on its session branch with the lock held.
#
# `CLAUDE_PROJECT_DIR` is the load-bearing anchor: the CLI sets it to the launch /
# project dir (the home repo) at startup and it does NOT move when the shell cd's
# into a subdirectory (that is exactly the T-050 wiring the hook command relies on).
# We prefer it, then fall back to stripping any `.workspace/` tail off `cwd`, and
# only use bare `cwd` as a last resort — but NEVER a bare `.workspace/*` subrepo.

_WORKSPACE_MARKER = os.sep + ".workspace"


def strip_workspace_subrepo(path: str) -> str:
    """Return ``path`` with any ``/.workspace``(``/…``) tail removed.

    A finalise that resolves to ``…/home-x/.workspace/academy-admin`` (the shell
    ended inside a stood-up subrepo) must operate on ``…/home-x`` — the clone that
    OWNS the ``.workspace/``. Idempotent for a path that carries no ``.workspace``
    segment (returns it unchanged, sans trailing slash)."""
    path = (path or "").rstrip("/")
    i = path.find(_WORKSPACE_MARKER + os.sep)
    if i != -1:
        return path[:i]
    if path.endswith(_WORKSPACE_MARKER):
        return path[: -len(_WORKSPACE_MARKER)]
    return path


def resolve_home_repo(cwd: str = "", *, env: Optional[dict] = None) -> str:
    """Resolve the authoritative home-repo dir for a finalise (PROJ-039/T-054).

    Priority:
      1. ``$CLAUDE_PROJECT_DIR`` — the CLI's fixed launch/project anchor; survives
         the shell cd'ing into a ``.workspace/`` subrepo.
      2. ``cwd`` with any ``.workspace/`` tail stripped — defence-in-depth for a
         finalise where ``CLAUDE_PROJECT_DIR`` is somehow unset.

    NEVER returns a bare ``.workspace/*`` subrepo path (the hijack class). The
    result is also ``.workspace``-stripped in case the anchor itself points inside
    one. Pure — no I/O — so the resolution is fully unit-testable."""
    env = env if env is not None else os.environ
    proj = (env.get("CLAUDE_PROJECT_DIR") or "").strip()
    if proj:
        return strip_workspace_subrepo(proj)
    return strip_workspace_subrepo(cwd or "")


# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------

def _git(repo_dir: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True, text=True, check=False,
    )


def _out(cp: subprocess.CompletedProcess) -> str:
    return (cp.stdout or "").strip()


def is_session_branch(name: str) -> bool:
    """True if ``name`` looks like a per-session branch (safe to reap once pushed)."""
    return bool(name) and name != "main" and bool(_SESSION_BRANCH_RE.match(name))


def current_branch(repo_dir: str) -> str:
    """Current branch name, or ``""`` if detached / not a git repo."""
    return _out(_git(repo_dir, "branch", "--show-current"))


def is_linked_worktree(repo_dir: str) -> bool:
    """True if ``repo_dir`` is a *linked* git worktree (the legacy ``~/sessions/``
    path), not the primary clone. In a linked worktree the per-worktree git dir
    (``…/.git/worktrees/<name>``) differs from the shared common dir (``…/.git``)."""
    gd = _out(_git(repo_dir, "rev-parse", "--absolute-git-dir"))
    gc_raw = _out(_git(repo_dir, "rev-parse", "--git-common-dir"))
    if not gd or not gc_raw:
        return False
    gc = gc_raw if os.path.isabs(gc_raw) else os.path.join(str(repo_dir), gc_raw)
    return os.path.realpath(gd) != os.path.realpath(gc)


def working_tree_dirty(repo_dir: str) -> bool:
    """True if the tree carries any uncommitted change beyond an untracked
    ``.DS_Store`` (macOS cruft that must never block a launch — Step 3a rule)."""
    porcelain = _out(_git(repo_dir, "status", "--porcelain"))
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        # Untracked .DS_Store ("?? …/.DS_Store") is benign; anything else is dirt.
        if line.startswith("?? ") and os.path.basename(line[3:].strip()) == ".DS_Store":
            continue
        return True
    return False


def _branch_pushed(repo_dir: str, branch: str) -> bool:
    """True if ``branch``'s tip is reachable from a remote ref (pushed / merged) —
    so deleting the local branch cannot lose unpushed work."""
    tip = _out(_git(repo_dir, "rev-parse", "--verify", branch))
    if not tip:
        return False
    # Any remote ref (origin/main or origin/<branch>) that contains the tip means
    # the commit is on the remote already.
    contains = _git(repo_dir, "branch", "-r", "--contains", tip)
    return contains.returncode == 0 and bool(_out(contains))


def ff_main(repo_dir: str) -> dict:
    """Fetch origin and fast-forward the local ``main`` to ``origin/main``.

    ``--ff-only`` refuses to merge a diverged local main (unpushed commits) rather
    than papering over it with a merge commit — a diverged main is an anomaly to
    surface, not resolve here. Returns ``{"ok", "reason"}``."""
    fetch = _git(repo_dir, "fetch", "origin", "main")
    if fetch.returncode != 0:
        return {"ok": False, "reason": f"fetch failed: {fetch.stderr.strip()}"}
    co = _git(repo_dir, "checkout", "main")
    if co.returncode != 0:
        return {"ok": False, "reason": f"checkout main failed: {co.stderr.strip()}"}
    ff = _git(repo_dir, "merge", "--ff-only", "origin/main")
    if ff.returncode != 0:
        return {"ok": False,
                "reason": f"ff-only merge failed (main diverged?): {ff.stderr.strip()}"}
    return {"ok": True, "reason": "main fast-forwarded to origin/main"}


# ---------------------------------------------------------------------------
# T-030 finalise-ledger recovery signal
# ---------------------------------------------------------------------------

def _latest_ledger_entry_for_cwd(repo_dir: str) -> Optional[dict]:
    """Return the most-recently-updated finalise-ledger entry whose ``cwd`` is this
    clone, or ``None``. The ledger keys the last session that ran in ``repo_dir``;
    its ``complete`` flag tells preflight whether a leftover branch is safe."""
    target = os.path.realpath(str(repo_dir))
    best: Optional[dict] = None
    for _sid, entry in finalise_ledger.load().items():
        if not isinstance(entry, dict):
            continue
        cwd = entry.get("cwd")
        if not cwd or os.path.realpath(cwd) != target:
            continue
        if best is None or str(entry.get("updated_ts", "")) > str(best.get("updated_ts", "")):
            best = entry
    return best


def _clone_last_session_finalised(repo_dir: str) -> bool:
    """True if the ledger shows this clone's most recent session reached ``complete``
    — i.e. a leftover session branch is a benign un-deleted remnant, not live work."""
    entry = _latest_ledger_entry_for_cwd(repo_dir)
    return bool(entry and entry.get("complete"))


# ---------------------------------------------------------------------------
# Launch-time guard (before branching)
# ---------------------------------------------------------------------------

def preflight(repo_dir: str, *, auto_recover: bool = True) -> dict:
    """Assert ``repo_dir`` is safe to branch a new session from, and leave it on a
    fast-forwarded ``main``.

    Returns ``{"decision", "reason", "branch", "message"}`` where ``decision`` is:

      * ``ready``     — clean + on main (ff'd); branch away.
      * ``recovered`` — a leftover session branch whose session already finalised
        (ledger ``complete``) was reset to a ff'd ``main``.
      * ``halt``      — dirty tree, a leftover session branch with unfinalised work,
        or an unexpected branch. ``message`` carries operator recovery guidance;
        **do not branch**.

    Never mutates on a ``halt`` — the clone is left exactly as found for inspection.
    """
    branch = current_branch(repo_dir)
    if not branch:
        return {"decision": "halt", "reason": "detached-head",
                "branch": "", "message":
                f"{repo_dir} is in a detached-HEAD state. Restore with "
                f"`git -C {repo_dir} checkout main` and re-launch."}

    # A dirty tree blocks regardless of branch — never branch over uncommitted work.
    if working_tree_dirty(repo_dir):
        return {"decision": "halt", "reason": "dirty-tree", "branch": branch,
                "message":
                f"{repo_dir} has uncommitted changes (on '{branch}'). Commit, stash, "
                f"or discard them, then re-launch. `git -C {repo_dir} status`."}

    if branch == "main":
        ff = ff_main(repo_dir)
        if not ff["ok"]:
            return {"decision": "halt", "reason": "ff-failed", "branch": "main",
                    "message": f"{repo_dir}: {ff['reason']}. Resolve the diverged main "
                               f"before launching."}
        return {"decision": "ready", "reason": "clean-on-main", "branch": "main",
                "message": "clone clean on a fast-forwarded main — ready to branch"}

    # On a non-main branch. A leftover *session* branch is a prior-crash signature.
    if is_session_branch(branch):
        if auto_recover and _clone_last_session_finalised(repo_dir):
            ff = ff_main(repo_dir)  # checks out main + ff
            if not ff["ok"]:
                return {"decision": "halt", "reason": "recover-ff-failed", "branch": branch,
                        "message": f"{repo_dir}: leftover branch '{branch}' from a finalised "
                                   f"session, but {ff['reason']}."}
            if _branch_pushed(repo_dir, branch):
                _git(repo_dir, "branch", "-D", branch)
            return {"decision": "recovered", "reason": "finalised-leftover-branch",
                    "branch": branch,
                    "message": f"leftover session branch '{branch}' (its session had "
                               f"finalised) reset to a ff'd main — recovered"}
        return {"decision": "halt", "reason": "unfinalised-session-branch", "branch": branch,
                "message":
                f"{repo_dir} is on session branch '{branch}' with no completed finalise on "
                f"record — a prior session likely crashed mid-work. Inspect it, finalise or "
                f"discard it, return the clone to main, then re-launch:\n"
                f"  git -C {repo_dir} status\n"
                f"  git -C {repo_dir} checkout main && git -C {repo_dir} branch -D {branch}"}

    return {"decision": "halt", "reason": "unexpected-branch", "branch": branch,
            "message":
            f"{repo_dir} is on '{branch}', not main and not a recognised session branch. "
            f"Return it to main before launching: `git -C {repo_dir} checkout main`."}


# ---------------------------------------------------------------------------
# Session-end guard (after PRs pushed) — return the clone to main
# ---------------------------------------------------------------------------

def return_to_main(repo_dir: str, session_branch: Optional[str] = None) -> dict:
    """Session-end guard: return ``repo_dir`` to a ff'd ``main`` and delete the local
    session branch (its work is already pushed as a PR). No-op in the legacy linked
    worktree path — there the primary clone was never branched, and consolidate reaps
    the worktree. Returns ``{"ok", "disposition", "branch", "reason"}``.

    Best-effort: any failure yields ``ok=False`` but never raises — teardown must not
    be broken by a guard.
    """
    result = {"ok": False, "disposition": "noop", "branch": session_branch or "",
              "reason": ""}
    try:
        if is_linked_worktree(repo_dir):
            result.update(ok=True, disposition="skipped-worktree",
                          reason="legacy linked worktree — primary clone untouched")
            return result

        branch = session_branch or current_branch(repo_dir)
        result["branch"] = branch
        if branch == "main" or not branch:
            result.update(ok=True, disposition="already-main",
                          reason="clone already on main")
            return result

        ff = ff_main(repo_dir)  # checks out main + ff to origin/main
        if not ff["ok"]:
            result["reason"] = ff["reason"]
            return result

        if not is_session_branch(branch):
            result.update(ok=True, disposition="returned-kept-branch",
                          reason=f"returned to main; '{branch}' is not a session branch — kept")
            return result

        # Only delete once the branch is pushed/merged, so a missed push cannot lose work.
        if _branch_pushed(repo_dir, branch):
            _git(repo_dir, "branch", "-D", branch)
            result.update(ok=True, disposition="returned-branch-deleted",
                          reason=f"returned to main; deleted pushed session branch '{branch}'")
        else:
            result.update(ok=True, disposition="returned-branch-kept-unpushed",
                          reason=f"returned to main; kept '{branch}' — tip not on any remote "
                                 f"(unpushed work?)")
        return result
    except Exception as exc:  # never break teardown
        result["reason"] = f"return_to_main error: {exc}"
        return result


# ---------------------------------------------------------------------------
# Post-finalise log-tail commit (PROJ-039/T-063)
# ---------------------------------------------------------------------------

def commit_log_tail(repo_dir: str) -> dict:
    """Stage + commit any dirty *pure-log* tracked files as the finalise's true
    **last act**, so :func:`working_tree_dirty` is false when the hook exits.

    Since T-062 tracked ``logs/*.log`` (they ride the session-result PR as durable
    diagnostics), the finalise ledger ``complete()`` write and the trailing
    ``libraries-{sid8}.log`` lines land in already-tracked files *after*
    :func:`return_to_main` has reset the clone to ``main`` — leaving 2-3 modified
    tracked files behind. The next session's :func:`preflight` HALTs on that dirt
    (``working_tree_dirty`` blocks regardless of branch). This closes the gap by
    having the finalise commit its own tail rather than leaving it for a human
    (Hermes previously did this by hand — sid ``d350e898``, commit ``730fda1``).

    Call this ONLY after every other finalise step (including the last log line)
    has been written — anything logged after this runs re-dirties the tree.

    Refuses (``halt-non-log-dirt``, no commit made) if the dirty set contains
    anything outside ``logs/`` — that is real uncommitted work, not tail noise,
    and must surface to the operator via the normal ``preflight`` halt rather than
    being silently swept into a log-only commit. Best-effort: never raises. A push
    failure leaves the commit local (the working tree is still clean, which is all
    the next ``preflight`` requires) — non-fatal, since origin catches up next
    time this repo's main is pushed.
    """
    result = {"ok": False, "disposition": "noop", "reason": ""}
    try:
        if is_linked_worktree(repo_dir):
            result.update(ok=True, disposition="skipped-worktree",
                          reason="legacy linked worktree — primary clone untouched")
            return result
        if current_branch(repo_dir) != "main":
            result.update(ok=True, disposition="not-main",
                          reason="clone not on main — left for preflight to surface")
            return result

        # NOTE: use raw stdout, not `_out()` — `_out` strips the *whole* blob, which
        # eats the leading space of a first porcelain line like " M path" (status
        # codes that start with a space) and shifts every `line[3:]` slice by one.
        porcelain = _git(repo_dir, "status", "--porcelain").stdout
        paths = [line[3:].strip() for line in porcelain.splitlines() if line.strip()]
        if not paths:
            result.update(ok=True, disposition="clean", reason="nothing to commit")
            return result

        non_log = [p for p in paths
                   if not p.startswith("logs/") and os.path.basename(p) != ".DS_Store"]
        if non_log:
            result.update(disposition="halt-non-log-dirt",
                          reason=f"dirty tree includes non-log paths {non_log} — not "
                                 f"auto-committed; a real preflight halt is correct here")
            return result

        add = _git(repo_dir, "add", "logs/")
        if add.returncode != 0:
            result["reason"] = f"git add logs/ failed: {add.stderr.strip()}"
            return result
        commit = _git(repo_dir, "commit", "-m", "chore(logs): post-finalise tail")
        out = (commit.stdout + commit.stderr).lower()
        if commit.returncode != 0 and "nothing to commit" not in out:
            result["reason"] = f"commit failed: {commit.stderr.strip()}"
            return result

        push = _git(repo_dir, "push", "origin", "main")
        if push.returncode != 0:
            result.update(ok=True, disposition="committed-push-failed",
                          reason=f"committed locally; push failed (non-fatal): "
                                 f"{push.stderr.strip()}")
            return result
        result.update(ok=True, disposition="committed-and-pushed",
                      reason="post-finalise log tail committed + pushed")
        return result
    except Exception as exc:  # never break teardown
        result["reason"] = f"commit_log_tail error: {exc}"
        return result


# ---------------------------------------------------------------------------
# One-session-at-a-time lock
# ---------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except Exception:
        return False
    return True


def _lock_path(repo: str) -> Path:
    # Sanitise to a flat, filesystem-safe basename (repo may be a path or a name).
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(str(repo).rstrip("/")))
    return LOCK_DIR / f"{key}.lock"


# A lock older than this with no live owner pid is presumed orphaned (crashed launch
# that never finalised) and may be stolen — so a dead session can never wedge a clone
# indefinitely. Well above a normal session's length; finalise releases far sooner.
STALE_AFTER_SECONDS = 12 * 3600


class SessionLock:
    """One-session-at-a-time advisory lock for a clone, at
    ``~/.claude/session-locks/{repo}.lock``.

    **Presence-based, not pid-based** — because the lock is acquired at *launch* by an
    ephemeral process but must hold for the whole session and is released explicitly at
    finalise. So a present, recent lock refuses a second launch against the same clone.
    It is stolen only when clearly orphaned: a recorded *persistent-owner* pid that is
    dead (``pid`` passed by a session hook), or an age past :data:`STALE_AFTER_SECONDS`
    (crashed launch that never released). Same-``session_id`` re-acquire is idempotent
    (crash-recovery re-launch). Usable as a context manager or via
    :meth:`acquire`/:meth:`release`.

    Pass ``pid`` only from a *persistent* owner (e.g. a SessionStart hook recording the
    session process via ``os.getppid()``); the default ``0`` means "no liveness pid —
    rely on presence + age", correct for the ephemeral launcher that acquires and exits.
    """

    def __init__(self, repo: str, session_id: str = "", pid: int = 0):
        self.repo = str(repo)
        self.session_id = session_id or ""
        self.pid = int(pid or 0)
        self.path = _lock_path(repo)
        self.acquired = False

    def _read(self) -> Optional[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write(self) -> None:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "repo": self.repo, "session_id": self.session_id, "pid": self.pid,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, indent=2), encoding="utf-8")

    @staticmethod
    def _age_seconds(holder: dict) -> float:
        try:
            ts = datetime.strptime(holder.get("ts", ""), "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - ts).total_seconds()
        except Exception:
            return float("inf")  # unparseable ts → treat as orphaned (self-heal)

    def acquire(self) -> dict:
        """Return ``{"ok", "reason", "holder"}``. ``ok=False`` ⇒ a live session already
        holds this clone; refuse cleanly (do not launch)."""
        holder = self._read()
        if holder:
            if self.session_id and holder.get("session_id") == self.session_id:
                self._write()  # refresh ts for our own session
                self.acquired = True
                return {"ok": True, "reason": "reacquired-same-session", "holder": self._read()}
            hpid = int(holder.get("pid", 0) or 0)
            orphaned = ((hpid and not _pid_alive(hpid))
                        or self._age_seconds(holder) > STALE_AFTER_SECONDS)
            if not orphaned:
                return {"ok": False, "reason": "held-by-live-session", "holder": holder}
            # Orphaned (dead owner pid or aged out) → steal.
            self._write()
            self.acquired = True
            return {"ok": True, "reason": "stole-stale-lock", "holder": self._read()}
        self._write()
        self.acquired = True
        return {"ok": True, "reason": "acquired", "holder": self._read()}

    def release(self, *, owned_sids: Optional[list] = None) -> bool:
        """Remove the lock iff we own it, else leave it and return ``False``.

        Ownership matches when the recorded holder sid is empty, equals
        ``self.session_id``, or is any of ``owned_sids``. The extra ids close the
        **PROJ-039/T-054 lock-release-always-False** defect: in the brief-first flow
        the launcher takes the lock keyed on the **brief_id** (the runtime sid does
        not exist yet), but the finalise released with the runtime **session_id** — a
        permanent mismatch that always reported ``False`` and left every clone locked.
        The finalise now passes ``owned_sids=[brief_id]`` so its own brief-keyed lock
        matches, while a genuinely foreign lock is still left untouched."""
        holder = self._read()
        if holder is None:
            return True
        hsid = holder.get("session_id", "")
        owned = {self.session_id, *(owned_sids or [])}
        owned.discard("")
        if hsid and owned and hsid not in owned:
            return False  # someone else's lock — leave it
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            return False
        self.acquired = False
        return True

    def __enter__(self) -> "SessionLock":
        res = self.acquire()
        if not res["ok"]:
            raise RuntimeError(
                f"session lock held for {self.repo} by "
                f"{res['holder'].get('session_id', '?')} (pid {res['holder'].get('pid')})")
        return self

    def __exit__(self, *exc) -> None:
        self.release()


# ---------------------------------------------------------------------------
# CLI — lets the launch-session / consolidate skills call one line each
# ---------------------------------------------------------------------------

def _main(argv: Optional[list] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="PROJ-039/T-045 serial-mode session guards")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_pre = sub.add_parser("preflight", help="assert clean+on-main (ff), before branching")
    p_pre.add_argument("--repo", required=True)
    p_pre.add_argument("--no-recover", action="store_true",
                       help="do not auto-recover a finalised leftover branch")

    p_ret = sub.add_parser("return-to-main", help="session-end: ff main + delete session branch")
    p_ret.add_argument("--repo", required=True)
    p_ret.add_argument("--branch", default=None)

    p_lock = sub.add_parser("lock", help="acquire the one-session-at-a-time lock")
    p_lock.add_argument("--repo", required=True)
    p_lock.add_argument("--sid", default="")
    p_lock.add_argument("--pid", type=int, default=0,
                        help="persistent owner pid (e.g. a session hook's $PPID); "
                             "omit for the ephemeral launcher (presence+age lock)")

    p_unlock = sub.add_parser("unlock", help="release the lock")
    p_unlock.add_argument("--repo", required=True)
    p_unlock.add_argument("--sid", default="")

    args = ap.parse_args(argv)

    if args.cmd == "preflight":
        res = preflight(args.repo, auto_recover=not args.no_recover)
        print(json.dumps(res))
        print(res["message"], file=__import__("sys").stderr)
        return 0 if res["decision"] in ("ready", "recovered") else 3
    if args.cmd == "return-to-main":
        res = return_to_main(args.repo, args.branch)
        print(json.dumps(res))
        return 0 if res["ok"] else 1
    if args.cmd == "lock":
        res = SessionLock(args.repo, args.sid, args.pid).acquire()
        print(json.dumps(res))
        return 0 if res["ok"] else 4
    if args.cmd == "unlock":
        ok = SessionLock(args.repo, args.sid).release()
        print(json.dumps({"ok": ok}))
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
