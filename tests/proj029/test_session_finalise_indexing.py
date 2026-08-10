"""PROJ-029/T-257 — the SessionEnd finalise hook indexes a just-committed
result into `session_results` immediately (design doc §3.4: "the write-time
hook that produces the artefact is also the one that indexes it").

Asserts the WIRING, not the indexing internals (those are covered by
test_session_results_substrate.py) or the git plumbing (covered by
test_trunk_finalise.py / test_session_finalise.py's branch-mode tests):
`author_home_result` / `author_home_result_trunk` call
`_index_session_result` exactly when the underlying commit succeeded
(``ok=True``), with the exact text that was committed, and never when it
didn't (a failed/deferred commit must not index a result that isn't on disk).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import session_finalise  # noqa: E402

SESSION_POINT = {
    "session_id": "idx-test-session-1",
    "agent": "hephaestus",
    "work_item": "PROJ-029/T-257",
    "brief": {"text": "brief text", "dispatch_ts": "2026-08-10T09:00:00+00:00"},
    "response": {"text": "response text", "status_transition": "", "end_ts": "2026-08-10T10:00:00+00:00"},
    "rollup": {},
}


class TestAuthorHomeResultTrunkIndexing(unittest.TestCase):
    def test_indexes_on_success(self) -> None:
        fake_result = {"file_path": "results/session-2026-08-10-proj-029-t-257-idxtest1.md",
                        "ok": True, "disposition": "done"}
        with mock.patch.object(session_finalise, "commit_and_push_trunk",
                                return_value=fake_result) as m_commit, \
             mock.patch.object(session_finalise, "_index_session_result") as m_index:
            res = session_finalise.author_home_result_trunk(
                SESSION_POINT, session_id="idx-test-session-1",
                repo_dir="/tmp/home-podzone-hephaestus", date="2026-08-10",
            )
        self.assertIs(res, fake_result)
        m_commit.assert_called_once()
        m_index.assert_called_once()
        _, kwargs = m_index.call_args
        self.assertEqual(kwargs["repo_dir"], "/tmp/home-podzone-hephaestus")
        self.assertEqual(kwargs["rel_path"], fake_result["file_path"])
        self.assertEqual(kwargs["date"], "2026-08-10")
        self.assertIn("response text", kwargs["result_text"])

    def test_no_index_when_commit_not_ok(self) -> None:
        fake_result = {"file_path": "results/x.md", "ok": False, "disposition": "halted"}
        with mock.patch.object(session_finalise, "commit_and_push_trunk",
                                return_value=fake_result), \
             mock.patch.object(session_finalise, "_index_session_result") as m_index:
            session_finalise.author_home_result_trunk(
                SESSION_POINT, session_id="idx-test-session-1",
                repo_dir="/tmp/home-podzone-hephaestus", date="2026-08-10",
            )
        m_index.assert_not_called()


class TestAuthorHomeResultBranchModeIndexing(unittest.TestCase):
    def test_indexes_on_success(self) -> None:
        fake_result = {"file_path": "results/session-2026-08-10-proj-029-t-257-idxtest1.md",
                        "branch": "session-result/2026-08-10-proj-029-t-257-idxtest1",
                        "pr_url": "https://github.com/x/y/pull/1",
                        "ok": True, "disposition": "done"}
        with mock.patch.object(session_finalise, "commit_home_result",
                                return_value=fake_result) as m_commit, \
             mock.patch.object(session_finalise, "_index_session_result") as m_index:
            res = session_finalise.author_home_result(
                SESSION_POINT, session_id="idx-test-session-1",
                repo_dir="/tmp/home-podzone-hephaestus", date="2026-08-10",
            )
        self.assertIs(res, fake_result)
        m_commit.assert_called_once()
        m_index.assert_called_once()

    def test_no_index_on_deferred_cancelled(self) -> None:
        fake_result = {"file_path": "results/x.md", "ok": False,
                        "disposition": "deferred-cancelled"}
        with mock.patch.object(session_finalise, "commit_home_result",
                                return_value=fake_result), \
             mock.patch.object(session_finalise, "_index_session_result") as m_index:
            session_finalise.author_home_result(
                SESSION_POINT, session_id="idx-test-session-1",
                repo_dir="/tmp/home-podzone-hephaestus", date="2026-08-10",
            )
        m_index.assert_not_called()


class TestIndexSessionResultBestEffort(unittest.TestCase):
    def test_swallows_exceptions_from_the_upsert(self) -> None:
        with mock.patch("lib.session_results_substrate.upsert_result",
                         side_effect=RuntimeError("qdrant unreachable")):
            try:
                session_finalise._index_session_result(
                    SESSION_POINT, repo_dir="/tmp/home-x",
                    rel_path="results/f.md", result_text="body", date="2026-08-10",
                )
            except Exception as exc:  # pragma: no cover - the assertion IS "no raise"
                self.fail(f"_index_session_result must never raise, got: {exc}")

    def test_calls_upsert_with_repo_basename_and_filename(self) -> None:
        with mock.patch("lib.session_results_substrate.upsert_result") as m:
            session_finalise._index_session_result(
                SESSION_POINT, repo_dir="/Users/x/workspace/home-podzone-hephaestus",
                rel_path="results/session-2026-08-10-foo.md",
                result_text="the full text", date="2026-08-10",
            )
        m.assert_called_once_with(
            home_repo="home-podzone-hephaestus",
            filename="session-2026-08-10-foo.md",
            body="the full text",
            work_item="PROJ-029/T-257",
            agent="hephaestus",
            date="2026-08-10",
        )


if __name__ == "__main__":
    unittest.main()
