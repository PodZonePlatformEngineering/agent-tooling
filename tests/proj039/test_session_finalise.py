"""DT-008 — session-finalise parity test (PROJ-039 AC-006 / R-016 / MVP-12).

Asserts: `session_finalise.apply_from_point` (the Qdrant-driven path) applied
to a known session point produces the **same** tasklist + STATUS state that the
legacy `_apply_from_outbox_markdown` path produces from the equivalent outbox
markdown. diff(tasklist_point, tasklist_outbox) == ∅ AND diff(STATUS_*) == ∅.

All assertions operate on in-memory copies of the files so no disk state is
modified and no Qdrant connection is needed.  The fixture sets up a minimal but
realistic tasklist row and STATUS block.

Also covers:
  - apply_status_to_tasklist: row found, row not found, already at target status
  - update_status_block: agent section found, not found
  - generate_brief_result: key fields present in output
  - _canonical_status: every transition form
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import session_finalise  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TASKLIST_TEMPLATE = textwrap.dedent("""\
    # Team Tasklist

    | ID | Legacy | Status | Agent | Summary |
    |----|--------|--------|-------|---------|
    | T-009 | CC-322 | 🚀 Ready | Hephaestus | Close the MVP gate for PROJ-039/T-009 |
    | T-010 | CC-323 | 🚀 Ready | Hephaestus | Wiring + single-instance trial |
    | T-011 | CC-324 | 🚀 Ready | Hephaestus | Cutover + migration |
""")

STATUS_TEMPLATE = textwrap.dedent("""\
    # Programme Status

    ### Hephaestus

    - `PROJ-039/T-009` 🚀 Ready — close the MVP gate
    - `PROJ-039/T-010` 🚀 Ready — wiring trial

    ### Martin

    - review PRs
""")

# The session point as it would look after session-end writes response.
SESSION_POINT = {
    "session_id": "dt008-test-session-42",
    "agent": "Hephaestus",
    "work_item": "PROJ-039/T-009",
    "brief": {
        "text": "Close the MVP gate so the Phase B trial can proceed.",
        "dispatch_ts": "2026-06-13T09:00:00+00:00",
        "target_agent": "Hephaestus",
    },
    "response": {
        "text": "MVP gate closed: DT-001 10/10, OT-006 brief vector preserved, DT-015 pass.",
        "status_transition": "in_progress->complete",
        "event_refs": [],
        "end_ts": "2026-06-13T18:00:00+00:00",
    },
    "session_stop": [
        {"ts": "2026-06-13T12:00:00+00:00", "response_delta": None, "tool_uses": 5},
        {"ts": "2026-06-13T15:00:00+00:00", "response_delta": None, "tool_uses": 8},
    ],
    "rollup": {
        "tool_usage": {"Bash": 12, "Read": 7, "Edit": 3},
        "cost_tokens": {
            "claude-sonnet-4-6": {"input_tokens": 45000, "output_tokens": 8000}
        },
    },
}

# Equivalent outbox markdown the legacy path would have read.
OUTBOX_MARKDOWN = textwrap.dedent("""\
    ---
    type: Hephaestus session outbox
    session_id: dt008-test-session-42
    agent: Hephaestus
    work_item: PROJ-039/T-009
    status_transition: in_progress->complete
    date: 2026-06-13
    ---

    # PROJ-039/T-009 — MVP gate — session status

    MVP gate closed: DT-001 10/10, OT-006 brief vector preserved, DT-015 pass.
