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
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import lifecycle_mode, session_finalise  # noqa: E402


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
