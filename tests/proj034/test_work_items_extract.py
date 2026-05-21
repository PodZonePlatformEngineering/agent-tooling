"""Tests for lib/work_items_extract.extract()."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import work_items_extract  # noqa: E402


class TestExtract(unittest.TestCase):

    def test_t1_empty_corpus(self) -> None:
        self.assertEqual(
            work_items_extract.extract([]),
            {"work_items": [], "projects": []},
        )

    def test_t2_single_canonical(self) -> None:
        r = work_items_extract.extract(["working on PROJ-034/T-008 today"])
        self.assertEqual(r["work_items"], ["PROJ-034/T-008"])
        self.assertEqual(r["projects"], ["PROJ-034"])

    def test_t3_multiple_distinct_dedupe_sort(self) -> None:
        r = work_items_extract.extract([
            "PROJ-034/T-010 and PROJ-033/T-001",
            "more PROJ-034/T-010 mentions",
        ])
        self.assertEqual(r["work_items"], ["PROJ-033/T-001", "PROJ-034/T-010"])
        self.assertEqual(r["projects"], ["PROJ-033", "PROJ-034"])

    def test_t4_lowercase_shorthand(self) -> None:
        r = work_items_extract.extract(["see proj034/t8 for details"])
        self.assertEqual(r["work_items"], ["PROJ-034/T-008"])
        self.assertEqual(r["projects"], ["PROJ-034"])

    def test_t5_bare_project_no_task(self) -> None:
        r = work_items_extract.extract(["context lives in PROJ-033 today"])
        self.assertEqual(r["work_items"], [])
        self.assertEqual(r["projects"], ["PROJ-033"])

    def test_t6_brief_filename(self) -> None:
        r = work_items_extract.extract([
            "Brief: team/hephaestus/incoming/2026-05-21-proj034-usage-report.md",
        ])
        self.assertIn("PROJ-034", r["projects"])

    def test_t7_cwd_slug_only(self) -> None:
        r = work_items_extract.extract(
            [],
            cwd="/Users/x/sessions/hephaestus-2026-05-21-proj034-foundation",
        )
        self.assertEqual(r["work_items"], [])
        self.assertEqual(r["projects"], ["PROJ-034"])

    def test_t8_letter_suffix(self) -> None:
        r = work_items_extract.extract(["see PROJ-034/T-6b for the gap-fill"])
        self.assertEqual(r["work_items"], ["PROJ-034/T-006b"])

    def test_t9_case_insensitive(self) -> None:
        r = work_items_extract.extract(["proj-029/T-7 plannerapi"])
        self.assertEqual(r["work_items"], ["PROJ-029/T-007"])

    def test_t10_padding(self) -> None:
        r = work_items_extract.extract(["PROJ-3/T-9 tiny"])
        self.assertEqual(r["work_items"], ["PROJ-003/T-009"])
        self.assertEqual(r["projects"], ["PROJ-003"])

    def test_t11_truncation_by_frequency(self) -> None:
        # 25 distinct work items: PROJ-001/T-001 .. PROJ-001/T-025.
        # Bump PROJ-001/T-099 to highest frequency so it survives.
        corpus = [f"PROJ-001/T-{i:03d}" for i in range(1, 26)]
        corpus.extend(["PROJ-001/T-099"] * 10)
        r = work_items_extract.extract(corpus)
        self.assertEqual(len(r["work_items"]), 20)
        self.assertIn("PROJ-001/T-099", r["work_items"])
        # Output should be alphabetically sorted.
        self.assertEqual(r["work_items"], sorted(r["work_items"]))

    def test_t12_five_digit_no_match(self) -> None:
        r = work_items_extract.extract(["PROJ-12345 should not match"])
        self.assertEqual(r["projects"], [])
        self.assertEqual(r["work_items"], [])

    def test_t13_work_items_inflate_projects(self) -> None:
        r = work_items_extract.extract(["PROJ-034/T-008 only"])
        self.assertIn("PROJ-034", r["projects"])

    def test_t14_url_strings_still_match(self) -> None:
        r = work_items_extract.extract([
            "https://github.com/x/y/blob/main/planning/PROJ-034/T-008.md",
        ])
        self.assertEqual(r["work_items"], ["PROJ-034/T-008"])

    def test_t15_tool_input_text(self) -> None:
        # Tool input is just text in the corpus from the caller's POV.
        r = work_items_extract.extract([
            '{"command": "git log --grep PROJ-029/T-011"}',
        ])
        self.assertEqual(r["work_items"], ["PROJ-029/T-011"])


if __name__ == "__main__":
    unittest.main()
