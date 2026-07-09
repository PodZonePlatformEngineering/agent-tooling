"""Tests for the trunk-mode home-repo finalise path (PROJ-039/T-084).

Exercises `lib.session_finalise.commit_and_push_trunk` against a **real** temp
git repo (a bare "origin" + a working clone) — no mocking of git — so both
required acceptance paths are OBSERVED, not just asserted:

  * happy path: commit lands on the live working tree and pushes straight to
    `origin/main`, no branch, no PR.
  * conflict-retry-then-halt path: a concurrent writer lands a conflicting
    commit on `origin/main` first; the rebase fails, is retried once, still
    fails, and the function HALTS — the commit stays local, nothing is
    force-pushed, and `origin/main` is left exactly as the concurrent writer
    landed it.

Also covers `lib.lifecycle_mode.read_lifecycle_mode` (manifest absent/present/
corrupt) and the idempotent re-run no-op (result already on `origin/main`).

`TestCommitTrunkLogTail` and `TestTrunkFinaliseNoPostCommitDirt` (PROJ-039/T-091)
cover the post-commit log-tail regression: in trunk mode `commit_and_push_trunk`
force-adds + commits + pushes THIS session's sid-keyed logs straight into the LIVE
working tree (no isolated worktree to shield it, unlike branch-mode). Every
finalise step that runs afterwards (session-end main-guard, the ledger
`complete()` write, the final "finalise complete" log line) calls
`runtime_log.log_library`, which appends to that now-tracked file — leaving the
clone dirty on `main` for the next `session_guard.preflight` to HALT on (first
hit: sid `7aaf93d5`, T-086 dogfood). `TestTrunkFinaliseNoPostCommitDirt` drives
the REAL `session-end-finalise.py` hook end-to-end against a real git repo and
asserts a clean working tree afterwards — it fails on the pre-fix ordering
(dirty tree) and passes once `commit_trunk_log_tail` runs as the finalise's
literal last act.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import (  # noqa: E402
    lifecycle_mode, session_finalise, session_guard, session_substrate,
    telemetry_repo, cst_cleanup, brief_substrate,
)

HOOK_PATH = REPO_ROOT / "hooks" / "session-end-finalise.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("session_end_finalise", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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

    def _second_clone(self) -> Path:
        """A second, independent clone of the same origin — simulates a
        concurrent writer landing a commit on `origin/main` out of band."""
        other = Path(self._tmp.name) / "clone2"
        _run("git", "clone", str(self.origin), str(other))
        for k, v in (("user.email", "o@o"), ("user.name", "O"),
                     ("commit.gpgsign", "false")):
            _git(other, "config", k, v)
        return other


class TestCommitAndPushTrunkHappyPath(_GitFixture):
    def test_happy_path_lands_directly_on_main_no_branch(self) -> None:
        res = session_finalise.commit_and_push_trunk(
            "# Session Result\n\nsome content\n",
            session_id="abcd1234efgh",
            work_item="PROJ-039/T-084",
            date="2026-07-09",
            repo_dir=str(self.clone),
        )
        self.assertEqual(res["disposition"], "done")
        self.assertTrue(res["ok"])
        self.assertEqual(
            session_finalise._git(self.clone, "branch", "--show-current").stdout.strip(),
            "main",
        )
        result_path = self.clone / res["file_path"]
        self.assertTrue(result_path.is_file())

        # Actually landed on origin/main — a fresh clone sees it, no PR/branch involved.
        fresh = Path(self._tmp.name) / "fresh"
        _run("git", "clone", str(self.origin), str(fresh))
        self.assertTrue((fresh / res["file_path"]).is_file())
        # Only `main` exists remotely — no session/result branch was ever pushed.
        branches = _git(self.origin, "branch").stdout
        self.assertNotIn("session", branches)
        self.assertNotIn("result", branches)

    def test_idempotent_rerun_is_noop_once_on_origin_main(self) -> None:
        first = session_finalise.commit_and_push_trunk(
            "content\n", session_id="deadbeef0000", work_item="PROJ-039/T-084",
            date="2026-07-09", repo_dir=str(self.clone),
        )
        self.assertEqual(first["disposition"], "done")

        second = session_finalise.commit_and_push_trunk(
            "content\n", session_id="deadbeef0000", work_item="PROJ-039/T-084",
            date="2026-07-09", repo_dir=str(self.clone),
        )
        self.assertEqual(second["disposition"], "exists")
        self.assertTrue(second["ok"])


class TestCommitAndPushTrunkConflictRetryHalt(_GitFixture):
    def test_conflicting_concurrent_write_retries_then_halts_without_force_push(self) -> None:
        sid = "cafefeed0001"
        sid8 = sid[:8]
        work_item = "PROJ-039/T-084"
        date = "2026-07-09"
        log_rel = f"logs/libraries-{sid8}.log"

        # This session's own sid-keyed log already exists locally — stage_session_logs
        # (T-068) force-adds it into the result commit, same as a real finalise.
        (self.clone / "logs").mkdir(parents=True, exist_ok=True)
        (self.clone / log_rel).write_text("this session's own log line\n")

        # Land a CONFLICTING commit on origin/main at the SAME log path, from an
        # independent clone, before this session's commit tries to push — the
        # "an operator or another process touched the repo out of band" case the
        # plan calls out (§2). The result file itself is sid-keyed-unique so it
        # can never collide this way in practice; the shared path a real race can
        # still land on is a log file force-added alongside it.
        other = self._second_clone()
        (other / "logs").mkdir(parents=True, exist_ok=True)
        (other / log_rel).write_text("a DIFFERENT concurrent writer's log line\n")
        _git(other, "add", "-f", log_rel)
        _git(other, "commit", "-m", "concurrent write")
        _git(other, "push", "origin", "main")
        origin_tip_before = _git(self.origin, "rev-parse", "main").stdout.strip()

        res = session_finalise.commit_and_push_trunk(
            "this session's own content\n",
            session_id=sid, work_item=work_item, date=date,
            repo_dir=str(self.clone),
        )

        self.assertEqual(res["disposition"], "halted")
        self.assertFalse(res["ok"])
        self.assertTrue(res["reason"])

        # origin/main is untouched — no force-push, the concurrent writer's
        # commit is exactly as it landed.
        origin_tip_after = _git(self.origin, "rev-parse", "main").stdout.strip()
        self.assertEqual(origin_tip_before, origin_tip_after)

        # The commit stayed LOCAL: HEAD carries our commit, not on origin/main.
        local_log = _git(self.clone, "log", "--oneline", "-1").stdout
        self.assertIn("session-result", local_log)
        # No half-finished rebase left behind — the abort left a clean, usable tree.
        status = _git(self.clone, "status", "--porcelain").stdout
        self.assertEqual(status.strip(), "")
        rebase_dir_git = _git(self.clone, "rev-parse", "--git-dir").stdout.strip()
        self.assertFalse(
            (Path(self.clone) / rebase_dir_git / "rebase-merge").exists()
            or (Path(self.clone) / rebase_dir_git / "rebase-apply").exists()
        )


class TestCommitTrunkLogTail(_GitFixture):
    """Unit coverage of `session_finalise.commit_trunk_log_tail` (T-091)."""

    def test_noop_when_clean(self) -> None:
        res = session_finalise.commit_trunk_log_tail(str(self.clone))
        self.assertTrue(res["ok"])
        self.assertEqual(res["disposition"], "clean")

    def test_commits_and_pushes_log_only_dirt(self) -> None:
        # Simulate a trunk result commit that already force-added a sid-keyed log,
        # then a POST-commit `_log()` line landing after the push (the T-091 class).
        sid8 = "7aaf93d5"
        log_rel = f"logs/libraries-{sid8}.log"
        (self.clone / "logs").mkdir(parents=True, exist_ok=True)
        (self.clone / log_rel).write_text("line written before the result commit\n")
        _git(self.clone, "add", "-f", log_rel)
        _git(self.clone, "commit", "-m", "chore(session-result): pretend result")
        _git(self.clone, "push", "origin", "main")

        with open(self.clone / log_rel, "a", encoding="utf-8") as fh:
            fh.write("finalise complete\n")  # the post-commit tail line
        self.assertNotEqual(
            _git(self.clone, "status", "--porcelain").stdout.strip(), "",
            "fixture sanity: the post-commit append must actually dirty the tree",
        )

        res = session_finalise.commit_trunk_log_tail(str(self.clone))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["disposition"], "committed-and-pushed")
        self.assertEqual(_git(self.clone, "status", "--porcelain").stdout.strip(), "")

        fresh = Path(self._tmp.name) / "fresh-tail"
        _run("git", "clone", str(self.origin), str(fresh))
        self.assertIn("finalise complete", (fresh / log_rel).read_text())

    def test_refuses_on_non_log_dirt(self) -> None:
        (self.clone / "results").mkdir(parents=True, exist_ok=True)
        (self.clone / "results" / "real-work.md").write_text("uncommitted work\n")

        res = session_finalise.commit_trunk_log_tail(str(self.clone))
        self.assertFalse(res["ok"])
        self.assertEqual(res["disposition"], "halt-non-log-dirt")
        # Left exactly as found — real work is never auto-committed.
        status = _git(self.clone, "status", "--porcelain").stdout
        self.assertIn("results/", status)


SESSION_POINT = {
    "session_id": "7aaf93d5-1234-4f78-aba6-29dcf3c4e55a",
    "agent": "Hephaestus", "work_item": "PROJ-039/T-091",
    "brief_id": "", "brief": {"text": "trunk log tail fix"},
    "response": {"text": "done", "status_transition": "in_progress->complete"},
    "rollup": {},
}


class TestTrunkFinaliseNoPostCommitDirt(unittest.TestCase):
    """End-to-end regression (T-091): drive the REAL `session-end-finalise.py`
    hook against a real trunk-mode home repo and assert it exits with a clean
    working tree. FAILS on the pre-fix ordering — the hook's own post-commit
    `_log()` calls (main-guard, ledger `complete()`, the final log line) re-dirty
    the sid-keyed log file that `commit_and_push_trunk` just force-added, commit-
    ted, and pushed — and PASSES once `commit_trunk_log_tail` runs as the trunk
    finalise's literal last act.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.origin = base / "home.git"
        self.home = base / "home-podzone-hephaestus"
        _run("git", "init", "--bare", "-b", "main", str(self.origin))
        _run("git", "clone", str(self.origin), str(self.home))
        for k, v in (("user.email", "t@t"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            _git(self.home, "config", k, v)
        claude_dir = self.home / ".claude"
        claude_dir.mkdir()
        (claude_dir / "tooling-manifest.json").write_text(
            json.dumps({"version": "1.8.1", "lifecycle_mode": "trunk"}))
        (self.home / ".gitignore").write_text("logs/\n")
        _git(self.home, "add", ".claude", ".gitignore")
        _git(self.home, "commit", "-m", "init")
        _git(self.home, "push", "origin", "main")

        self._orig_lockdir = session_guard.LOCK_DIR
        session_guard.LOCK_DIR = base / "locks"
        self.sid = SESSION_POINT["session_id"]
        acq = session_guard.SessionLock(str(self.home), self.sid).acquire()
        assert acq["ok"], acq

        self._patches = []
        self._patch(session_substrate, "upsert_response", lambda *a, **k: None)
        self._patch(session_substrate, "get_session_point", lambda sid: SESSION_POINT)
        self._patch(session_substrate, "compute_rollup",
                    lambda tp: {"tool_usage": {}, "cost_tokens": {}})
        self._patch(session_substrate, "attach_rollup", lambda *a, **k: None)
        self._patch(brief_substrate, "complete_brief", lambda *a, **k: None)
        self._patch(telemetry_repo, "resolve_remote", lambda *a, **k: None)
        self._patch(telemetry_repo, "ensure_repo",
                    lambda *a, **k: {"repo_dir": "", "initialised": False})
        self._patch(telemetry_repo, "commit_and_push",
                    lambda *a, **k: {"committed": False, "pushed": False, "reason": "test"})
        self._patch(cst_cleanup, "delete_raw_tool_events",
                    lambda *a, **k: {"deleted_before_count": 0})

        self._orig_env = dict(os.environ)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.home)
        os.environ["PODZONE_LOG_DIR"] = str(self.home / "logs")
        os.environ.pop("PODZONETEAM_REPO", None)
        os.environ.pop("TRAINEE_RUNTIME", None)
        os.environ.pop("PODZONE_INGEST_TRANSCRIPT", None)

        self._orig_stdin = sys.stdin
        self.hook = _load_hook()

    def _patch(self, mod, name, fn) -> None:
        orig = getattr(mod, name)
        self._patches.append((mod, name, orig))
        setattr(mod, name, fn)

    def tearDown(self) -> None:
        for mod, name, orig in reversed(self._patches):
            setattr(mod, name, orig)
        sys.stdin = self._orig_stdin
        session_guard.LOCK_DIR = self._orig_lockdir
        os.environ.clear()
        os.environ.update(self._orig_env)
        self._tmp.cleanup()

    def test_hook_exits_with_clean_tree_on_trunk_repo(self) -> None:
        sys.stdin = io.StringIO(json.dumps({
            "session_id": self.sid, "transcript_path": "", "cwd": str(self.home),
        }))
        self.assertEqual(self.hook.main(), 0)

        status = _git(self.home, "status", "--porcelain").stdout
        self.assertEqual(status.strip(), "",
                          f"trunk finalise left the clone dirty: {status!r}")

        # The result really landed on origin/main (no branch, no PR).
        fresh = Path(self._tmp.name) / "fresh-check"
        _run("git", "clone", str(self.origin), str(fresh))
        results = list((fresh / "results").glob("session-*.md")) if \
            (fresh / "results").is_dir() else []
        self.assertTrue(results, "trunk result commit did not land on origin/main")


class TestLifecycleMode(unittest.TestCase):
    def test_default_branch_when_manifest_absent(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(lifecycle_mode.read_lifecycle_mode(d), "branch")
            self.assertFalse(lifecycle_mode.is_trunk_mode(d))

    def test_trunk_when_manifest_flags_it(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            claude_dir = Path(d) / ".claude"
            claude_dir.mkdir()
            (claude_dir / "tooling-manifest.json").write_text(
                json.dumps({"version": "1.0.0", "lifecycle_mode": "trunk"}))
            self.assertEqual(lifecycle_mode.read_lifecycle_mode(d), "trunk")
            self.assertTrue(lifecycle_mode.is_trunk_mode(d))

    def test_branch_when_manifest_present_but_unflagged(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            claude_dir = Path(d) / ".claude"
            claude_dir.mkdir()
            (claude_dir / "tooling-manifest.json").write_text(
                json.dumps({"version": "1.0.0"}))
            self.assertEqual(lifecycle_mode.read_lifecycle_mode(d), "branch")

    def test_branch_when_manifest_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            claude_dir = Path(d) / ".claude"
            claude_dir.mkdir()
            (claude_dir / "tooling-manifest.json").write_text("{ not json")
            self.assertEqual(lifecycle_mode.read_lifecycle_mode(d), "branch")


if __name__ == "__main__":
    unittest.main()
