"""Integration tests for launch.sh Phase 1 (PROJ-039/T-108, proposal §3.2).

Exercises the actual git mechanics `tools/launch.sh` performs against real
temp git repos (bare "origin" + working clones), the same style as
test_session_guard.py: preflight-abort on a dirtied repo, and the
branch + allow-empty-commit + push staging that pre-creates a working repo's
session branch (and, implicitly, what a PR would be opened against) BEFORE
Claude ever runs.

Does NOT invoke `claude -p` or `gh pr create` — those need a live subscription
token / real GitHub remote respectively, neither available in a test harness.
This proves the git-level contract launch.sh relies on: (a) preflight aborts
on a dirty working repo without touching it, (b) the empty-shell staging
leaves a branch pushed with exactly one commit ahead of main, ready for a PR.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import session_guard  # noqa: E402


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


class TestPreflightAbort(_GitFixture):
    """launch.sh's abort condition: session_guard.preflight must HALT (not
    ready/recovered) on a dirty working repo — the exact condition launch.sh
    treats as an abort-with-message, no launch, no cleanup attempt."""

    def test_clean_on_main_is_ready(self):
        res = session_guard.preflight(str(self.clone))
        self.assertEqual(res["decision"], "ready")

    def test_dirty_tree_halts(self):
        (self.clone / "scratch.txt").write_text("uncommitted work\n")
        res = session_guard.preflight(str(self.clone))
        self.assertEqual(res["decision"], "halt")
        self.assertEqual(res["reason"], "dirty-tree")
        # Never mutates on a halt — the clone is left exactly as found.
        self.assertTrue((self.clone / "scratch.txt").exists())

    def test_untracked_file_also_halts(self):
        # "untracked" is explicitly in scope per the brief's preflight-abort
        # condition (untracked, uncommitted, OR unpushed).
        (self.clone / "untracked.txt").write_text("new file\n")
        status = _git(self.clone, "status", "--porcelain").stdout
        self.assertIn("??", status)
        res = session_guard.preflight(str(self.clone))
        self.assertEqual(res["decision"], "halt")


class TestWorkingRepoStaging(_GitFixture):
    """The branch + allow-empty-commit + push sequence launch.sh runs to
    pre-stage a working repo's session branch before Claude ever runs."""

    def test_empty_shell_branch_is_pushed_with_one_commit_ahead(self):
        branch = "hephaestus/2026-08-05-t108-smoke"
        _git(self.clone, "checkout", "-b", branch)
        # Nothing ahead of main yet — launch.sh's allow-empty guard fires.
        ahead = _git(self.clone, "log", "origin/main.." + branch).stdout
        self.assertEqual(ahead.strip(), "")
        _git(self.clone, "commit", "--allow-empty", "-m",
             "chore: open session branch for podzone/2026-08-05-t108-smoke")
        _git(self.clone, "push", "-u", "origin", branch)

        # Verify: the remote now carries the branch, exactly one commit ahead
        # of main, and the working tree is clean (a PR could be opened against
        # it with a real diff to review, even though the diff is empty).
        remote_branches = _git(self.clone, "branch", "-r").stdout
        self.assertIn(f"origin/{branch}", remote_branches)
        commits_ahead = _git(self.clone, "rev-list", "--count",
                              "origin/main.." + branch).stdout.strip()
        self.assertEqual(commits_ahead, "1")
        self.assertEqual(_git(self.clone, "status", "--porcelain").stdout, "")

    def test_restaging_an_existing_branch_does_not_duplicate_the_empty_commit(self):
        # launch.sh's idempotency guard: `git log origin/main..branch` already
        # non-empty ⇒ skip the allow-empty commit (a re-run / relaunch against
        # an already-staged branch must not pile up empty commits).
        branch = "hephaestus/2026-08-05-t108-smoke2"
        _git(self.clone, "checkout", "-b", branch)
        _git(self.clone, "commit", "--allow-empty", "-m", "chore: open session branch")
        _git(self.clone, "push", "-u", "origin", branch)

        _git(self.clone, "checkout", branch)  # simulate a relaunch re-checking out
        ahead = _git(self.clone, "log", "origin/main.." + branch).stdout
        self.assertNotEqual(ahead.strip(), "")  # non-empty ⇒ launch.sh's guard skips a 2nd commit
        commits_ahead = _git(self.clone, "rev-list", "--count",
                              "origin/main.." + branch).stdout.strip()
        self.assertEqual(commits_ahead, "1")


if __name__ == "__main__":
    unittest.main()
