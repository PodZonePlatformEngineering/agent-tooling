"""R-1 (PROJ-011/T-021, CC-351) — brief-id from the first prompt.

Covers the pure logic that does not need Qdrant: the brief-id grammar
(``Brief:`` line vs bare ``{team}/{date}-{slug}`` id, precedence, no-op) and the
first-prompt sentinel guard. The end-to-end materialise (which needs the live
`briefs` collection) is exercised by the brief-first materialise tests + the E2E
dry proof; here we assert the parse/guard/precedence contract deterministically.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_hook():
    spec = importlib.util.spec_from_file_location(
        "first_prompt_brief", str(REPO_ROOT / "hooks" / "first-prompt-brief.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod


HOOK = _load_hook()


class TestParseBriefId(unittest.TestCase):
    def test_explicit_brief_line(self) -> None:
        self.assertEqual(
            HOOK.parse_brief_id("Brief: training/2026-07-02-python-basics-sam"),
            "training/2026-07-02-python-basics-sam",
        )

    def test_brief_line_case_insensitive_and_surrounded(self) -> None:
        prompt = "Hi!\nbrief:   podzone/2026-07-04-proj011-t021-template-hooks  \nthanks"
        self.assertEqual(
            HOOK.parse_brief_id(prompt),
            "podzone/2026-07-04-proj011-t021-template-hooks",
        )

    def test_bare_id_anywhere(self) -> None:
        prompt = "please start on training/2026-07-02-t044demo-sam today"
        self.assertEqual(
            HOOK.parse_brief_id(prompt), "training/2026-07-02-t044demo-sam"
        )

    def test_explicit_line_wins_over_bare(self) -> None:
        prompt = (
            "Brief: training/2026-07-02-real-one-sam\n"
            "(ignore the stale training/2026-01-01-old-one-sam below)"
        )
        self.assertEqual(
            HOOK.parse_brief_id(prompt), "training/2026-07-02-real-one-sam"
        )

    def test_no_brief_id_returns_none(self) -> None:
        for prompt in (
            "",
            "just get started on the python module please",
            "see docs/foo/bar.md and src/2026/thing.py",   # paths, no date-slug id
            "the meeting is 2026-07-04 in room training/2",  # no {date}-{slug} shape
        ):
            self.assertIsNone(HOOK.parse_brief_id(prompt), msg=repr(prompt))

    def test_bare_id_requires_slug_after_date(self) -> None:
        # A bare date with no trailing slug token must not match.
        self.assertIsNone(HOOK.parse_brief_id("training/2026-07-04"))


class TestFirstPromptGuard(unittest.TestCase):
    SID = "22ca589f-82ce-410b-b766-4a726b1a710c"

    def _workspace(self, tmp: Path, status: dict | None, identity: dict | None) -> str:
        cwd = tmp / "repo"
        (cwd / ".workspace").mkdir(parents=True)
        if status is not None:
            (cwd / ".workspace" / ".materialise-status.json").write_text(
                json.dumps(status), encoding="utf-8"
            )
        if identity is not None:
            (cwd / ".workspace" / "identity.json").write_text(
                json.dumps(identity), encoding="utf-8"
            )
        return str(cwd)

    def test_absent_sentinel_not_materialised(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = self._workspace(Path(td), None, None)
            self.assertFalse(HOOK.already_materialised(cwd, self.SID))

    def test_ok_false_not_materialised(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = self._workspace(
                Path(td), {"ok": False, "reason": "empty-brief"},
                {"session_id": self.SID},
            )
            self.assertFalse(HOOK.already_materialised(cwd, self.SID))

    def test_ok_true_this_session_is_materialised(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = self._workspace(
                Path(td), {"ok": True, "source": "brief"},
                {"session_id": self.SID},
            )
            self.assertTrue(HOOK.already_materialised(cwd, self.SID))

    def test_ok_true_but_prior_session_not_materialised_here(self) -> None:
        # The load-bearing case: a stale ok:true sentinel from a PREVIOUS session
        # (different sid) must NOT suppress first-prompt materialise for the new one.
        with tempfile.TemporaryDirectory() as td:
            cwd = self._workspace(
                Path(td), {"ok": True, "source": "brief"},
                {"session_id": "00000000-0000-0000-0000-000000000000"},
            )
            self.assertFalse(HOOK.already_materialised(cwd, self.SID))


if __name__ == "__main__":
    unittest.main()
