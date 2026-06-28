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

    def test_missing_sections_render_placeholder(self) -> None:
        point = {
            "session_id": "s", "agent": "A", "work_item": "W",
            "response": {"text": "Just a flat summary, no headers."},
        }
        out = session_finalise.generate_session_result(point, date=FIXED_DATE)
        # Every canonical section still rendered; unfilled ones say so.
        self.assertEqual(out.count("_None recorded._"), len(session_finalise.RESULT_SECTIONS))

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


if __name__ == "__main__":
    unittest.main()