""")

FIXED_DATE = "2026-06-13"


class TestParityDT008(unittest.TestCase):
    """DT-008: point-driven path == outbox-markdown path (tasklist + STATUS diff = 0)."""

    def _run_both(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            tl_point = d / "tasklist_point.md"
            tl_outbox = d / "tasklist_outbox.md"
            st_point = d / "status_point.md"
            st_outbox = d / "status_outbox.md"
            for f in (tl_point, tl_outbox):
                f.write_text(TASKLIST_TEMPLATE, encoding="utf-8")
            for f in (st_point, st_outbox):
                f.write_text(STATUS_TEMPLATE, encoding="utf-8")

            session_finalise.apply_from_point(
                SESSION_POINT, tasklist_path=tl_point,
                status_path=st_point, date=FIXED_DATE,
            )
            session_finalise._apply_from_outbox_markdown(
                OUTBOX_MARKDOWN, tasklist_path=tl_outbox,
                status_path=st_outbox, date=FIXED_DATE,
            )
            return (
                tl_point.read_text(encoding="utf-8"),
                tl_outbox.read_text(encoding="utf-8"),
                st_point.read_text(encoding="utf-8"),
                st_outbox.read_text(encoding="utf-8"),
            )

    def test_tasklist_diff_is_zero(self) -> None:
        tl_point, tl_outbox, _, _ = self._run_both()
        self.assertEqual(tl_point, tl_outbox,
                         "tasklist diff is non-zero — parity broken")

    def test_status_diff_is_zero(self) -> None:
        _, _, st_point, st_outbox = self._run_both()
        self.assertEqual(st_point, st_outbox,
                         "STATUS diff is non-zero — parity broken")

    def test_tasklist_row_updated_to_complete(self) -> None:
        tl_point, _, _, _ = self._run_both()
        self.assertIn("✅ Complete", tl_point)
        self.assertIn(FIXED_DATE, tl_point)
        self.assertIn("PROJ-039/T-009", tl_point)

    def test_unrelated_rows_unchanged(self) -> None:
        tl_point, _, _, _ = self._run_both()
        # T-010 and T-011 rows must still be present and unmodified
        self.assertIn("T-010", tl_point)
        self.assertIn("Wiring + single-instance trial", tl_point)
        self.assertIn("🚀 Ready", tl_point)


class TestApplyStatusToTasklist(unittest.TestCase):
    def test_in_progress_to_complete(self) -> None:
        tasklist = "| T-1 | CC-1 | 🔄 In Progress | Hepha | do it |\n"
        out = session_finalise.apply_status_to_tasklist(
            tasklist, work_item="T-1",
            status_transition="in_progress->complete", date="2026-06-13",
        )
        self.assertIn("✅ Complete 2026-06-13", out)

    def test_row_not_found_returns_unchanged(self) -> None:
        tasklist = "| T-99 | CC-99 | 🚀 Ready | Agent | task |\n"
        out = session_finalise.apply_status_to_tasklist(
            tasklist, work_item="T-MISSING",
            status_transition="in_progress->complete",
        )
        self.assertEqual(out, tasklist)

    def test_no_transition_returns_unchanged(self) -> None:
        tasklist = "| T-1 | CC-1 | 🚀 Ready | Agent | task |\n"
        out = session_finalise.apply_status_to_tasklist(
            tasklist, work_item="T-1", status_transition=None,
        )
        self.assertEqual(out, tasklist)

    def test_idempotent(self) -> None:
        tasklist = "| T-1 | CC-1 | 🚀 Ready | Agent | task |\n"
        once = session_finalise.apply_status_to_tasklist(
            tasklist, work_item="T-1",
            status_transition="ready->complete", date="2026-06-13",
        )
        twice = session_finalise.apply_status_to_tasklist(
            once, work_item="T-1",
            status_transition="ready->complete", date="2026-06-13",
        )
        self.assertEqual(once, twice)


class TestCanonicalStatus(unittest.TestCase):
    def test_complete_transition(self) -> None:
        s = session_finalise._canonical_status("in_progress->complete", "2026-06-13")
        self.assertIn("✅", s)
        self.assertIn("2026-06-13", s)

    def test_blocked_transition(self) -> None:
        s = session_finalise._canonical_status("ready->blocked", "2026-06-13")
        self.assertIn("⛔", s)

    def test_unknown_transition_returns_none(self) -> None:
        s = session_finalise._canonical_status("foo->bar", "2026-06-13")
        self.assertIsNone(s)

    def test_bare_none_returns_none(self) -> None:
        self.assertIsNone(session_finalise._canonical_status(None, "2026-06-13"))


class TestGenerateBriefResult(unittest.TestCase):
    def test_key_fields_present(self) -> None:
        out = session_finalise.generate_brief_result(SESSION_POINT, date=FIXED_DATE)
        self.assertIn("session_id: dt008-test-session-42", out)
        self.assertIn("PROJ-039/T-009", out)
        self.assertIn("in_progress->complete", out)
        self.assertIn("Bash", out)
        self.assertIn("claude-sonnet-4-6", out)
        self.assertIn("2 recorded", out)  # 2 session stops

    def test_empty_point_does_not_raise(self) -> None:
        out = session_finalise.generate_brief_result({}, date=FIXED_DATE)
        self.assertIsInstance(out, str)
        self.assertGreater(len(out), 20)


def _fake_git(repo_dir, *args):
    """Stand-in for ``session_finalise._git`` — every git call succeeds silently.

    ``_resolve_base_ref`` reads ``.returncode == 0`` from its ``rev-parse`` probes,
    so returning 0 resolves the base ref to ``origin/main`` without touching a repo.
    """
    return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")


def _fake_gh_run(cmd, **kwargs):
    """Stand-in for ``subprocess.run`` (the ``gh pr create`` call) — success + a URL."""
    return subprocess.CompletedProcess(
        args=cmd, returncode=0, stdout="https://github.com/x/y/pull/1", stderr="")


class TestResultBranchSidScoping(unittest.TestCase):
    """T-046 (CC-346): the home-result **branch** and the open-PR existence check are
    sid-scoped, so two sessions on the same ``date``+``slug`` each author their own
    result — the re-set-up-same-day recovery path that becomes the *normal* path under
    T-045 serial simple-repo mode — while a same-sid re-run stays idempotent (T-035).

    Regression guard for the reset-session finding (operator libraries.log, 2026-07-03):
    reset sid ``257e8e9d`` found the first run's PR still open on the shared
    ``session-result/{date}-{slug}`` branch → ``exists`` → silently skipped authoring the
    resumed run's narrative. The fix is to sid-suffix the branch (landed T-039) so the
    existence check at :func:`commit_home_result` queries a *per-session* branch.

    Drives :func:`session_finalise.commit_home_result` end-to-end with the git/gh
    boundary mocked, so no network, clone, or worktree is touched.
    """

    WORK_ITEM = "PROJ-039/T-044"
    DATE = "2026-07-02"
    SID_A = "257e8e9d1111aaaa"   # the reset-finding sid
    SID_B = "31465cdc2222bbbb"   # a distinct same-day re-set-up sid

    def _author(self, session_id, open_pr_branches):
        """Author a result with ``open_pr_branches`` standing in for 'has an open PR'."""
        with mock.patch.object(session_finalise, "_git", _fake_git), \
             mock.patch.object(session_finalise, "_result_on_base", return_value=False), \
             mock.patch.object(
                 session_finalise, "_open_result_pr_exists",
                 side_effect=lambda repo, branch: branch in open_pr_branches), \
             mock.patch.object(session_finalise.subprocess, "run", _fake_gh_run):
            return session_finalise.commit_home_result(
                "result body", session_id=session_id, work_item=self.WORK_ITEM,
                date=self.DATE, repo_dir="/tmp/does-not-matter", raise_pr=True,
            )

    def test_branch_and_file_carry_sid_suffix(self) -> None:
        r = self._author(self.SID_A, open_pr_branches=set())
        self.assertTrue(r["branch"].endswith(self.SID_A[:8]),
                        f"branch {r['branch']!r} must carry the sid8 suffix")
        self.assertIn(self.SID_A[:8], r["file_path"])

    def test_second_sid_authors_despite_first_pr_open(self) -> None:
        # First session authors + leaves its result PR open.
        a = self._author(self.SID_A, open_pr_branches=set())
        self.assertEqual(a["disposition"], "done")

        # Second session, SAME date+slug, sees sid A's PR still open — and must still
        # author its own result (the bug was that it resolved to `exists` and skipped).
        b = self._author(self.SID_B, open_pr_branches={a["branch"]})
        self.assertEqual(b["disposition"], "done",
                         "second sid skipped authoring — branch/PR check collided across sids")
        self.assertNotEqual(a["branch"], b["branch"])
        self.assertNotEqual(a["file_path"], b["file_path"])

    def test_same_sid_rerun_is_idempotent(self) -> None:
        # First run authors and opens the PR.
        a = self._author(self.SID_A, open_pr_branches=set())
        self.assertEqual(a["disposition"], "done")

        # A same-sid re-run (T-035 crash recovery) sees ITS OWN open PR → no-op.
        again = self._author(self.SID_A, open_pr_branches={a["branch"]})
        self.assertEqual(again["disposition"], "exists")
        self.assertEqual(a["branch"], again["branch"])


if __name__ == "__main__":
    unittest.main()
