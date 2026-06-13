"""DT-001 semantic-recall harness — the offline halves (PROJ-039 AC-002 / MVP-11).

Three properties are enforceable without credentials and guard the live run:

  1. **Fixture validity** — exactly 10 labelled pairs, each with a non-trivial
     brief and query, unique slugs.
  2. **Grep baseline = 0** — no query phrase appears verbatim (case-insensitive)
     anywhere in the brief corpus. This is the half of DT-001 that makes the
     live ≥ 8/10 result *mean* something: retrieval cannot be lexical.
  3. **Scoring logic** — top3_recall counts hits/threshold correctly, so the
     number the live harness records is produced by tested code.

The live half (seed → Ollama embed → search using="brief" → cleanup) runs via
``tools/proj039-live-checks.py dt001`` under ``mcp__secrets__secret_run``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import recall_harness  # noqa: E402


class TestFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.pairs = recall_harness.load_pairs()

    def test_exactly_ten_labelled_pairs(self) -> None:
        self.assertEqual(len(self.pairs), 10)

    def test_pairs_are_well_formed_and_unique(self) -> None:
        slugs = [p["slug"] for p in self.pairs]
        self.assertEqual(len(set(slugs)), 10)
        for p in self.pairs:
            # Non-trivial text on both sides: a one-word brief or query would
            # make the recall claim meaningless.
            self.assertGreater(len(p["brief"].split()), 15, p["slug"])
            self.assertGreater(len(p["query"].split()), 4, p["slug"])

    def test_grep_baseline_is_zero_for_every_query(self) -> None:
        # The load-bearing offline half of DT-001: the win must be semantic.
        baseline = recall_harness.grep_baseline(self.pairs)
        self.assertEqual(
            {slug: n for slug, n in baseline.items() if n != 0},
            {},
            "query phrase found verbatim in the brief corpus — fixture invalid",
        )

    def test_point_ids_are_deterministic_and_distinct(self) -> None:
        ids = {recall_harness.point_id_for_pair(p["slug"]) for p in self.pairs}
        self.assertEqual(len(ids), 10)
        self.assertEqual(
            recall_harness.point_id_for_pair("tls-rotation"),
            recall_harness.point_id_for_pair("tls-rotation"),
        )

    def test_seed_points_carry_marker_and_brief_vector(self) -> None:
        vectors = {p["slug"]: [0.1] * 4 for p in self.pairs}
        points = recall_harness.seed_points(self.pairs, vectors)
        self.assertEqual(len(points), 10)
        for pt in points:
            self.assertEqual(pt["payload"]["point_type"], "brief")
            self.assertEqual(pt["payload"]["test_marker"], recall_harness.TEST_MARKER)
            self.assertIn("brief", pt["vector"])


class TestScoring(unittest.TestCase):
    def _results(self, hit_count: int) -> dict:
        # First `hit_count` queries find their target at rank 2; the rest miss.
        slugs = [f"s{i}" for i in range(10)]
        return {
            s: (["other", s, "x"] if i < hit_count else ["a", "b", "c"])
            for i, s in enumerate(slugs)
        }

    def test_eight_of_ten_passes(self) -> None:
        score = recall_harness.top3_recall(self._results(8))
        self.assertEqual(score["hits"], 8)
        self.assertTrue(score["passed"])

    def test_seven_of_ten_fails(self) -> None:
        score = recall_harness.top3_recall(self._results(7))
        self.assertEqual(score["hits"], 7)
        self.assertFalse(score["passed"])

    def test_rank_four_is_not_a_hit(self) -> None:
        score = recall_harness.top3_recall({"q": ["a", "b", "c", "q"]})
        self.assertEqual(score["hits"], 0)

    def test_partial_corpus_cannot_pass(self) -> None:
        # 8 hits from only 8 queries must not pass — the contract is 8 of 10.
        results = {f"s{i}": [f"s{i}"] for i in range(8)}
        self.assertFalse(recall_harness.top3_recall(results)["passed"])


if __name__ == "__main__":
    unittest.main()
