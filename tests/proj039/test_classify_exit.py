"""Tests for lib/classify_exit.py — launch-wrapper exit classification
(PROJ-039/T-108, proposal §5). The proposal's flagged main risk: this is the
hard acceptance gate before the run loop is wired up (brief §"Scope" item 1).

Fixtures are the LITERAL captured markers from proposal §5's table (live data,
2026-07-19/20), plus an unrecognised-exit case (never loop forever) and the
precedence case §5 calls out explicitly (a captured limit string wins over a
trailing Brief-Status line from the same transcript).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib.classify_exit import (  # noqa: E402
    API_ERROR,
    COMPLETE,
    OTHER,
    SESSION_LIMIT,
    brief_status,
    classify_exit,
    detect_process_outcome,
)

# --- proposal §5's captured markers, verbatim ---

FABLE_LIMIT = (
    "You've reached your Fable 5 limit. Run /usage-credits to continue or "
    "switch models with /model."
)
RESET_TIME_LIMIT = "You've hit your session limit · resets 6:30am (Europe/London)"
API_ERROR_TEXT = (
    "API Error: Connection closed mid-response. The response above may be "
    "incomplete."
)
BRIEF_COMPLETE_LINE = "Brief-Status: complete"
BRIEF_IN_PROGRESS_LINE = "Brief-Status: in_progress"


class TestSessionLimitFixtures(unittest.TestCase):
    """Both captured session-limit phrasings — different wording, same class."""

    def test_fable5_limit_wording(self):
        self.assertEqual(detect_process_outcome(FABLE_LIMIT), SESSION_LIMIT)
        self.assertEqual(classify_exit(FABLE_LIMIT, exit_code=1), SESSION_LIMIT)

    def test_reset_time_limit_wording(self):
        self.assertEqual(detect_process_outcome(RESET_TIME_LIMIT), SESSION_LIMIT)
        self.assertEqual(classify_exit(RESET_TIME_LIMIT, exit_code=1), SESSION_LIMIT)

    def test_third_wording_is_still_tolerantly_matched(self):
        # A plausible third phrasing (proposal flags more variants are likely):
        # not a literal-string test fixture, but proves the tolerant pattern
        # generalises rather than pinning to exactly two known strings.
        text = "You have reached your usage limit for this session."
        self.assertEqual(detect_process_outcome(text), SESSION_LIMIT)


class TestApiErrorFixture(unittest.TestCase):
    def test_api_error_wording(self):
        self.assertEqual(detect_process_outcome(API_ERROR_TEXT), API_ERROR)
        self.assertEqual(classify_exit(API_ERROR_TEXT, exit_code=1), API_ERROR)


class TestBriefStatusFixtures(unittest.TestCase):
    def test_brief_complete_exact_match(self):
        self.assertEqual(brief_status(BRIEF_COMPLETE_LINE), "complete")

    def test_brief_in_progress_exact_match(self):
        self.assertEqual(brief_status(BRIEF_IN_PROGRESS_LINE), "in_progress")

    def test_absent_line_is_none(self):
        self.assertIsNone(brief_status("Some other closing remark, no status line."))

    def test_near_miss_text_is_not_fuzzy_matched(self):
        # Brief-state markers are exact, not fuzzy (§5) — trailing punctuation,
        # extra whitespace, or a paraphrase must NOT count as a match.
        self.assertIsNone(brief_status("Brief-Status: Complete"))
        self.assertIsNone(brief_status("The brief status is complete."))


class TestCleanExitRouting(unittest.TestCase):
    """A clean process outcome only reaches COMPLETE via the exact brief line;
    anything else (absent, in_progress, exit!=0) routes to OTHER — the same
    bucket the raise-to-lead-and-exit convention lands in (§5)."""

    def test_clean_exit_with_complete_line_is_complete(self):
        text = f"...closing remarks...\n{BRIEF_COMPLETE_LINE}\n"
        self.assertEqual(classify_exit(text, exit_code=0), COMPLETE)

    def test_clean_exit_with_in_progress_line_is_other(self):
        text = f"...raised to Hermes...\n{BRIEF_IN_PROGRESS_LINE}\n"
        self.assertEqual(classify_exit(text, exit_code=0), OTHER)

    def test_clean_exit_with_no_status_line_is_other(self):
        text = "Session finished but forgot to print a status line."
        self.assertEqual(classify_exit(text, exit_code=0), OTHER)

    def test_nonzero_exit_with_complete_line_is_other(self):
        # exit_code must ALSO be 0 for COMPLETE — a nonzero exit alongside the
        # line text (e.g. a crash right after printing) is not a clean success.
        text = f"{BRIEF_COMPLETE_LINE}\n"
        self.assertEqual(classify_exit(text, exit_code=1), OTHER)


class TestUnknownExitFallsThroughSafely(unittest.TestCase):
    """An unrecognised/unrecognisable exit must land in OTHER, never loop
    forever (brief's explicit acceptance requirement)."""

    def test_garbage_output_no_markers(self):
        text = "Segmentation fault (core dumped)"
        self.assertEqual(classify_exit(text, exit_code=139), OTHER)

    def test_empty_output(self):
        self.assertEqual(classify_exit("", exit_code=1), OTHER)
        self.assertEqual(classify_exit("", exit_code=0), OTHER)


class TestPrecedenceWhenMarkersConflict(unittest.TestCase):
    """§5: a limit-stop's final response is often empty/stub precisely because
    the limit interrupted it (the bda7781f live example). A captured limit/error
    string anywhere in the process output is authoritative for axis 1
    regardless of what axis 2 (final response text) shows."""

    def test_session_limit_marker_wins_over_trailing_brief_in_progress(self):
        transcript = (
            f"...working on the task...\n{FABLE_LIMIT}\n{BRIEF_IN_PROGRESS_LINE}\n"
        )
        self.assertEqual(classify_exit(transcript, exit_code=1), SESSION_LIMIT)

    def test_api_error_marker_wins_over_trailing_brief_complete(self):
        # Pathological but must still resolve deterministically to axis 1.
        transcript = f"{API_ERROR_TEXT}\n{BRIEF_COMPLETE_LINE}\n"
        self.assertEqual(classify_exit(transcript, exit_code=1), API_ERROR)

    def test_separate_final_response_text_still_defers_to_axis1_marker(self):
        process_output = f"...\n{FABLE_LIMIT}\n"
        final_response = BRIEF_IN_PROGRESS_LINE
        self.assertEqual(
            classify_exit(process_output, exit_code=1,
                          final_response_text=final_response),
            SESSION_LIMIT,
        )


if __name__ == "__main__":
    unittest.main()
