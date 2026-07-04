"""R-3 (PROJ-011/T-021, CC-351) — trainee session PR on SessionEnd.

Two guards:
  * :func:`build_trainee_pr_body` — the PR body is **both** the substrate session
    summary AND the fixed review checklist (brief R-3, resolved 2026-07-04), with the
    T-047 raw-response fallback (override → response.text → placeholder, never empty).
  * :func:`author_trainee_session_pr` — commits the live working tree to the session
    branch and (with ``raise_pr=False`` here, to stay offline) leaves the work
    committed + ahead of base. Covers the happy path, the on-main no-op, and the
    empty-branch no-op.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from lib import session_finalise as SF

SID = "22ca589f-82ce-410b-b766-4a726b1a710c"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


def _make_repo(tmp: Path) -> Path:
    origin = tmp / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True,
                   capture_output=True)
    repo = tmp / "repo"
    subprocess.run(["git", "clone", str(origin), str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    _git(repo, "push", "-u", "origin", "main")
    return repo


class TestBuildTraineePrBody(unittest.TestCase):
    POINT = {"brief_id": "training/2026-07-02-python-basics-sam",
             "work_item": "PROJ-011/T-021",
             "response": {"text": "## Completed\n- module 1\n"}}

    def test_body_has_summary_and_checklist(self) -> None:
        body = SF.build_trainee_pr_body(
            self.POINT, brief_id=self.POINT["brief_id"], date="2026-07-04",
            session_id=SID)
        self.assertIn("## Session summary", body)
        self.assertIn("module 1", body)                 # summary from response.text
        self.assertIn("Review checklist", body)         # the fixed checklist
        self.assertIn("Files changed reviewed", body)
        self.assertIn("training/2026-07-02-python-basics-sam", body)

    def test_override_wins_over_response_text(self) -> None:
        body = SF.build_trainee_pr_body(
            self.POINT, brief_id=self.POINT["brief_id"], date="2026-07-04",
            session_id=SID, summary_override="OVERRIDE SUMMARY")
        self.assertIn("OVERRIDE SUMMARY", body)
        self.assertNotIn("module 1", body)

    def test_empty_summary_falls_back_not_blank(self) -> None:
        body = SF.build_trainee_pr_body(
            {"response": {}}, brief_id=None, date="2026-07-04", session_id=SID)
        self.assertIn("No session summary was captured", body)
        self.assertIn("Review checklist", body)


class TestAuthorTraineeSessionPr(unittest.TestCase):
    POINT = {"brief_id": "training/2026-07-02-python-basics-sam",
             "response": {"text": "did the work"}}

    def test_commits_working_tree_to_session_branch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td))
            _git(repo, "checkout", "-b", "session/2026-07-04-22ca589f")
            (repo / "answer.py").write_text("print('hi')\n", encoding="utf-8")  # trainee work, uncommitted
            res = SF.author_trainee_session_pr(
                self.POINT, session_id=SID, repo_dir=str(repo),
                brief_id=self.POINT["brief_id"], date="2026-07-04", raise_pr=False)
            self.assertEqual(res["disposition"], "done", msg=res["reason"])
            self.assertTrue(res["committed"])
            # The work is committed on the session branch and pushed to origin.
            log = _git(repo, "log", "--oneline", "-1").stdout
            self.assertIn("session(2026-07-04)", log)
            self.assertTrue(_git(repo, "cat-file", "-e",
                                 "origin/session/2026-07-04-22ca589f:answer.py").returncode == 0)

    def test_on_main_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td))  # still on main
            res = SF.author_trainee_session_pr(
                self.POINT, session_id=SID, repo_dir=str(repo),
                brief_id=self.POINT["brief_id"], date="2026-07-04", raise_pr=False)
            self.assertEqual(res["disposition"], "noop", msg=res["reason"])

    def test_empty_session_branch_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td))
            _git(repo, "checkout", "-b", "session/2026-07-04-22ca589f")  # no work, no commits ahead
            res = SF.author_trainee_session_pr(
                self.POINT, session_id=SID, repo_dir=str(repo),
                brief_id=self.POINT["brief_id"], date="2026-07-04", raise_pr=False)
            self.assertEqual(res["disposition"], "noop", msg=res["reason"])
            self.assertIn("nothing to PR", res["reason"])


if __name__ == "__main__":
    unittest.main()
