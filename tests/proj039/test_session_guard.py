"""Tests for lib/session_guard.py — serial simple-repo mode guards (PROJ-039/T-045).

Exercises the three load-bearing serial-safety primitives against **real** temp git
repos (a bare "origin" + a working clone), so the preflight / return-to-main / lock
behaviour is proven end-to-end, not mocked:

  * preflight: clean-on-main → ready; dirty → halt; leftover session branch → halt
    unless the finalise ledger shows the clone's last session finalised → recovered.
  * return_to_main: ff main + delete pushed session branch; keep an unpushed branch.
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
        # The ledger's own in-progress content (this session's step records) must
        # survive on disk — T-030's recovery guard reads it from the working tree —
        # even though the path is STILL TRACKED here (the legacy/unmigrated shape:
        # a tracked file that diverged genuinely re-dirties main once the preserved
        # bytes are written back; that residual is exactly what the real fix
        # (logs/*.log gitignored, PROJ-039/T-068) removes entirely — proven by the
        # sibling gitignored-path test below, which DOES assert a clean tree).
        self.assertIn("sid-this-session", (self.clone / rel).read_text())

    def test_diverged_IGNORED_log_leaves_a_fully_clean_tree(self) -> None:
        """The T-068 fix proper: once ``logs/*.log`` is gitignored (this session's
        ledger/live-log writes are working state, never tracked), the exact same
        divergence shape above cannot even arise — an ignored file carries no
        per-branch git history to diverge, so ``return_to_main`` both succeeds AND
        leaves a fully clean tree, with the in-progress content untouched on disk."""
        rel = "logs/finalise-state.log"
        (self.clone / ".gitignore").write_text("logs/*.log\n")
        _git(self.clone, "add", ".gitignore")
        _git(self.clone, "commit", "-m", "gitignore logs")
        _git(self.clone, "push", "origin", "main")

        branch = "session/hephaestus-2026-07-06-t068b"
        self._branch(branch)
        self._commit()

        (self.clone / "logs").mkdir(exist_ok=True)
        (self.clone / rel).write_text('{"sid-this-session": {"step1": "done"}}\n')

        res = session_guard.return_to_main(str(self.clone))

        self.assertTrue(res["ok"], res)
        self.assertEqual(session_guard.current_branch(self.clone), "main")
        self.assertFalse(session_guard.working_tree_dirty(self.clone))
        self.assertIn("sid-this-session", (self.clone / rel).read_text())

    def _seed_tracked(self, rel: str, body: str) -> Path:
        p = self.clone / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        _git(self.clone, "add", rel)
        _git(self.clone, "commit", "-m", "seed tracked log")
        _git(self.clone, "push", "origin", "main")
        return p


class TestStageSessionLogs(_GitFixture):
    """PROJ-039/T-068 — completed session logs still ride the result PR, via
    explicit force-add (``git add -f``, since ``logs/*.log`` is gitignored again)
    rather than blanket tracking (``commit_log_tail``, T-063, now retired: with
    the ledger + live logs gitignored there is no more post-return-to-main
    tracked-log dirt for a last-act sweep to clean up)."""

    SID = "abcd1234ef567890"
    SID8 = SID[:8]

    def _gitignore_logs(self) -> None:
        (self.clone / ".gitignore").write_text("logs/*.log\n")
        _git(self.clone, "add", ".gitignore")
        _git(self.clone, "commit", "-m", "gitignore logs")

    def test_stages_matching_sid_logs_in_place(self) -> None:
        self._gitignore_logs()
        (self.clone / "logs").mkdir(exist_ok=True)
        (self.clone / "logs" / f"libraries-{self.SID8}.log").write_text("line one\n")
        (self.clone / "logs" / f"primitives-{self.SID8}.log").write_text("prim line\n")
        (self.clone / "logs" / "libraries-other0000.log").write_text("not this session\n")

        staged = session_guard.stage_session_logs(str(self.clone), self.SID)

        self.assertEqual(sorted(staged),
                         [f"logs/libraries-{self.SID8}.log", f"logs/primitives-{self.SID8}.log"])
        cached = _git(self.clone, "diff", "--cached", "--name-only").stdout
        self.assertIn(f"logs/libraries-{self.SID8}.log", cached)
        self.assertIn(f"logs/primitives-{self.SID8}.log", cached)
        self.assertNotIn("other0000", cached)

    def test_copies_into_a_different_dest_dir(self) -> None:
        """The migrated-home shape: commit_home_result authors in an isolated
        worktree, so the session's live logs must be copied across first."""
        self._gitignore_logs()
        (self.clone / "logs").mkdir(exist_ok=True)
        (self.clone / "logs" / f"libraries-{self.SID8}.log").write_text("worktree-bound\n")

        dest = Path(tempfile.mkdtemp())
        _git(dest, "init", "-q")
        for k, v in (("user.email", "t@t"), ("user.name", "T"), ("commit.gpgsign", "false")):
            _git(dest, "config", k, v)
        (dest / "seed.txt").write_text("x\n")
        _git(dest, "add", "seed.txt")
        _git(dest, "commit", "-m", "seed")

        staged = session_guard.stage_session_logs(str(self.clone), self.SID, dest_dir=str(dest))

        self.assertEqual(staged, [f"logs/libraries-{self.SID8}.log"])
        self.assertEqual((dest / "logs" / f"libraries-{self.SID8}.log").read_text(),
                         "worktree-bound\n")
        cached = _git(dest, "diff", "--cached", "--name-only").stdout
        self.assertIn(f"logs/libraries-{self.SID8}.log", cached)

    def test_no_matching_logs_is_not_an_error(self) -> None:
        self.assertEqual(session_guard.stage_session_logs(str(self.clone), self.SID), [])

    def test_no_session_id_is_not_an_error(self) -> None:
        self.assertEqual(session_guard.stage_session_logs(str(self.clone), ""), [])


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


class TestBareScriptInvocation(_GitFixture):
    """T-075 F9 (plan D-7) — the documented CLI form must work BARE:
    ``python3 …/lib/session_guard.py <cmd>`` from any cwd with an empty
    PYTHONPATH. Pre-fix it crashed at import (sys.path[0] = lib/, so the
    ``lib`` package is invisible and the bare fallback dies one level deeper
    on finalise_ledger's relative import) — hit live at the T-069 launch and
    PYTHONPATH-worked-around at every launch since."""

    SCRIPT = REPO_ROOT / "lib" / "session_guard.py"

    def _bare_run(self, *args: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONPATH"] = ""
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), *args],
            cwd=self._tmp.name,  # a foreign cwd — not the agent-tooling repo
            env=env, capture_output=True, text=True,
        )

    def test_bare_preflight_runs_from_foreign_cwd(self) -> None:
        cp = self._bare_run("preflight", "--repo", str(self.clone))
        self.assertNotIn("ModuleNotFoundError", cp.stderr, cp.stderr)
        self.assertNotIn("ImportError", cp.stderr, cp.stderr)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(json.loads(cp.stdout.splitlines()[0])["decision"], "ready")

    def test_bare_return_to_main_runs_from_foreign_cwd(self) -> None:
        cp = self._bare_run("return-to-main", "--repo", str(self.clone))
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(json.loads(cp.stdout.splitlines()[0])["disposition"],
                         "already-main")


class TestOwnedSidSet(unittest.TestCase):
    """T-075 F13 — the sid forms a launch may key locks under. Observed on disk:
    the runtime UUID (pinned-sid launch), the bare brief_id (t071 home lock) AND
    a ``brief:``-prefixed brief_id (the t075 launch's own locks). Matching must
    accept all three or a release silently mismatches (the T-054 class)."""

    def test_expands_bare_brief_id_to_prefixed(self) -> None:
        got = session_guard.owned_sid_set("runtime-uuid", "podzone/2026-07-08-t075")
        self.assertEqual(got, {"runtime-uuid", "podzone/2026-07-08-t075",
                               "brief:podzone/2026-07-08-t075"})

    def test_strips_prefix_when_brief_id_already_carries_it(self) -> None:
        got = session_guard.owned_sid_set("", "brief:podzone/2026-07-08-t075")
        self.assertEqual(got, {"podzone/2026-07-08-t075",
                               "brief:podzone/2026-07-08-t075"})

    def test_empty_inputs_yield_empty_set(self) -> None:
        self.assertEqual(session_guard.owned_sid_set("", ""), set())


class _TwoCloneFixture(_GitFixture):
    """The multi-clone launch shape (T-075 F13): the home clone from _GitFixture
    plus a second origin+clone standing in for a task repo (the stranded
    ``~/workspace/agent-tooling`` of t069/t070/t071), with LOCK_DIR redirected."""

    def setUp(self) -> None:
        super().setUp()
        base = Path(self._tmp.name)
        self.task_origin = base / "task-origin.git"
        self.task_clone = base / "task-clone"
        _run("git", "init", "--bare", "-b", "main", str(self.task_origin))
        _run("git", "clone", str(self.task_origin), str(self.task_clone))
        for k, v in (("user.email", "t@t"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            _git(self.task_clone, "config", k, v)
        (self.task_clone / "README.md").write_text("task repo\n")
        _git(self.task_clone, "add", "README.md")
        _git(self.task_clone, "commit", "-m", "init")
        _git(self.task_clone, "push", "origin", "main")

        self._orig_lock_dir = session_guard.LOCK_DIR
        session_guard.LOCK_DIR = base / "locks"

    def tearDown(self) -> None:
        session_guard.LOCK_DIR = self._orig_lock_dir
        super().tearDown()

    def _task_branch_commit_push(self, branch: str, *, push: bool = True) -> None:
        _git(self.task_clone, "checkout", "-b", branch)
        (self.task_clone / "w.txt").write_text("task work\n")
        _git(self.task_clone, "add", "w.txt")
        _git(self.task_clone, "commit", "-m", "task work")
        if push:
            _git(self.task_clone, "push", "origin", branch)


class TestLockedRepos(_TwoCloneFixture):
    def test_enumerates_only_owned_locks(self) -> None:
        sid = "brief:podzone/2026-07-08-t075"
        session_guard.SessionLock(str(self.clone), sid).acquire()
        session_guard.SessionLock(str(self.task_clone), sid).acquire()
        session_guard.SessionLock("/w/foreign-repo", "someone-else").acquire()

        owned = session_guard.owned_sid_set("runtime", "podzone/2026-07-08-t075")
        repos = {h["repo"] for h in session_guard.locked_repos(owned)}
        self.assertEqual(repos, {str(self.clone), str(self.task_clone)})

    def test_unparseable_lock_is_skipped(self) -> None:
        session_guard.LOCK_DIR.mkdir(parents=True, exist_ok=True)
        (session_guard.LOCK_DIR / "junk.lock").write_text("not json")
        self.assertEqual(session_guard.locked_repos({"sid"}), [])

    def test_no_lock_dir_is_empty(self) -> None:
        self.assertEqual(session_guard.locked_repos({"sid"}), [])


class TestEndGuardAllClones(_TwoCloneFixture):
    """T-075 F13 — the all-clones end-guard. The three-manual-sweep failure: a
    clean finalise returned the HOME repo only; every additional task-repo clone
    stayed on its session branch with its lock held. The guard must end-guard
    the whole launch-recorded lock set, home first."""

    SID = "4aa90fe7-0000-0000-0000-000000000000"
    BRIEF = "podzone/2026-07-08-t075-lifecycle-hardening"

    def _lock_both(self, lock_sid: str) -> None:
        self.assertTrue(session_guard.SessionLock(str(self.clone), lock_sid)
                        .acquire()["ok"])
        self.assertTrue(session_guard.SessionLock(str(self.task_clone), lock_sid)
                        .acquire()["ok"])

    def test_multi_clone_lifecycle_returns_all_and_unlocks(self) -> None:
        """The full t071 shape: brief-keyed locks (prefixed form, as observed on
        disk for the t075 launch), pushed session branches on both clones, live
        gitignored ledger writes in the home repo mid-finalise (T-068 shape).
        A clean end-guard must leave BOTH clones on a ff'd main, session
        branches deleted, and ZERO locks for this session — no Hermes sweep."""
        self._lock_both(f"brief:{self.BRIEF}")

        # Home repo: gitignored ledger dirt written during the finalise (T-068).
        (self.clone / ".gitignore").write_text("logs/*.log\n")
        _git(self.clone, "add", ".gitignore")
        _git(self.clone, "commit", "-m", "gitignore logs")
        _git(self.clone, "push", "origin", "main")
        home_branch = "session/hephaestus-2026-07-08-t075"
        self._branch(home_branch)
        self._commit()
        _git(self.clone, "push", "origin", home_branch)
        (self.clone / "logs").mkdir(exist_ok=True)
        (self.clone / "logs" / "finalise-state.log").write_text(
            '{"sid": {"steps": {"response": "done"}}}\n')

        # Task repo: its own pushed session branch (the stranded clone).
        task_branch = "hephaestus/2026-07-08-t075-lifecycle-hardening"
        self._task_branch_commit_push(task_branch)

        results = session_guard.end_guard_all_clones(
            str(self.clone), session_id=self.SID, brief_id=self.BRIEF)

        self.assertEqual([e["kind"] for e in results], ["home", "task"],
                         "home repo must be end-guarded first")
        for entry in results:
            self.assertTrue(entry["return"]["ok"], entry)
            self.assertEqual(entry["return"]["disposition"],
                             "returned-branch-deleted", entry)
            self.assertTrue(entry["lock_released"], entry)
        self.assertEqual(session_guard.current_branch(self.clone), "main")
        self.assertEqual(session_guard.current_branch(self.task_clone), "main")
        # Zero .lock files remaining for this session — the acceptance line.
        self.assertEqual(list(session_guard.LOCK_DIR.glob("*.lock")), [])
        # The ledger's live content survives on disk (T-030 reads the tree).
        self.assertIn("response",
                      (self.clone / "logs" / "finalise-state.log").read_text())

    def test_bare_brief_keyed_locks_also_match(self) -> None:
        """The t071 home lock was keyed on the BARE brief_id — both conventions
        must release."""
        self._lock_both(self.BRIEF)
        results = session_guard.end_guard_all_clones(
            str(self.clone), session_id=self.SID, brief_id=self.BRIEF)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(e["lock_released"] for e in results))
        self.assertEqual(list(session_guard.LOCK_DIR.glob("*.lock")), [])

    def test_unpushed_task_branch_is_kept_and_surfaced(self) -> None:
        """Per-clone safety semantics preserved: an unpushed task branch is
        never deleted — ``returned-branch-kept-unpushed`` surfaces instead."""
        self._lock_both(f"brief:{self.BRIEF}")
        task_branch = "hephaestus/2026-07-08-t075-unpushed"
        self._task_branch_commit_push(task_branch, push=False)

        results = session_guard.end_guard_all_clones(
            str(self.clone), session_id=self.SID, brief_id=self.BRIEF)

        task = [e for e in results if e["kind"] == "task"][0]
        self.assertTrue(task["return"]["ok"], task)
        self.assertEqual(task["return"]["disposition"],
                         "returned-branch-kept-unpushed")
        self.assertEqual(session_guard.current_branch(self.task_clone), "main")
        branches = _git(self.task_clone, "branch", "--list", task_branch).stdout
        self.assertIn(task_branch, branches, "unpushed branch must survive")

    def test_foreign_lock_and_clone_left_untouched(self) -> None:
        """A lock held by an UNRELATED session must not have its clone returned
        or its lock removed — owned_sids is not a skeleton key."""
        session_guard.SessionLock(str(self.clone), f"brief:{self.BRIEF}").acquire()
        session_guard.SessionLock(str(self.task_clone), "someone-elses-sid").acquire()
        foreign_branch = "athena/2026-07-08-someone-elses-work"
        self._task_branch_commit_push(foreign_branch)

        results = session_guard.end_guard_all_clones(
            str(self.clone), session_id=self.SID, brief_id=self.BRIEF)

        self.assertEqual([e["kind"] for e in results], ["home"])
        self.assertEqual(session_guard.current_branch(self.task_clone),
                         foreign_branch, "foreign clone must not be returned")
        remaining = list(session_guard.LOCK_DIR.glob("*.lock"))
        self.assertEqual(len(remaining), 1)
        self.assertIn("someone-elses-sid", remaining[0].read_text())

    def test_task_clone_already_on_main_is_noop(self) -> None:
        self._lock_both(f"brief:{self.BRIEF}")
        results = session_guard.end_guard_all_clones(
            str(self.clone), session_id=self.SID, brief_id=self.BRIEF)
        task = [e for e in results if e["kind"] == "task"][0]
        self.assertTrue(task["return"]["ok"])
        self.assertEqual(task["return"]["disposition"], "already-main")
        self.assertTrue(task["lock_released"])

    def test_no_home_repo_still_sweeps_task_locks(self) -> None:
        """A finalise with no resolvable home repo (edge) must still end-guard
        the lock set rather than strand it."""
        session_guard.SessionLock(str(self.task_clone), f"brief:{self.BRIEF}").acquire()
        results = session_guard.end_guard_all_clones(
            "", session_id=self.SID, brief_id=self.BRIEF)
        self.assertEqual([e["kind"] for e in results], ["task"])
        self.assertEqual(list(session_guard.LOCK_DIR.glob("*.lock")), [])


class TestAgentSessionBranch(unittest.TestCase):
    """T-003 — the agent-scoped matcher must be a strict subset of
    is_session_branch: only THIS agent's two branch forms, nobody else's."""

    def test_matches_both_agent_forms(self) -> None:
        self.assertTrue(session_guard.is_agent_session_branch(
            "hephaestus/2026-07-17-proj043-t002-fusion-sweep", "hephaestus"))
        self.assertTrue(session_guard.is_agent_session_branch(
            "session/hephaestus-2026-07-17-t003", "hephaestus"))

    def test_agent_case_insensitive(self) -> None:
        self.assertTrue(session_guard.is_agent_session_branch(
            "hephaestus/2026-07-17-x", "Hephaestus"))

    def test_foreign_agent_never_matches(self) -> None:
        self.assertFalse(session_guard.is_agent_session_branch(
            "athena/2026-07-17-x", "hephaestus"))
        self.assertFalse(session_guard.is_agent_session_branch(
            "session/athena-2026-07-17-x", "hephaestus"))

    def test_prefix_agent_does_not_leak(self) -> None:
        # "hermes2" must not match agent "hermes" via a loose prefix.
        self.assertFalse(session_guard.is_agent_session_branch(
            "hermes2/2026-07-17-x", "hermes"))
        self.assertFalse(session_guard.is_agent_session_branch(
            "session/hermes2-2026-07-17-x", "hermes"))

    def test_non_session_shapes_rejected(self) -> None:
        for name in ("main", "hephaestus/no-date-here", "feature/foo", ""):
            self.assertFalse(
                session_guard.is_agent_session_branch(name, "hephaestus"), name)

    def test_empty_agent_matches_nothing(self) -> None:
        self.assertFalse(session_guard.is_agent_session_branch(
            "hephaestus/2026-07-17-x", ""))


class TestStrandedAgentClones(_TwoCloneFixture):
    """T-003 — the workspace-roots scan: depth-1 repos and {team}/{repo}
    grandchildren, agent-scoped, never descending into a repo or a hidden dir."""

    def setUp(self) -> None:
        super().setUp()
        self.ws = Path(self._tmp.name) / "workspace"
        self.ws.mkdir()

    def _clone_into(self, parent: Path, name: str, branch: str = "") -> Path:
        dest = parent / name
        _run("git", "clone", str(self.task_origin), str(dest))
        for k, v in (("user.email", "t@t"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            _git(dest, "config", k, v)
        if branch:
            _git(dest, "checkout", "-b", branch)
        return dest

    def test_finds_depth1_and_team_nested_clones(self) -> None:
        self._clone_into(self.ws, "agent-tooling",
                         "hephaestus/2026-07-17-proj043-t002-fusion-sweep")
        team = self.ws / "podzoneTeam-repos"
        team.mkdir()
        self._clone_into(team, "nested", "session/hephaestus-2026-07-17-t003")
        found = session_guard.stranded_agent_clones(
            "hephaestus", workspace_root=self.ws)
        self.assertEqual(
            sorted(os.path.basename(p) for p in found),
            ["agent-tooling", "nested"])

    def test_skips_main_foreign_and_hidden(self) -> None:
        self._clone_into(self.ws, "on-main")  # stays on main
        self._clone_into(self.ws, "foreign", "athena/2026-07-17-her-work")
        hidden = self.ws / ".hidden"
        hidden.mkdir()
        self._clone_into(hidden, "in-hidden", "hephaestus/2026-07-17-x")
        (self.ws / "loose-file.txt").write_text("not a dir\n")
        self.assertEqual(session_guard.stranded_agent_clones(
            "hephaestus", workspace_root=self.ws), [])

    def test_never_descends_into_a_repo(self) -> None:
        # A stood-up subrepo INSIDE a depth-1 repo (the .workspace/ shape) must
        # be invisible — that repo's own lifecycle owns it (T-054 boundary).
        outer = self._clone_into(self.ws, "home-like")  # on main → not matched
        inner_parent = outer / ".workspace"
        inner_parent.mkdir()
        self._clone_into(inner_parent, "stood-up", "hephaestus/2026-07-17-inner")
        self.assertEqual(session_guard.stranded_agent_clones(
            "hephaestus", workspace_root=self.ws), [])

    def test_empty_agent_or_missing_root_is_empty(self) -> None:
        self.assertEqual(session_guard.stranded_agent_clones(
            "", workspace_root=self.ws), [])
        self.assertEqual(session_guard.stranded_agent_clones(
            "hephaestus", workspace_root=self.ws / "nope"), [])


class TestResolveScanAgent(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home-podzone-hephaestus"
        self.home.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_identity_yaml_wins(self) -> None:
        ident = self.home / "workspaces" / "identity"
        ident.mkdir(parents=True)
        (ident / "hephaestus.identity.yaml").write_text(
            "agent: Hephaestus\nrole_class: coder\nscope: podzone\n")
        self.assertEqual(session_guard.resolve_scan_agent(str(self.home)),
                         "hephaestus")

    def test_basename_fallback_without_yaml(self) -> None:
        self.assertEqual(session_guard.resolve_scan_agent(str(self.home)),
                         "hephaestus")

    def test_unresolvable_returns_empty(self) -> None:
        other = Path(self._tmp.name) / "just-a-clone"
        other.mkdir()
        self.assertEqual(session_guard.resolve_scan_agent(str(other)), "")
        self.assertEqual(session_guard.resolve_scan_agent(""), "")


class TestEndGuardScanPhase(_TwoCloneFixture):
    """T-003 — the t002/e29fed8b regression shape at the guard level: the task
    clone was branched MID-SESSION (no lock), so the F13 lock set cannot see
    it; the agent-scoped scan must return it. Foreign locks and foreign
    branches stay untouched; scan hits deduplicate against the lock set."""

    SID = "e29fed8b-0000-0000-0000-000000000000"
    BRIEF = "podzone/2026-07-17-proj043-t002-fusion-sweep"

    def setUp(self) -> None:
        super().setUp()
        # The scan is pointed at the fixture base, where task_clone lives.
        self._orig_ws_root = session_guard.WORKSPACE_ROOT
        session_guard.WORKSPACE_ROOT = Path(self._tmp.name)

    def tearDown(self) -> None:
        session_guard.WORKSPACE_ROOT = self._orig_ws_root
        super().tearDown()

    def test_unlocked_mid_session_branch_is_returned_by_scan(self) -> None:
        """The live t002 failure: home locked+branched, task clone branched by
        a bare `git checkout -b` with NO lock. Both must end on main with the
        pushed branches gone."""
        session_guard.SessionLock(str(self.clone), f"brief:{self.BRIEF}").acquire()
        home_branch = "session/hephaestus-2026-07-17-t002"
        self._branch(home_branch)
        self._commit()
        _git(self.clone, "push", "origin", home_branch)
        task_branch = "hephaestus/2026-07-17-proj043-t002-fusion-sweep"
        self._task_branch_commit_push(task_branch)

        results = session_guard.end_guard_all_clones(
            str(self.clone), session_id=self.SID, brief_id=self.BRIEF,
            agent="hephaestus")

        self.assertEqual([(e["kind"], e["via"]) for e in results],
                         [("home", "home"), ("task", "scan")])
        for entry in results:
            self.assertTrue(entry["return"]["ok"], entry)
            self.assertEqual(entry["return"]["disposition"],
                             "returned-branch-deleted", entry)
        self.assertEqual(session_guard.current_branch(self.clone), "main")
        self.assertEqual(session_guard.current_branch(self.task_clone), "main")
        branches = _git(self.task_clone, "branch",
                        "--format=%(refname:short)").stdout.split()
        self.assertNotIn(task_branch, branches)
        self.assertEqual(list(session_guard.LOCK_DIR.glob("*.lock")), [])

    def test_scan_dedupes_against_lock_set(self) -> None:
        """A clone that is BOTH locked and on the agent's branch shows up once,
        via the lock set — the scan never double-processes."""
        session_guard.SessionLock(str(self.clone), f"brief:{self.BRIEF}").acquire()
        session_guard.SessionLock(str(self.task_clone), f"brief:{self.BRIEF}").acquire()
        self._task_branch_commit_push("hephaestus/2026-07-17-locked-too")

        results = session_guard.end_guard_all_clones(
            str(self.clone), session_id=self.SID, brief_id=self.BRIEF,
            agent="hephaestus")

        task_entries = [e for e in results if e["kind"] == "task"]
        self.assertEqual([e["via"] for e in task_entries], ["lock"])
        self.assertEqual(session_guard.current_branch(self.task_clone), "main")

    def test_scanned_clone_with_foreign_lock_is_skipped(self) -> None:
        """Agent-named branch but a FOREIGN live lock (another session owns the
        clone right now): skip outright, touch nothing."""
        branch = "hephaestus/2026-07-17-someone-holds-lock"
        self._task_branch_commit_push(branch)
        session_guard.SessionLock(str(self.task_clone), "foreign-live-sid").acquire()

        results = session_guard.end_guard_all_clones(
            str(self.clone), session_id=self.SID, brief_id=self.BRIEF,
            agent="hephaestus")

        task = [e for e in results if e["kind"] == "task"][0]
        self.assertEqual(task["via"], "scan")
        self.assertEqual(task["return"]["disposition"], "skipped-foreign-lock")
        self.assertFalse(task["lock_released"])
        self.assertEqual(session_guard.current_branch(self.task_clone), branch)
        remaining = list(session_guard.LOCK_DIR.glob("*.lock"))
        self.assertEqual(len(remaining), 1)
        self.assertIn("foreign-live-sid", remaining[0].read_text())

    def test_scanned_unpushed_branch_is_kept_and_surfaced(self) -> None:
        """Never discard commits: an unpushed mid-session branch is returned to
        main but the branch survives, surfaced as kept-unpushed."""
        branch = "hephaestus/2026-07-17-unpushed-work"
        self._task_branch_commit_push(branch, push=False)

        results = session_guard.end_guard_all_clones(
            str(self.clone), session_id=self.SID, brief_id=self.BRIEF,
            agent="hephaestus")

        task = [e for e in results if e["kind"] == "task"][0]
        self.assertEqual(task["return"]["disposition"],
                         "returned-branch-kept-unpushed")
        self.assertEqual(session_guard.current_branch(self.task_clone), "main")
        branches = _git(self.task_clone, "branch", "--list", branch).stdout
        self.assertIn(branch, branches, "unpushed branch must survive")

    def test_unresolvable_agent_disables_scan(self) -> None:
        """No explicit agent + a home repo with no identity signal ⇒ the scan
        contributes nothing (never guesses an agent)."""
        self._task_branch_commit_push("hephaestus/2026-07-17-invisible")
        results = session_guard.end_guard_all_clones(
            str(self.clone), session_id=self.SID, brief_id=self.BRIEF)
        self.assertEqual([e["kind"] for e in results], ["home"])
        self.assertEqual(session_guard.current_branch(self.task_clone),
                         "hephaestus/2026-07-17-invisible")


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
