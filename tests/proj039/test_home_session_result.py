"""T-035 — hooks-only home-repo session result authoring (PROJ-039/CC-334).

In a migrated home repo there is NO `/session-end` skill — the SessionEnd finalise
hook OWNS the session result. This test proves the testable core of that step
(`lib.session_finalise`):

  * `generate_session_result` renders the five canonical structured sections
    (Completed / Started / Blockers / Decisions / Questions for Martin), extracting
    them from the substrate `response.text` where present.
  * `extract_sections` picks up header-shaped section labels (and aliases) and
    ignores prose that merely contains the words.
  * `commit_home_result` authors `results/session-{date}-{slug}.md` on a branch off
    `main` **without touching the live working tree / branch** (isolated worktree),
    pushes it, and is **idempotent** — a re-run after the result lands on `main`
    no-ops with disposition `exists` (mirrors the hook's ledger guard).

The git test runs against real temp repos (a bare "origin" + a clone); `gh` is
never invoked (raise_pr=False), so it is hermetic. Pure-function tests need no I/O.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import session_finalise  # noqa: E402

FIXED_DATE = "2026-06-28"

# A session point as it looks after the SessionEnd response upsert. The response
# text carries the canonical sections the agent authored in its final /exit turn.
SESSION_POINT = {
    "session_id": "t035-home-result-session-7",
    "agent": "Hephaestus",
    "work_item": "PROJ-039/T-035",
    "brief": {
        "text": "Move session-end result authoring into the finalise hook.",
        "dispatch_ts": "2026-06-28T09:00:00+00:00",
        "target_agent": "Hephaestus",
    },
    "response": {
        "text": textwrap.dedent("""\
            Landed the hooks-only home-repo result authoring.

            ## Completed
            - Finalise hook authors the home-repo result + PR.
            - Idempotent via the ledger + base-branch check.

            ## Started
            - Nothing left mid-flight.

            ## Blockers
            - None.

            ## Decisions
            - Worktree-based authoring keeps the live branch untouched.

            ## Questions for Martin
            - OK to refresh ADR-008 §6 layout in a separate apex PR?
            """),
        "status_transition": "in_progress->complete",
        "event_refs": [],
        "end_ts": "2026-06-28T18:00:00+00:00",
    },
    "rollup": {
        "tool_usage": {"Edit": 9, "Bash": 14, "Read": 11},
        "cost_tokens": {
            "claude-opus-4-8": {"input_tokens": 120000, "output_tokens": 15000}
        },
    },
}


class TestGenerateSessionResult(unittest.TestCase):
    def test_all_canonical_sections_present(self) -> None:
        out = session_finalise.generate_session_result(SESSION_POINT, date=FIXED_DATE)
        for label in session_finalise.RESULT_SECTIONS:
            self.assertIn(f"## {label}", out, f"missing section: {label}")

    def test_questions_for_martin_surfaced(self) -> None:
        out = session_finalise.generate_session_result(SESSION_POINT, date=FIXED_DATE)
        self.assertIn("OK to refresh ADR-008", out)

    def test_extracted_body_under_correct_header(self) -> None:
        out = session_finalise.generate_session_result(SESSION_POINT, date=FIXED_DATE)
        completed_idx = out.index("## Completed")
        started_idx = out.index("## Started")
        # The "authors the home-repo result" line sits under Completed, before Started.
        body_idx = out.index("authors the home-repo result")
        self.assertTrue(completed_idx < body_idx < started_idx)

    def test_frontmatter_and_identity(self) -> None:
        out = session_finalise.generate_session_result(SESSION_POINT, date=FIXED_DATE)
        self.assertIn("type: session-result", out)
        self.assertIn("session_id: t035-home-result-session-7", out)
        self.assertIn("work_item: PROJ-039/T-035", out)
        self.assertIn("in_progress->complete", out)
        self.assertIn("claude-opus-4-8", out)

    def test_freeform_response_embedded_verbatim_not_stubbed(self) -> None:
        # T-047 (CC-348): a real free-form response with none of the canonical
        # headers must NOT be buried under five "None recorded" stubs (home#20/#21).
        point = {
            "session_id": "sid-freeform", "agent": "A", "work_item": "W",
            "response": {"text": "Just a flat summary, no headers."},
        }
        out = session_finalise.generate_session_result(point, date=FIXED_DATE)
        self.assertNotIn("_None recorded._", out)
        self.assertIn("## Session response (raw)", out)
        self.assertIn("Just a flat summary, no headers.", out)

    def test_headless_shaped_response_non_stub_result(self) -> None:
        # T-047: the headless `claude -p` final turn used its OWN `##`/`###`
        # headers (not the canonical set) — the 22ca589f home#24 shape. The result
        # must surface it, not stub it.
        point = {
            "session_id": "sid-headless", "agent": "hephaestus",
            "work_item": "PROJ-011/T-021",
            "response": {"text": textwrap.dedent("""\
                All work is landed and verified. Here's the summary.

                ## PROJ-011/T-021 (CC-351) + rider T-050 — complete
                ### Verification
                - 207 tests green.

                Brief-Status: complete
                """)},
        }
        out = session_finalise.generate_session_result(point, date=FIXED_DATE)
        self.assertNotIn("_None recorded._", out)
        self.assertIn("## Session response (raw)", out)
        self.assertIn("Brief-Status: complete", out)
        self.assertIn("207 tests green", out)

    def test_partial_canonical_sections_still_stub_the_rest(self) -> None:
        # When the response DID use canonical headers, absent ones stub as before —
        # the fallback only fires when NONE are found.
        point = {
            "session_id": "sid-partial", "agent": "A", "work_item": "W",
            "response": {"text": "## Completed\n- did a thing\n"},
        }
        out = session_finalise.generate_session_result(point, date=FIXED_DATE)
        self.assertNotIn("## Session response (raw)", out)
        self.assertIn("did a thing", out)
        # Started/Blockers/Decisions/Questions absent → stubbed.
        self.assertEqual(out.count("_None recorded._"),
                         len(session_finalise.RESULT_SECTIONS) - 1)

    def test_no_real_response_still_stubs(self) -> None:
        # Genuinely empty response → the stubs are the honest rendering.
        point = {
            "session_id": "sid-empty", "agent": "A", "work_item": "W",
            "response": {"text": "(no response)"},
        }
        out = session_finalise.generate_session_result(point, date=FIXED_DATE)
        self.assertNotIn("## Session response (raw)", out)
        self.assertEqual(out.count("_None recorded._"),
                         len(session_finalise.RESULT_SECTIONS))

    def test_empty_point_does_not_raise(self) -> None:
        out = session_finalise.generate_session_result({}, date=FIXED_DATE)
        self.assertIsInstance(out, str)
        self.assertGreater(len(out), 20)


class TestExtractSections(unittest.TestCase):
    def test_alias_and_bold_headers(self) -> None:
        text = textwrap.dedent("""\
            **Blocked**
            waiting on review

            Questions:
            one open question
            """)
        got = session_finalise.extract_sections(text)
        self.assertEqual(got.get("Blockers"), "waiting on review")
        self.assertEqual(got.get("Questions for Martin"), "one open question")

    def test_prose_mentioning_label_is_not_a_header(self) -> None:
        text = "We completed the work and there are no blockers in this paragraph.\n"
        got = session_finalise.extract_sections(text)
        self.assertEqual(got, {})

    def test_non_canonical_header_terminates_section(self) -> None:
        text = textwrap.dedent("""\
            ## Completed
            did the thing

            ## Notes
            irrelevant trailing prose
            """)
        got = session_finalise.extract_sections(text)
        self.assertEqual(got.get("Completed"), "did the thing")


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True, check=False)


class TestResultPrBodyToolingStamp(unittest.TestCase):
    def test_carries_tooling_version(self) -> None:
        # PROJ-039/T-055 — every result PR body records the shipped tooling version
        # ("unknown" absent a manifest, exercised here with no CLAUDE_PROJECT_DIR set).
        body = session_finalise._result_pr_body("sid-1234567890", "PROJ-039/T-055",
                                                 "2026-07-06")
        self.assertIn("tooling: unknown", body)


class TestCommitHomeResult(unittest.TestCase):
    """Real-git authoring: authors off main without disturbing the work branch,
    and no-ops once the result is on main (idempotent)."""

    def _setup_repo(self, d: Path) -> str:
        origin = d / "origin.git"
        work = d / "work"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                       capture_output=True, check=True)
        subprocess.run(["git", "clone", str(origin), str(work)],
                       capture_output=True, check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            _git(str(work), "config", k, v)
        (work / "README.md").write_text("seed\n", encoding="utf-8")
        _git(str(work), "add", "README.md")
        _git(str(work), "commit", "-m", "seed")
        _git(str(work), "push", "origin", "main")
        # Simulate the session's own work branch with a dirty working tree.
        _git(str(work), "checkout", "-b", "session/work-branch")
        (work / "wip.txt").write_text("uncommitted work in progress\n", encoding="utf-8")
        return str(work)

    def test_authors_off_main_without_touching_work_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            work = self._setup_repo(d)

            text = session_finalise.generate_session_result(SESSION_POINT, date=FIXED_DATE)
            res = session_finalise.commit_home_result(
                text, session_id=SESSION_POINT["session_id"],
                work_item=SESSION_POINT["work_item"], date=FIXED_DATE,
                repo_dir=work, raise_pr=False, base_branch="main",
            )
            self.assertTrue(res["ok"], res)
            self.assertEqual(res["disposition"], "done", res)

            # Live branch + dirty working file are untouched.
            cur = _git(work, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            self.assertEqual(cur, "session/work-branch")
            self.assertEqual((Path(work) / "wip.txt").read_text(encoding="utf-8"),
                             "uncommitted work in progress\n")
            # No stray worktrees left registered.
            wl = _git(work, "worktree", "list").stdout
            self.assertNotIn("session-result-", wl)

            # The result landed on the pushed result branch.
            branch = res["branch"]
            show = _git(work, "show", f"origin/{branch}:{res['file_path']}")
            self.assertEqual(show.returncode, 0, show.stderr)
            self.assertIn("Questions for Martin", show.stdout)

    def test_idempotent_once_on_main(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            work = self._setup_repo(d)
            text = session_finalise.generate_session_result(SESSION_POINT, date=FIXED_DATE)

            first = session_finalise.commit_home_result(
                text, session_id=SESSION_POINT["session_id"],
                work_item=SESSION_POINT["work_item"], date=FIXED_DATE,
                repo_dir=work, raise_pr=False, base_branch="main",
            )
            self.assertEqual(first["disposition"], "done", first)

            # Simulate the result PR being merged: bring the branch onto main + push.
            _git(work, "fetch", "origin", first["branch"])
            _git(work, "checkout", "main")
            _git(work, "merge", "--no-edit", "FETCH_HEAD")
            _git(work, "push", "origin", "main")
            _git(work, "checkout", "session/work-branch")

            second = session_finalise.commit_home_result(
                text, session_id=SESSION_POINT["session_id"],
                work_item=SESSION_POINT["work_item"], date=FIXED_DATE,
                repo_dir=work, raise_pr=False, base_branch="main",
            )
            self.assertTrue(second["ok"], second)
            self.assertEqual(second["disposition"], "exists", second)


class TestSessionBranchSurvivesFinalise(unittest.TestCase):
    """PROJ-039/T-060 — a commit made on the session's own branch (e.g. a resident
    ``.claude/`` self-sync) must be reachable from the result PR after finalise, not
    stranded when the end-guard reaps the session branch.

    Regression for the run-4/T-059 defect: the result PR forked from ``main`` carried
    only ``results/``, so a self-sync commit on the session branch became unreachable
    once the branch was deleted. ``commit_home_result``/``author_home_result`` now
    accept ``session_branch`` and fork the result branch from there when it carries
    commits ``base_branch`` does not (see :func:`session_finalise._resolve_fork_ref`).
    """

    def _setup_repo(self, d: Path) -> str:
        origin = d / "origin.git"
        work = d / "work"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                       capture_output=True, check=True)
        subprocess.run(["git", "clone", str(origin), str(work)],
                       capture_output=True, check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            _git(str(work), "config", k, v)
        (work / "README.md").write_text("seed\n", encoding="utf-8")
        _git(str(work), "add", "README.md")
        _git(str(work), "commit", "-m", "seed")
        _git(str(work), "push", "origin", "main")
        _git(str(work), "checkout", "-b", "session/self-sync-branch")
        return str(work)

    def test_session_branch_commit_rides_result_pr(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            work = self._setup_repo(d)

            # Simulate a mid-session resident self-sync commit on the session branch.
            claude_dir = Path(work) / ".claude"
            claude_dir.mkdir(exist_ok=True)
            (claude_dir / "hooks-marker.txt").write_text("synced\n", encoding="utf-8")
            _git(work, "add", ".claude/hooks-marker.txt")
            _git(work, "commit", "-m", "chore: self-sync .claude/")

            text = session_finalise.generate_session_result(SESSION_POINT, date=FIXED_DATE)
            res = session_finalise.commit_home_result(
                text, session_id=SESSION_POINT["session_id"],
                work_item=SESSION_POINT["work_item"], date=FIXED_DATE,
                repo_dir=work, raise_pr=False, base_branch="main",
                session_branch="session/self-sync-branch",
            )
            self.assertTrue(res["ok"], res)
            self.assertEqual(res["disposition"], "done", res)

            # The self-sync commit is reachable from the pushed result branch.
            branch = res["branch"]
            show = _git(work, "show", f"origin/{branch}:.claude/hooks-marker.txt")
            self.assertEqual(show.returncode, 0, show.stderr)
            self.assertEqual(show.stdout, "synced\n")
            # And the result file is still there alongside it.
            result_show = _git(work, "show", f"origin/{branch}:{res['file_path']}")
            self.assertEqual(result_show.returncode, 0, result_show.stderr)

    def test_result_only_session_forks_off_base_unchanged(self) -> None:
        # No commits on the session branch beyond base — passing session_branch
        # must not change today's behaviour (fork off base_branch).
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            work = self._setup_repo(d)  # session branch == main tip, no extra commits

            text = session_finalise.generate_session_result(SESSION_POINT, date=FIXED_DATE)
            res = session_finalise.commit_home_result(
                text, session_id=SESSION_POINT["session_id"],
                work_item=SESSION_POINT["work_item"], date=FIXED_DATE,
                repo_dir=work, raise_pr=False, base_branch="main",
                session_branch="session/self-sync-branch",
            )
            self.assertTrue(res["ok"], res)
            self.assertEqual(res["disposition"], "done", res)
            branch = res["branch"]
            # Exactly one commit ahead of main (the result commit) — no session-branch
            # commits pulled in, since there were none beyond base.
            count = _git(work, "rev-list", "--count",
                         f"origin/main..origin/{branch}").stdout.strip()
            self.assertEqual(count, "1", count)

    def test_resolve_fork_ref_prefers_session_branch_when_ahead(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            work = self._setup_repo(d)
            (Path(work) / "extra.txt").write_text("x\n", encoding="utf-8")
            _git(work, "add", "extra.txt")
            _git(work, "commit", "-m", "extra")

            fork = session_finalise._resolve_fork_ref(
                work, "main", "session/self-sync-branch")
            self.assertEqual(fork, "session/self-sync-branch")

    def test_resolve_fork_ref_falls_back_when_no_session_branch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            work = self._setup_repo(d)
            fork = session_finalise._resolve_fork_ref(work, "main", None)
            self.assertEqual(fork, "main")
            fork_missing = session_finalise._resolve_fork_ref(
                work, "main", "no/such/branch")
            self.assertEqual(fork_missing, "main")


class TestSessionIdIdempotencyKey(unittest.TestCase):
    """T-039 — the result idempotency key is the **session_id**, not date+slug.

    Regression for the C2c-build result that was hidden on 2026-06-29: a session
    re-set-up on the same day (after a crash) shares the prior attempt's
    ``date``+``slug``. When the existence check keyed on ``session-{date}-{slug}.md``
    the second (real) session saw the first attempt's file → ``exists`` → silently
    skipped authoring its own result. Keying on session_id fixes both directions:

      * two distinct sids on the same date+slug → **two** results (no skip);
      * the same sid re-run → **one** result (still idempotent — T-035 recovery).
    """

    SID_A = "11111111-aaaa-4bbb-8ccc-1111aaaa1111"
    SID_B = "22222222-bbbb-4ccc-8ddd-2222bbbb2222"

    def test_two_sids_same_date_slug_author_two_results(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            work = self._setup_repo(d)
            point_a = {**SESSION_POINT, "session_id": self.SID_A}
            point_b = {**SESSION_POINT, "session_id": self.SID_B}
            text_a = session_finalise.generate_session_result(point_a, date=FIXED_DATE)
            text_b = session_finalise.generate_session_result(point_b, date=FIXED_DATE)

            res_a = session_finalise.commit_home_result(
                text_a, session_id=self.SID_A, work_item=SESSION_POINT["work_item"],
                date=FIXED_DATE, repo_dir=work, raise_pr=False, base_branch="main",
            )
            # Land A on main, simulating the first attempt's PR merging.
            self._merge_branch_to_main(work, res_a["branch"])

            # The SECOND session shares date+slug but has a distinct sid: it must
            # NOT see A's result as its own — it authors a fresh one (no skip).
            res_b = session_finalise.commit_home_result(
                text_b, session_id=self.SID_B, work_item=SESSION_POINT["work_item"],
                date=FIXED_DATE, repo_dir=work, raise_pr=False, base_branch="main",
            )

            self.assertEqual(res_a["disposition"], "done", res_a)
            self.assertEqual(res_b["disposition"], "done", res_b)
            self.assertNotEqual(res_a["file_path"], res_b["file_path"])
            self.assertNotEqual(res_a["branch"], res_b["branch"])
            self.assertIn(self.SID_A[:8], res_a["file_path"])
            self.assertIn(self.SID_B[:8], res_b["file_path"])

    def test_same_sid_rerun_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            work = self._setup_repo(d)
            point = {**SESSION_POINT, "session_id": self.SID_A}
            text = session_finalise.generate_session_result(point, date=FIXED_DATE)

            first = session_finalise.commit_home_result(
                text, session_id=self.SID_A, work_item=SESSION_POINT["work_item"],
                date=FIXED_DATE, repo_dir=work, raise_pr=False, base_branch="main",
            )
            self.assertEqual(first["disposition"], "done", first)
            self._merge_branch_to_main(work, first["branch"])

            # Same sid, same date+slug → resolves to the same filename → no-op.
            second = session_finalise.commit_home_result(
                text, session_id=self.SID_A, work_item=SESSION_POINT["work_item"],
                date=FIXED_DATE, repo_dir=work, raise_pr=False, base_branch="main",
            )
            self.assertEqual(second["disposition"], "exists", second)
            self.assertEqual(first["file_path"], second["file_path"])

    # -- helpers (reuse TestCommitHomeResult's repo setup) ------------------
    _setup_repo = TestCommitHomeResult._setup_repo

    @staticmethod
    def _merge_branch_to_main(work: str, branch: str) -> None:
        _git(work, "fetch", "origin", branch)
        _git(work, "checkout", "main")
        _git(work, "merge", "--no-edit", "FETCH_HEAD")
        _git(work, "push", "origin", "main")
        _git(work, "checkout", "session/work-branch")


if __name__ == "__main__":
    unittest.main()
