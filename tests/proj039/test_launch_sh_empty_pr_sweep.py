"""Tests for launch.sh's empty-shell PR auto-close sweep (PROJ-039/USS-261).

Extracts the actual `close_empty_shell_prs` function body from launch.sh (the
same style test_launch_sh_staging.py uses for the token-rotation cursor
block) and runs it under real bash against real temp git repos, with `gh`
stubbed via a fake PATH entry that logs its invocations to a file instead of
hitting the network. This proves the git-level decision logic — which
branches get closed and which are left alone — without needing a live GitHub
remote or `gh` auth.

Covers the brief's minimum bar (item 4): a repo whose branch is STILL only
the empty-shell placeholder commit gets closed; a repo with real commits on
top is never touched.
"""

from __future__ import annotations

import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _run(*args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True, env=env)


def _git(repo, *args):
    return _run("git", "-C", str(repo), *args)


def _extract_function(name):
    lines = (REPO_ROOT / "tools" / "launch.sh").read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == f"{name}() {{")
    depth = 0
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth == 0 and i > start:
            end = i + 1
            break
    return "\n".join(lines[start:end])


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

        # Fake `gh` on PATH: records every invocation to a log file so tests
        # can assert on close-vs-leave-alone without a real GitHub remote.
        self.gh_log = base / "gh.log"
        fake_bin = base / "bin"
        fake_bin.mkdir()
        gh_stub = fake_bin / "gh"
        gh_stub.write_text(f"""#!/usr/bin/env bash
echo "$@" >> "{self.gh_log}"
if [[ "$1 $2" == "pr list" ]]; then
  echo "1234"
fi
exit 0
""")
        gh_stub.chmod(gh_stub.stat().st_mode | stat.S_IEXEC)
        self.env = {**__import__("os").environ, "PATH": f"{fake_bin}:{__import__('os').environ['PATH']}"}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _sweep_script(self, branch_name, brief_id, repos):
        repos_csv = " ".join(f'"{r}"' for r in repos)
        return f"""
set -euo pipefail
BRANCH_NAME="{branch_name}"
BRIEF_ID="{brief_id}"
ORG="TestOrg"
BASE_BRANCH="main"
REPOS=({repos_csv})
WORKSPACE_DIR="{self.clone.parent}"
repo_dir_for() {{ echo "${{WORKSPACE_DIR}}/$1"; }}
log() {{ echo "[log] $*"; }}
{_extract_function('close_empty_shell_prs')}
close_empty_shell_prs
"""


