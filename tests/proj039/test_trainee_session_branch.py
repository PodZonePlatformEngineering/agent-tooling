"""R-2 (PROJ-011/T-021, CC-351) — session branch on SessionStart (hook-driven).

Exercises :func:`ensure_session_branch` against real throwaway git repos: the
name grammar, the clean-on-main happy path (create), idempotent resume (no-op), and
the guard HALT on a dirty tree (no branch, clone left as found).
"""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_hook():
    spec = importlib.util.spec_from_file_location(
        "trainee_session_branch", str(REPO_ROOT / "hooks" / "trainee-session-branch.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod


HOOK = _load_hook()
SID = "22ca589f-82ce-410b-b766-4a726b1a710c"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


def _make_repo(tmp: Path) -> Path:
    """A clone-with-remote on a clean main: a bare origin + a working clone, so
    ``session_guard`` preflight's ``fetch``/``ff-only`` have a real remote."""
    origin = tmp / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True,
                   capture_output=True)
    repo = tmp / "repo"
    subprocess.run(["git", "clone", str(origin), str(repo)], check=True,
                   capture_output=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    _git(repo, "push", "-u", "origin", "main")
    return repo


class TestSessionBranchName(unittest.TestCase):
    def test_name_grammar(self) -> None:
        name = HOOK.session_branch_name(SID, date="2026-07-04")
        self.assertEqual(name, "session/2026-07-04-22ca589f")

    def test_name_is_a_session_guard_session_branch(self) -> None:
        from lib import session_guard
        self.assertTrue(session_guard.is_session_branch(
            HOOK.session_branch_name(SID, date="2026-07-04")))


class TestEnsureSessionBranch(unittest.TestCase):
    def test_creates_off_clean_main(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td))
            res = HOOK.ensure_session_branch(str(repo), SID, date="2026-07-04")
            self.assertEqual(res["action"], "created", msg=res["message"])
            self.assertEqual(
                _git(repo, "branch", "--show-current").stdout.strip(),
                "session/2026-07-04-22ca589f",
            )

    def test_resume_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td))
            HOOK.ensure_session_branch(str(repo), SID, date="2026-07-04")
            # Second fire, already on the branch → no-op, still on the branch.
            res = HOOK.ensure_session_branch(str(repo), SID, date="2026-07-04")
            self.assertEqual(res["action"], "noop", msg=res["message"])
            self.assertEqual(
                _git(repo, "branch", "--show-current").stdout.strip(),
                "session/2026-07-04-22ca589f",
            )

    def test_crash_leftover_session_branch_halts(self) -> None:
        # A leftover session branch from a prior crash, with no finalise-ledger
        # record → preflight HALTs (unfinalised-session-branch); the hook must NOT
        # branch again. (Ledger-backed auto-recovery is covered in test_session_guard.)
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td))
            _git(repo, "checkout", "-b", "session/2026-07-01-deadbeef")  # stale, unfinalised
            res = HOOK.ensure_session_branch(str(repo), SID, date="2026-07-04")
            self.assertEqual(res["action"], "halt", msg=res["message"])
            self.assertEqual(
                _git(repo, "branch", "--show-current").stdout.strip(),
                "session/2026-07-01-deadbeef")  # left as found

    def test_dirty_tree_halts_without_branching(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td))
            (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
            _git(repo, "add", "-A")  # staged, uncommitted → dirty
            res = HOOK.ensure_session_branch(str(repo), SID, date="2026-07-04")
            self.assertEqual(res["action"], "halt", msg=res["message"])
            # Left on main, never branched.
            self.assertEqual(
                _git(repo, "branch", "--show-current").stdout.strip(), "main")


if __name__ == "__main__":
    unittest.main()
