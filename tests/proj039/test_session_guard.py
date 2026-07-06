"""Tests for lib/session_guard.py — serial simple-repo mode guards (PROJ-039/T-045).

Exercises the three load-bearing serial-safety primitives against **real** temp git
repos (a bare "origin" + a working clone), so the preflight / return-to-main / lock
behaviour is proven end-to-end, not mocked:

  * preflight: clean-on-main → ready; dirty → halt; leftover session branch → halt
    unless the finalise ledger shows the clone's last session finalised → recovered.
  * return_to_main: ff main + delete pushed session branch; keep an unpushed branch;
    skip a linked worktree (legacy path).
  * SessionLock: refuse a live holder, steal a stale lock, idempotent same-sid.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import finalise_ledger, session_guard  # noqa: E402


def _run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


def _git(repo, *args):
    return _run("git", "-C", str(repo), *args)


class _GitFixture(unittest.TestCase):
    """A bare origin + a clone on main with one commit, in a temp dir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.origin = base / "origin.git"
        self.clone = base / "clone"
        _run("git", "init", "--bare", "-b", "main", str(self.origin))
        _run("git", "clone", str(self.origin), str(self.clone))
        for k, v in (("user.email", "t@t"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            _git(self.clone, "config", k, v)
        (self.clone / "README.md").write_text("hi\n")
        _git(self.clone, "add", "README.md")
        _git(self.clone, "commit", "-m", "init")
        _git(self.clone, "push", "origin", "main")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _branch(self, name: str) -> None:
        _git(self.clone, "checkout", "-b", name)

    def _commit(self, fname="w.txt", body="work\n") -> None:
        (self.clone / fname).write_text(body)
        _git(self.clone, "add", fname)
        _git(self.clone, "commit", "-m", f"add {fname}")


class TestBranchHelpers(_GitFixture):
    def test_is_session_branch(self) -> None:
        self.assertTrue(session_guard.is_session_branch("session/hephaestus-2026-07-03-t045"))
        self.assertTrue(session_guard.is_session_branch("hephaestus/2026-07-03-t045"))
        self.assertFalse(session_guard.is_session_branch("main"))
        self.assertFalse(session_guard.is_session_branch("feature/foo"))
        self.assertFalse(session_guard.is_session_branch(""))

    def test_current_branch_and_dirty(self) -> None:
        self.assertEqual(session_guard.current_branch(self.clone), "main")
        self.assertFalse(session_guard.working_tree_dirty(self.clone))
        (self.clone / "README.md").write_text("changed\n")
        self.assertTrue(session_guard.working_tree_dirty(self.clone))

    def test_ds_store_does_not_count_as_dirty(self) -> None:
        (self.clone / ".DS_Store").write_text("junk")
        self.assertFalse(session_guard.working_tree_dirty(self.clone))

    def test_primary_clone_is_not_linked_worktree(self) -> None:
        self.assertFalse(session_guard.is_linked_worktree(self.clone))

    def test_linked_worktree_detected(self) -> None:
        wt = Path(self._tmp.name) / "wt"
        _git(self.clone, "worktree", "add", str(wt), "-b", "session/x-2026-07-03-t")
        self.assertTrue(session_guard.is_linked_worktree(wt))


class TestPreflight(_GitFixture):
    def test_clean_on_main_is_ready(self) -> None:
        res = session_guard.preflight(str(self.clone))
        self.assertEqual(res["decision"], "ready")
        self.assertEqual(session_guard.current_branch(self.clone), "main")

    def test_dirty_tree_halts(self) -> None:
        (self.clone / "README.md").write_text("dirty\n")
        res = session_guard.preflight(str(self.clone))
        self.assertEqual(res["decision"], "halt")
        self.assertEqual(res["reason"], "dirty-tree")
        # HALT must not mutate — still dirty, still on main.
        self.assertTrue(session_guard.working_tree_dirty(self.clone))

    def test_leftover_session_branch_without_finalise_halts(self) -> None:
        self._branch("session/hephaestus-2026-07-03-t045")
        self._commit()
        res = session_guard.preflight(str(self.clone))
        self.assertEqual(res["decision"], "halt")
        self.assertEqual(res["reason"], "unfinalised-session-branch")
        # Untouched: still on the session branch.
        self.assertEqual(session_guard.current_branch(self.clone),
                         "session/hephaestus-2026-07-03-t045")

    def test_leftover_branch_with_finalised_ledger_recovers(self) -> None:
        branch = "session/hephaestus-2026-07-03-t045"
        self._branch(branch)
        self._commit()
        _git(self.clone, "push", "origin", branch)  # pushed → safe to delete
        # Ledger: this clone's last session finalised (complete).
        with tempfile.TemporaryDirectory() as logdir:
            os.environ["PODZONE_LOG_DIR"] = logdir
            try:
                finalise_ledger.begin("sid-xyz", cwd=str(self.clone))
                finalise_ledger.complete("sid-xyz")
                res = session_guard.preflight(str(self.clone))
            finally:
                os.environ.pop("PODZONE_LOG_DIR", None)
        self.assertEqual(res["decision"], "recovered")
        self.assertEqual(session_guard.current_branch(self.clone), "main")
        branches = _git(self.clone, "branch").stdout
        self.assertNotIn(branch, branches)  # stale branch deleted

    def test_detached_head_halts(self) -> None:
        head = _git(self.clone, "rev-parse", "HEAD").stdout.strip()
        _git(self.clone, "checkout", head)
        res = session_guard.preflight(str(self.clone))
        self.assertEqual(res["decision"], "halt")
        self.assertEqual(res["reason"], "detached-head")


class TestReturnToMain(_GitFixture):
    def test_pushed_session_branch_returned_and_deleted(self) -> None:
        branch = "session/hephaestus-2026-07-03-t045"
        self._branch(branch)
        self._commit()
        _git(self.clone, "push", "origin", branch)
        res = session_guard.return_to_main(str(self.clone))
        self.assertTrue(res["ok"])
        self.assertEqual(res["disposition"], "returned-branch-deleted")
        self.assertEqual(session_guard.current_branch(self.clone), "main")
        self.assertNotIn(branch, _git(self.clone, "branch").stdout)

    def test_unpushed_session_branch_kept(self) -> None:
        branch = "hephaestus/2026-07-03-t045"
        self._branch(branch)
        self._commit()  # NOT pushed
        res = session_guard.return_to_main(str(self.clone))
        self.assertTrue(res["ok"])
        self.assertEqual(res["disposition"], "returned-branch-kept-unpushed")
        self.assertEqual(session_guard.current_branch(self.clone), "main")
        self.assertIn(branch, _git(self.clone, "branch").stdout)  # kept — work not lost

    def test_already_on_main_is_noop(self) -> None:
        res = session_guard.return_to_main(str(self.clone))
        self.assertTrue(res["ok"])
        self.assertEqual(res["disposition"], "already-main")

    def test_linked_worktree_skipped(self) -> None:
        wt = Path(self._tmp.name) / "wt"
        _git(self.clone, "worktree", "add", str(wt), "-b", "session/x-2026-07-03-t")
        res = session_guard.return_to_main(str(wt))
        self.assertTrue(res["ok"])
        self.assertEqual(res["disposition"], "skipped-worktree")
        # The worktree branch is untouched (legacy path reaps it elsewhere).
        self.assertEqual(session_guard.current_branch(wt), "session/x-2026-07-03-t")


class TestReturnToMainLedgerLifecycle(_GitFixture):
    """PROJ-039/T-068 — the REAL lifecycle regression the T-062/T-063 attempts missed.

    T-063 (v1.2.1)'s own suite (``TestCommitLogTail`` below) only ever seeded a
    tracked log file, wrote ONE tail line to it, and called ``commit_log_tail``
    directly — it never called :func:`return_to_main` at all, so it never
    exercised the actual failure: a *globally-shared* tracked log file (the
    finalise ledger, or the unkeyed ``libraries.log``/``primitives.log``) is
    written to by EVERY session, so by the time session N's ``return_to_main``
    runs, ``origin/main`` usually already carries a *different* commit of that
    same file (from session N-1's own tail commit, or a concurrent clone) than
    the one session N's branch forked from. Session N's own mid-finalise writes
    (ledger step-records, per-step log lines) are then uncommitted local
    modifications *on top of* that already-diverged file — exactly the shape
    ``git merge --ff-only`` refuses on with "local changes would be overwritten"
    (reproduced by hand against real git in the T-068 investigation; this is
    what actually happened to sid ``ca95a57b``, see home ``3ad6e62``).

    This fixture reproduces that shape directly against real git repos:
      1. clone forks a session branch off main (holding a tracked ledger file);
      2. a SEPARATE clone (a "prior/concurrent session") commits + pushes a
         DIFFERENT version of that same tracked path to origin/main;
      3. back on the session branch, the ledger is written to again
         (uncommitted) — simulating live ``finalise_ledger.record_step`` calls;
      4. :func:`return_to_main` is asked to return the clone to a ff'd main.

    Against v1.2.1 (tracked ``logs/*.log``, no return_to_main tolerance) this
    HALTS: ``ok=False``, disposition ``noop`` (branch still the session branch,
    ``ff_main`` failed on the diverged+dirty file) — the live ``ca95a57b``
    failure, reproduced. The T-068 fix (ledger + live logs gitignored again,
    plus a belt-and-braces stash-carry for any residual tracked log dirt) must
    make this ``ok=True`` and land the clone on a clean, ff'd main — the
    ledger's own content (this session's step records) preserved on disk for
    the T-030 recovery guard to still read from the working tree.
    """

    def _diverge_tracked_path_on_origin(self, rel: str, body: str) -> None:
        """A separate clone commits+pushes a DIFFERENT version of ``rel`` to
        origin/main — standing in for a prior session's own tail commit (or a
        concurrent clone), so this clone's ``origin/main`` fetch will disagree
        with both this clone's branch-point AND its current dirty content."""
        other = Path(tempfile.mkdtemp()) / "other-clone"
        _run("git", "clone", str(self.origin), str(other))
        for k, v in (("user.email", "t@t"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            _git(other, "config", k, v)
        p = other / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        _git(other, "add", rel)
        _git(other, "commit", "-m", f"other session writes {rel}")
        _git(other, "push", "origin", "main")

    def test_diverged_tracked_ledger_halts_return_to_main_pre_fix_shape(self) -> None:
        """The bug, reproduced: a tracked, globally-shared log file that has
        diverged on origin/main (a prior session's tail commit) PLUS this
        session's own live mid-finalise writes on top of a stale local branch
        deterministically breaks the ``git merge --ff-only`` in ``ff_main`` —
        regardless of whether ``logs/*.log`` is committed at all in THIS repo;
        this fixture always seeds it tracked (the v1.2.1 shape) to prove the
        failure mode exists at the git level. The T-068 fix must special-case
        this in :func:`return_to_main` (residual PURE-log dirt tolerance) since
        the underlying git conflict is unavoidable for any tracked file that
        several sequential/concurrent sessions all append to."""
        rel = "logs/finalise-state.log"
        self._seed_tracked(rel, '{"base": 1}\n')

        branch = "session/hephaestus-2026-07-06-t068"
        self._branch(branch)
        self._commit()  # the session's own real work, unrelated to the ledger

        # A prior/concurrent session's tail commit diverges origin/main's copy.
        self._diverge_tracked_path_on_origin(
            rel, '{"base": 1, "other-session": "complete"}\n')

        # This session's own live finalise: ledger step-writes, uncommitted.
        (self.clone / rel).write_text(
            '{"base": 1, "sid-this-session": {"step1": "done"}}\n')

        res = session_guard.return_to_main(str(self.clone))

        self.assertTrue(
            res["ok"],
            f"return_to_main must tolerate a diverged tracked log file, not halt: {res}")
        self.assertEqual(session_guard.current_branch(self.clone), "main")
        self.assertFalse(session_guard.working_tree_dirty(self.clone))
        # The ledger's own in-progress content (this session's step records) must
        # survive on disk — T-030's recovery guard reads it from the working tree.
        self.assertIn("sid-this-session", (self.clone / rel).read_text())

    def _seed_tracked(self, rel: str, body: str) -> Path:
        p = self.clone / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        _git(self.clone, "add", rel)
        _git(self.clone, "commit", "-m", "seed tracked log")
        _git(self.clone, "push", "origin", "main")
        return p


class TestCommitLogTail(_GitFixture):
    """PROJ-039/T-063 — the finalise's own tail commit (the log-tail-dirt fix)."""

    def _seed_tracked_log(self, rel="logs/finalise-state.log", body="{}\n") -> Path:
        p = self.clone / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        _git(self.clone, "add", rel)
        _git(self.clone, "commit", "-m", "seed tracked log")
        _git(self.clone, "push", "origin", "main")
        return p

    def test_pure_log_dirt_is_committed_and_pushed(self) -> None:
        log = self._seed_tracked_log()
        log.write_text('{"sid": {"complete": true}}\n')  # post-finalise tail write
        res = session_guard.commit_log_tail(str(self.clone))
        self.assertTrue(res["ok"])
        self.assertEqual(res["disposition"], "committed-and-pushed")
        self.assertFalse(session_guard.working_tree_dirty(self.clone))
        # Pushed — origin/main carries the tail commit too.
        origin_log = _git(self.clone, "show", "origin/main:logs/finalise-state.log").stdout
        self.assertIn("complete", origin_log)

    def test_new_untracked_sid_log_is_committed(self) -> None:
        # A brand-new sid-keyed log file (untracked "?? " porcelain line), the shape
        # T-048/T-062 actually produce for a fresh session.
        (self.clone / "logs").mkdir(exist_ok=True)
        (self.clone / "logs" / "libraries-abcd1234.log").write_text("line one\n")
        res = session_guard.commit_log_tail(str(self.clone))
        self.assertEqual(res["disposition"], "committed-and-pushed")
        self.assertFalse(session_guard.working_tree_dirty(self.clone))

    def test_clean_tree_is_a_noop(self) -> None:
        res = session_guard.commit_log_tail(str(self.clone))
        self.assertTrue(res["ok"])
        self.assertEqual(res["disposition"], "clean")

    def test_non_log_dirt_is_not_swept_into_the_commit(self) -> None:
        self._seed_tracked_log()
        (self.clone / "README.md").write_text("real uncommitted work\n")
        res = session_guard.commit_log_tail(str(self.clone))
        self.assertFalse(res["ok"])
        self.assertEqual(res["disposition"], "halt-non-log-dirt")
        # Untouched — still dirty, nothing committed.
        self.assertTrue(session_guard.working_tree_dirty(self.clone))

    def test_mixed_log_and_non_log_dirt_is_not_swept(self) -> None:
        log = self._seed_tracked_log()
        log.write_text("tail line\n")
        (self.clone / "app.py").write_text("real work\n")
        res = session_guard.commit_log_tail(str(self.clone))
        self.assertEqual(res["disposition"], "halt-non-log-dirt")
        self.assertTrue(session_guard.working_tree_dirty(self.clone))

    def test_not_on_main_is_a_noop(self) -> None:
        self._branch("session/hephaestus-2026-07-03-t045")
        p = self.clone / "logs" / "finalise-state.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"dirty": true}\n')
        res = session_guard.commit_log_tail(str(self.clone))
        self.assertEqual(res["disposition"], "not-main")

    def test_linked_worktree_skipped(self) -> None:
        wt = Path(self._tmp.name) / "wt"
        _git(self.clone, "worktree", "add", str(wt), "-b", "session/x-2026-07-03-t")
        (wt / "logs").mkdir(exist_ok=True)
        (wt / "logs" / "finalise-state.log").write_text("{}\n")
        res = session_guard.commit_log_tail(str(wt))
        self.assertEqual(res["disposition"], "skipped-worktree")

    def test_back_to_back_sessions_launch_cleanly(self) -> None:
        """The regression test named in the brief: two sessions in a row, with the
        tail commit as the only thing between them — no manual log commit, no halt."""
        # Session 1: does some work, its finalise tail writes to a tracked log.
        log = self._seed_tracked_log()
        log.write_text('{"sid-1": {"complete": true}}\n')
        tail1 = session_guard.commit_log_tail(str(self.clone))
        self.assertEqual(tail1["disposition"], "committed-and-pushed")

        # Session 2 launches: preflight must see a clean, ff'd main — never a halt.
        pre = session_guard.preflight(str(self.clone))
        self.assertEqual(pre["decision"], "ready")

        # Session 2 does its own work + finalise tail.
        log.write_text('{"sid-1": {"complete": true}, "sid-2": {"complete": true}}\n')
        tail2 = session_guard.commit_log_tail(str(self.clone))
        self.assertEqual(tail2["disposition"], "committed-and-pushed")

        # A third launch is clean too.
        pre2 = session_guard.preflight(str(self.clone))
        self.assertEqual(pre2["decision"], "ready")


class TestSessionLock(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = session_guard.LOCK_DIR
        session_guard.LOCK_DIR = Path(self._tmp.name) / "locks"

    def tearDown(self) -> None:
        session_guard.LOCK_DIR = self._orig_dir
        self._tmp.cleanup()

    def test_acquire_then_release(self) -> None:
        lock = session_guard.SessionLock("home-podzone-hephaestus", "sid-a")
        self.assertTrue(lock.acquire()["ok"])
        self.assertTrue(lock.path.exists())
        self.assertTrue(lock.release())
        self.assertFalse(lock.path.exists())

    def test_recent_lock_refuses_other_session(self) -> None:
        # A present, recent lock (no live owner pid) refuses a second launch — the
        # ephemeral-launcher case: presence, not pid, is the mutual-exclusion signal.
        a = session_guard.SessionLock("repo", "sid-a")
        self.assertTrue(a.acquire()["ok"])
        res = session_guard.SessionLock("repo", "sid-b").acquire()
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "held-by-live-session")

    def test_same_sid_reacquire_is_idempotent(self) -> None:
        a = session_guard.SessionLock("repo", "sid-a")
        self.assertTrue(a.acquire()["ok"])
        again = session_guard.SessionLock("repo", "sid-a").acquire()
        self.assertTrue(again["ok"])
        self.assertEqual(again["reason"], "reacquired-same-session")

    def test_dead_persistent_owner_is_stolen(self) -> None:
        # A recorded persistent-owner pid (a session hook's pid) that is dead ⇒ orphaned.
        lock = session_guard.SessionLock("repo", "sid-old")
        session_guard.LOCK_DIR.mkdir(parents=True, exist_ok=True)
        lock.path.write_text(json.dumps(
            {"repo": "repo", "session_id": "sid-old", "pid": 2 ** 31 - 1,
             "ts": session_guard.datetime.now(
                 session_guard.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}))
        res = session_guard.SessionLock("repo", "sid-new").acquire()
        self.assertTrue(res["ok"])
        self.assertEqual(res["reason"], "stole-stale-lock")

    def test_aged_out_lock_is_stolen(self) -> None:
        # No live pid, but older than the TTL ⇒ orphaned crashed launch → steal.
        lock = session_guard.SessionLock("repo", "sid-old")
        session_guard.LOCK_DIR.mkdir(parents=True, exist_ok=True)
        lock.path.write_text(json.dumps(
            {"repo": "repo", "session_id": "sid-old", "pid": 0,
             "ts": "2000-01-01T00:00:00Z"}))
        res = session_guard.SessionLock("repo", "sid-new").acquire()
        self.assertTrue(res["ok"])
        self.assertEqual(res["reason"], "stole-stale-lock")

    def test_context_manager_raises_on_conflict(self) -> None:
        held = session_guard.SessionLock("repo", "sid-a")
        held.acquire()
        with self.assertRaises(RuntimeError):
            with session_guard.SessionLock("repo", "sid-b"):
                pass

    # -- T-054: lock-release-always-False regression -------------------------

    def test_release_matches_brief_keyed_lock_via_owned_sids(self) -> None:
        """The launcher takes the lock keyed on the **brief_id** (brief-first flow —
        the runtime sid does not exist yet). The finalise, holding only the runtime
        sid, used to release with that sid and ALWAYS report False (permanent
        mismatch → every clone left locked). Passing ``owned_sids=[brief_id]`` now
        matches the brief-keyed holder and releases."""
        brief_id = "podzone/2026-07-05-finalise-hardening"
        launcher = session_guard.SessionLock("repo", brief_id)
        self.assertTrue(launcher.acquire()["ok"])
        # Old behaviour: release with only the runtime sid refuses (the regression).
        runtime = session_guard.SessionLock("repo", "runtime-uuid")
        self.assertFalse(runtime.release())
        self.assertTrue(runtime.path.exists(), "lock wrongly removed on a mismatch")
        # T-054 fix: name the brief_id as an owned sid → releases True.
        self.assertTrue(runtime.release(owned_sids=[brief_id]))
        self.assertFalse(runtime.path.exists())

    def test_release_still_refuses_a_genuinely_foreign_lock(self) -> None:
        """owned_sids must not become a skeleton key — a lock held by an unrelated
        sid (not in owned) is still left in place."""
        other = session_guard.SessionLock("repo", "someone-else")
        self.assertTrue(other.acquire()["ok"])
        mine = session_guard.SessionLock("repo", "runtime-uuid")
        self.assertFalse(mine.release(owned_sids=["my-brief"]))
        self.assertTrue(other.path.exists())


class TestResolveHomeRepo(unittest.TestCase):
    """T-054 — the finalise must bind to the agent's own home repo, never the bare
    wandered cwd (the operator-clone hijack: shell ended in `.workspace/academy-admin`)."""

    def test_prefers_claude_project_dir(self) -> None:
        env = {"CLAUDE_PROJECT_DIR": "/w/home-podzone-athena"}
        # cwd wandered into a stood-up subrepo — must be ignored in favour of the anchor.
        got = session_guard.resolve_home_repo(
            "/w/home-podzone-athena/.workspace/academy-admin", env=env)
        self.assertEqual(got, "/w/home-podzone-athena")

    def test_strips_workspace_tail_from_cwd_when_anchor_unset(self) -> None:
        got = session_guard.resolve_home_repo(
            "/w/home-podzone-athena/.workspace/academy-admin", env={})
        self.assertEqual(got, "/w/home-podzone-athena")

    def test_strips_workspace_tail_from_anchor_too(self) -> None:
        env = {"CLAUDE_PROJECT_DIR": "/w/home-podzone-athena/.workspace/sub"}
        self.assertEqual(session_guard.resolve_home_repo("", env=env),
                         "/w/home-podzone-athena")

    def test_bare_cwd_when_no_workspace_and_no_anchor(self) -> None:
        self.assertEqual(
            session_guard.resolve_home_repo("/w/home-podzone-athena/", env={}),
            "/w/home-podzone-athena")

    def test_never_returns_a_workspace_subrepo(self) -> None:
        for cwd in ("/w/home-x/.workspace/academy-admin",
                    "/w/home-x/.workspace",
                    "/w/home-x/.workspace/a/b/c"):
            self.assertNotIn("/.workspace",
                             session_guard.resolve_home_repo(cwd, env={}))

    def test_strip_is_idempotent_for_a_clean_path(self) -> None:
        self.assertEqual(
            session_guard.strip_workspace_subrepo("/w/home-podzone-athena"),
            "/w/home-podzone-athena")


if __name__ == "__main__":
    unittest.main()