class TestEmptyShellSweep(_GitFixture):
    def test_empty_shell_branch_gets_pr_closed_and_branch_deleted(self):
        branch = "hephaestus/2026-08-14-uss261-smoke"
        repo_dir = self.clone.parent / "myrepo"
        _run("git", "clone", str(self.origin), str(repo_dir))
        for k, v in (("user.email", "t@t"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            _git(repo_dir, "config", k, v)
        _git(repo_dir, "checkout", "-b", branch)
        _git(repo_dir, "commit", "--allow-empty", "-m",
             f"chore: open session branch for podzone/2026-08-14-uss261-smoke")
        _git(repo_dir, "push", "-u", "origin", branch)

        script = self._sweep_script(branch, "podzone/2026-08-14-uss261-smoke", ["myrepo"])
        _run("bash", "-c", script, env=self.env)

        log_text = self.gh_log.read_text()
        self.assertIn("pr close --repo TestOrg/myrepo 1234 --delete-branch", log_text)

        # The local clone must be returned to base and the local session
        # branch deleted — otherwise the NEXT launch.sh dispatch against this
        # repo trips session_guard's preflight on a branch nothing will ever
        # come back to finalise (the recurring friction this fix closes).
        current = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.assertEqual(current, "main")
        branches = _git(repo_dir, "branch", "--list", branch).stdout
        self.assertNotIn(branch, branches)

    def test_branch_with_real_commits_is_never_touched(self):
        branch = "hephaestus/2026-08-14-uss261-real-work"
        repo_dir = self.clone.parent / "realrepo"
        _run("git", "clone", str(self.origin), str(repo_dir))
        for k, v in (("user.email", "t@t"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            _git(repo_dir, "config", k, v)
        _git(repo_dir, "checkout", "-b", branch)
        _git(repo_dir, "commit", "--allow-empty", "-m",
             "chore: open session branch for podzone/2026-08-14-uss261-real-work")
        (repo_dir / "feature.txt").write_text("real work\n")
        _git(repo_dir, "add", "feature.txt")
        _git(repo_dir, "commit", "-m", "wip: podzone/2026-08-14-uss261-real-work attempt 1")
        _git(repo_dir, "push", "-u", "origin", branch)

        script = self._sweep_script(branch, "podzone/2026-08-14-uss261-real-work", ["realrepo"])
        _run("bash", "-c", script, env=self.env)

        self.assertFalse(self.gh_log.exists() and "pr close" in self.gh_log.read_text())
        # Real content must never be swept back to base — the branch (and its
        # commits) has to stay checked out for the Team Lead's normal review.
        current = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.assertEqual(current, branch)

    def test_local_ref_showing_empty_but_remote_has_real_commits_is_never_touched(self):
        # PROJ-039, 2026-09-02 — the ACP-465/PR#129 incident this test
        # guards against: a real 2-commit branch was swept as empty-shell
        # and its PR closed + branch deleted, because the OLD check trusted
        # the LOCAL BRANCH_NAME ref, which can diverge from what's actually
        # on the remote (root cause of the divergence itself was never
        # pinned down — this test proves the fix closes the failure mode
        # regardless of how the divergence happens). Real commit recovered
        # by SHA after the fact; nothing was actually lost, but the sweep
        # must never be able to destroy real remote content again.
        branch = "hephaestus/2026-09-02-uss261-divergence"
        repo_dir = self.clone.parent / "divergedrepo"
        _run("git", "clone", str(self.origin), str(repo_dir))
        for k, v in (("user.email", "t@t"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            _git(repo_dir, "config", k, v)
        _git(repo_dir, "checkout", "-b", branch)
        _git(repo_dir, "commit", "--allow-empty", "-m",
             "chore: open session branch for podzone/2026-09-02-uss261-divergence")
        (repo_dir / "feature.txt").write_text("real work\n")
        _git(repo_dir, "add", "feature.txt")
        _git(repo_dir, "commit", "-m", "wip: podzone/2026-09-02-uss261-divergence attempt 1")
        _git(repo_dir, "push", "-u", "origin", branch)
        # Simulate local/remote divergence: reset the LOCAL branch back to
        # just the placeholder, as if whatever caused the ACP-465 incident
        # happened here too. The remote still has both commits.
        _git(repo_dir, "reset", "--hard", "HEAD~1")

        script = self._sweep_script(branch, "podzone/2026-09-02-uss261-divergence", ["divergedrepo"])
        _run("bash", "-c", script, env=self.env)

        self.assertFalse(self.gh_log.exists() and "pr close" in self.gh_log.read_text())
        # The remote branch must still exist with both commits — not deleted.
        remote_log = _run("git", "ls-remote", str(self.origin), branch).stdout
        self.assertIn(branch, remote_log)

    def test_branch_with_no_commits_ahead_is_never_touched(self):
        # Not yet staged / already fully merged — nothing ahead of main at
        # all is not the empty-shell condition and must be left alone.
        branch = "hephaestus/2026-08-14-uss261-untouched"
        repo_dir = self.clone.parent / "untouchedrepo"
        _run("git", "clone", str(self.origin), str(repo_dir))
        _git(repo_dir, "checkout", "-b", branch)
        _git(repo_dir, "push", "-u", "origin", branch)

        script = self._sweep_script(branch, "podzone/2026-08-14-uss261-untouched", ["untouchedrepo"])
        _run("bash", "-c", script, env=self.env)

        self.assertFalse(self.gh_log.exists() and "pr close" in self.gh_log.read_text())


class TestSweepWiredIntoOtherFailurePath(unittest.TestCase):
    """Gap found live 2026-09-02 (ACP-474 dispatch): close_empty_shell_prs
    previously ran only on the `complete` exit. A session that made zero
    progress still leaves the placeholder branch/PR behind, and the next
    launch against the same brief_id (same deterministic BRANCH_NAME) hits
    session_guard's "unfinalised session branch" halt, requiring manual
    cleanup every time. Structural guard: the `other|*)` exit branch must
    call close_empty_shell_prs too, same as `complete` already does."""

    def test_other_failure_branch_calls_sweep(self):
        src = (REPO_ROOT / "tools" / "launch.sh").read_text()
        other_idx = src.index('    other|*)')
        next_case_idx = src.index("esac", other_idx)
        other_block = src[other_idx:next_case_idx]
        self.assertIn("close_empty_shell_prs", other_block)

    def test_complete_branch_still_calls_sweep(self):
        # Regression guard for the pre-existing behaviour this change must
        # not disturb.
        src = (REPO_ROOT / "tools" / "launch.sh").read_text()
        complete_idx = src.index("    complete)")
        other_idx = src.index("    other|*)")
        complete_block = src[complete_idx:other_idx]
        self.assertIn("close_empty_shell_prs", complete_block)


if __name__ == "__main__":
    unittest.main()
