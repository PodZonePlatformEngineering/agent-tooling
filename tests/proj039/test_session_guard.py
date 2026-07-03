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


if __name__ == "__main__":
    unittest.main()
