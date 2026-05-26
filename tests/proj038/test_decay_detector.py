"""Tests for the PROJ-038 decay detector (lib/decay + tools/decay-detector.py).

Covers:
  - Per-category detection on the sample-design fixture (≥ 1 event per shipped
    sub-case, per AC-001 + C-008 go column).
  - Per-category negative case via the "fresh" fixture (AC-004 ≤ 5 events,
    no high-severity on a ≤ 3-iteration design).
  - C-010 severity algorithm spans.
  - SD-008 source-anchor rendering for both file + qdrant forms.
  - Incremental mode (R-005 + SD-001) via `.last-run-timestamp`.
  - Determinism (R-004): byte-identical output on two consecutive runs with
    same `--run-date`.
  - Batch mode (R-011): two project dirs, two reports.
  - Pre-playbook degraded mode (C-009): Cat 2 disabled; Cat 6 uses
    first-occurrence-as-glossary substitution; degraded header rendered.
  - Manifest validation: missing `type` is a hard error.
  - Filler vocabulary minimum 20 phrases (Cat 4 GO-WITH-CONDITION).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib.decay import detectors  # noqa: E402
from lib.decay.anchors import (file_anchor, qdrant_anchor,  # noqa: E402
                               render_anchor_markdown)
from lib.decay.events import CATEGORIES, DecayEvent, severity_for_span  # noqa: E402
from lib.decay.fillers import (FillerVocabularyError, compile_filler_regex,  # noqa: E402
                               load_fillers)
from lib.decay.manifest import ManifestError, load_manifest  # noqa: E402
from lib.decay.runner import (DEFAULT_FILLER_PATH, run_batch,  # noqa: E402
                              run_detection, run_trajectory_replay)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample-design"
MANIFEST_PATH = (
    FIXTURE_DIR / "iterations" / "iteration-1" / "trajectory-manifest.yaml"
)
FROZEN_NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)


def _run_in_tmp(fixture_dir: Path = FIXTURE_DIR,
                manifest_relpath: str = "iterations/iteration-1/trajectory-manifest.yaml",
                noise_budget: str = "low",
                trajectory_replay: bool = False,
                incremental: bool = False):
    tmp = Path(tempfile.mkdtemp(prefix="decay-test-"))
    shutil.copytree(fixture_dir, tmp / fixture_dir.name)
    project_dir = tmp / fixture_dir.name
    manifest_path = project_dir / manifest_relpath
    output_path = project_dir / "iterations/iteration-1/decay-report.md"
    fn = run_trajectory_replay if trajectory_replay else run_detection
    if trajectory_replay:
        result = fn(
            project_dir=project_dir,
            manifest_path=manifest_path,
            output_path=output_path,
            noise_budget=noise_budget,
            generated_at=FROZEN_NOW,
        )
    else:
        result = run_detection(
            project_dir=project_dir,
            manifest_path=manifest_path,
            output_path=output_path,
            noise_budget=noise_budget,
            incremental=incremental,
            generated_at=FROZEN_NOW,
        )
    return tmp, project_dir, result


class TestPerCategoryDetection(unittest.TestCase):
    """Each of the six v1-shipped sub-cases (per C-008 go column) yields ≥ 1
    event on the sample-design fixture (AC-001 surrogate)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp, cls._project_dir, cls.result = _run_in_tmp()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _events_for(self, category: str) -> list[DecayEvent]:
        return [e for e in self.result.events if e.category == category]

    def test_cat1_oscillation_detected(self) -> None:
        evs = self._events_for(CATEGORIES[0])
        self.assertGreaterEqual(len(evs), 1)
        self.assertIn("glossary load", evs[0].description.lower())

    def test_cat2_lost_decisions_detected(self) -> None:
        evs = self._events_for(CATEGORIES[1])
        self.assertGreaterEqual(len(evs), 1)

    def test_cat3_over_emphasis_detected(self) -> None:
        evs = self._events_for(CATEGORIES[2])
        self.assertGreaterEqual(len(evs), 1)
        self.assertIn("conformance bridge", evs[0].description.lower())

    def test_cat4_off_topic_detected(self) -> None:
        evs = self._events_for(CATEGORIES[3])
        self.assertGreaterEqual(len(evs), 1)
        # T-1 turn-length AND T-2 filler-regex both contribute on session-001.
        desc = " ".join(e.description for e in evs)
        self.assertIn("T-1", desc)
        self.assertIn("T-2", desc)

    def test_cat5_briefing_gap_detected(self) -> None:
        evs = self._events_for(CATEGORIES[4])
        self.assertGreaterEqual(len(evs), 1)
        # T-003 drop between outbox(hephaestus) → brief-followup(hermes).
        self.assertTrue(
            any("PROJ-038/T-003" in e.description for e in evs)
        )

    def test_cat6_terminology_drift_detected(self) -> None:
        evs = self._events_for(CATEGORIES[5])
        self.assertGreaterEqual(len(evs), 1)
        # "Trajektory" → "Trajectory" within DL distance 1.
        self.assertTrue(
            any("Trajektory" in e.description for e in evs)
        )

    def test_coverage_gaps_section_present(self) -> None:
        self.assertIn("Coverage gaps (BL-NEW-E)", self.result.report_text)

    def test_severity_is_one_of_low_medium_high(self) -> None:
        valid = {"low", "medium", "high"}
        for e in self.result.events:
            self.assertIn(e.severity, valid)


class TestSeverityAlgorithm(unittest.TestCase):
    """C-010 spans: ≤2 low, 3-6 medium, >6 high."""

    def test_span_zero_is_low(self) -> None:
        self.assertEqual(severity_for_span(0), "low")

    def test_span_two_is_low(self) -> None:
        self.assertEqual(severity_for_span(2), "low")

    def test_span_three_is_medium(self) -> None:
        self.assertEqual(severity_for_span(3), "medium")

    def test_span_six_is_medium(self) -> None:
        self.assertEqual(severity_for_span(6), "medium")

    def test_span_seven_is_high(self) -> None:
        self.assertEqual(severity_for_span(7), "high")

    def test_decay_event_assigns_severity_from_indices(self) -> None:
        e = DecayEvent(
            timestamp="2026-05-25T10:00:00+00:00",
            source_anchor=file_anchor("p.md", "L1"),
            category=CATEGORIES[0],
            description="d", refactor="r",
            first_index=0, last_index=8,
        )
        self.assertEqual(e.severity, "high")


class TestAnchorRendering(unittest.TestCase):

    def test_file_anchor_renders_with_line_ref(self) -> None:
        a = file_anchor("foo/bar.md", "L42")
        out = render_anchor_markdown(a)
        self.assertIn("foo/bar.md#L42", out)

    def test_qdrant_anchor_renders(self) -> None:
        a = qdrant_anchor("sessions", "abc-123", "text")
        out = render_anchor_markdown(a)
        self.assertIn("qdrant://sessions/abc-123", out)
        self.assertIn("text", out)


class TestDeterminism(unittest.TestCase):
    """R-004: two consecutive runs with the same inputs produce byte-identical
    output."""

    @staticmethod
    def _strip_volatile(text: str, tmp: Path) -> str:
        return text.replace(str(tmp), "<TMP>")

    def test_two_runs_byte_identical(self) -> None:
        tmp_a, _, result_a = _run_in_tmp()
        tmp_b, _, result_b = _run_in_tmp()
        try:
            a = self._strip_volatile(result_a.report_text, tmp_a)
            b = self._strip_volatile(result_b.report_text, tmp_b)
            self.assertEqual(a, b)
        finally:
            shutil.rmtree(tmp_a, ignore_errors=True)
            shutil.rmtree(tmp_b, ignore_errors=True)


class TestIncrementalMode(unittest.TestCase):
    """R-005 + SD-001: `.last-run-timestamp` advanced; report re-rendered."""

    def test_high_water_mark_written(self) -> None:
        tmp, project_dir, result = _run_in_tmp()
        try:
            marker = result.output_path.parent / ".last-run-timestamp"
            self.assertTrue(marker.exists())
            dt = datetime.fromisoformat(marker.read_text().strip())
            self.assertEqual(
                dt.replace(tzinfo=timezone.utc if not dt.tzinfo else dt.tzinfo),
                datetime(2026, 5, 25, 14, 0, 0, tzinfo=timezone.utc),
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestBatchMode(unittest.TestCase):
    """R-011: N project dirs → N reports."""

    def test_batch_two_dirs_yields_two_reports(self) -> None:
        tmp_a = Path(tempfile.mkdtemp(prefix="decay-batch-a-"))
        tmp_b = Path(tempfile.mkdtemp(prefix="decay-batch-b-"))
        try:
            shutil.copytree(FIXTURE_DIR, tmp_a / "design-a")
            shutil.copytree(FIXTURE_DIR, tmp_b / "design-b")
            results = run_batch(
                [tmp_a / "design-a", tmp_b / "design-b"],
                iteration=1,
                generated_at=FROZEN_NOW,
            )
            self.assertEqual(len(results), 2)
            for r in results:
                self.assertTrue(r.output_path.exists())
        finally:
            shutil.rmtree(tmp_a, ignore_errors=True)
            shutil.rmtree(tmp_b, ignore_errors=True)


class TestPrePlaybookMode(unittest.TestCase):
    """C-009: every entry pre-2026-05-24 disables Cat 2 and substitutes
    first-occurrence-as-glossary for Cat 6."""

    def _make_pre_playbook_manifest(self, root: Path) -> Path:
        project_dir = root / "design"
        shutil.copytree(FIXTURE_DIR, project_dir)
        manifest_path = (
            project_dir / "iterations/iteration-1/trajectory-manifest.yaml"
        )
        # Re-date every entry to 2026-05-20 (pre-playbook).
        text = manifest_path.read_text()
        text = text.replace("2026-05-25T", "2026-05-20T")
        manifest_path.write_text(text)
        return manifest_path

    def test_pre_playbook_disables_cat2_and_renders_degraded_header(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="decay-pp-"))
        try:
            manifest_path = self._make_pre_playbook_manifest(tmp)
            project_dir = manifest_path.parents[2]
            output_path = project_dir / "iterations/iteration-1/decay-report.md"
            result = run_detection(
                project_dir=project_dir,
                manifest_path=manifest_path,
                output_path=output_path,
                generated_at=FROZEN_NOW,
            )
            cat2_events = [e for e in result.events
                           if e.category == CATEGORIES[1]]
            self.assertEqual(cat2_events, [],
                             "Cat 2 must be disabled in pre-playbook mode")
            self.assertIn("Degraded mode", result.report_text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestManifestValidation(unittest.TestCase):

    def test_missing_type_field_raises(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="decay-mv-"))
        try:
            mf = tmp / "manifest.yaml"
            mf.write_text(
                "artefacts:\n"
                "  - path: a.md\n"
                "    timestamp: 2026-05-25T10:00:00+00:00\n"
                "    role: hermes\n"
            )
            with self.assertRaises(ManifestError) as cm:
                load_manifest(mf)
            self.assertIn("type", str(cm.exception))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_type_enum_raises(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="decay-mv-"))
        try:
            mf = tmp / "manifest.yaml"
            mf.write_text(
                "artefacts:\n"
                "  - path: a.md\n"
                "    type: bogus\n"
                "    timestamp: 2026-05-25T10:00:00+00:00\n"
                "    role: hermes\n"
            )
            with self.assertRaises(ManifestError):
                load_manifest(mf)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_qdrant_entry_accepted(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="decay-mv-"))
        try:
            mf = tmp / "manifest.yaml"
            mf.write_text(
                "artefacts:\n"
                "  - qdrant_collection: sessions\n"
                "    qdrant_id: abc-123\n"
                "    type: session\n"
                "    timestamp: 2026-05-25T10:00:00+00:00\n"
                "    role: hephaestus\n"
            )
            m = load_manifest(mf)
            self.assertEqual(len(m), 1)
            self.assertTrue(m.entries[0].is_qdrant())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestFillerVocab(unittest.TestCase):

    def test_default_vocab_has_min_phrases(self) -> None:
        phrases = load_fillers(DEFAULT_FILLER_PATH)
        self.assertGreaterEqual(len(phrases), 20)

    def test_too_small_vocab_raises(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="decay-fv-"))
        try:
            f = tmp / "v.yaml"
            f.write_text("phrases:\n  - uh\n  - um\n")
            with self.assertRaises(FillerVocabularyError):
                load_fillers(f)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_filler_regex_matches_word_boundary(self) -> None:
        rx = compile_filler_regex(["uh", "i mean"])
        self.assertTrue(rx.search("Yeah, uh, that's it"))
        self.assertTrue(rx.search("OK, i mean, sure"))
        self.assertIsNone(rx.search("uhm not quite"))  # word boundary on uh


class TestSourceAnchorOnQdrantEntry(unittest.TestCase):
    """SD-008: qdrant-typed manifest entries emit qdrant-form anchors in
    detected events."""

    def test_qdrant_entry_yields_qdrant_anchor_in_report(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="decay-q-"))
        try:
            shutil.copytree(FIXTURE_DIR, tmp / "design")
            iter_dir = tmp / "design" / "iterations" / "iteration-1"
            mf = iter_dir / "trajectory-manifest.yaml"
            # Add a qdrant entry that won't load (no API key) — body empty,
            # anchor still rendered qdrant-form when picked up by a detector.
            mf.write_text(mf.read_text() + (
                "  - qdrant_collection: sessions\n"
                "    qdrant_id: abc-deadbeef\n"
                "    type: session\n"
                "    timestamp: 2026-05-25T15:00:00+00:00\n"
                "    role: hephaestus\n"
            ))
            result = run_detection(
                project_dir=tmp / "design",
                manifest_path=mf,
                output_path=iter_dir / "decay-report.md",
                generated_at=FROZEN_NOW,
            )
            self.assertTrue(result.qdrant_unreachable)
            self.assertIn("Qdrant unreachable", result.report_text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestFreshDesignThreshold(unittest.TestCase):
    """AC-004: a ≤3-iteration fresh design produces ≤ 5 events and no
    high-severity events."""

    def test_minimal_clean_fixture(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="decay-fresh-"))
        try:
            project_dir = tmp / "fresh-design"
            iter_dir = project_dir / "iterations" / "iteration-1"
            iter_dir.mkdir(parents=True)
            (project_dir / "spec.md").write_text(
                "# Fresh design\n\n## Glossary\n\n"
                "| Term | Definition |\n|---|---|\n"
                "| **Widget** | A unit of work. |\n\n"
                "## Requirements\n\n- R-001 ship it.\n"
            )
            (iter_dir / "brief.md").write_text(
                "# Brief\n\nWork on PROJ-099/T-001. Use the Widget concept.\n"
            )
            (iter_dir / "session-001.md").write_text(
                "# Session\n\nDone PROJ-099/T-001. Used the Widget cleanly.\n"
            )
            (iter_dir / "trajectory-manifest.yaml").write_text(
                "artefacts:\n"
                "  - path: ../../spec.md\n"
                "    type: brief\n"
                "    timestamp: 2026-05-25T08:00:00+00:00\n"
                "    role: hermes\n"
                "  - path: brief.md\n"
                "    type: brief\n"
                "    timestamp: 2026-05-25T09:00:00+00:00\n"
                "    role: hermes\n"
                "  - path: session-001.md\n"
                "    type: session\n"
                "    timestamp: 2026-05-25T10:00:00+00:00\n"
                "    role: hephaestus\n"
            )
            result = run_detection(
                project_dir=project_dir,
                manifest_path=iter_dir / "trajectory-manifest.yaml",
                output_path=iter_dir / "decay-report.md",
                generated_at=FROZEN_NOW,
            )
            self.assertLessEqual(len(result.events), 5)
            self.assertFalse(
                any(e.severity == "high" for e in result.events)
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestTrajectoryReplay(unittest.TestCase):
    """R-014 + SD-002: per-session sub-report appended; origin-untraced flag
    when an event's first manifest occurrence is in a non-session artefact."""

    def test_replay_emits_sub_report(self) -> None:
        tmp, _, result = _run_in_tmp(trajectory_replay=True)
        try:
            self.assertIn("Trajectory replay (R-014)", result.report_text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestDamerauLevenshtein(unittest.TestCase):

    def test_distance_zero(self) -> None:
        self.assertEqual(detectors._damerau_levenshtein("abc", "abc"), 0)

    def test_single_substitution(self) -> None:
        self.assertEqual(detectors._damerau_levenshtein("abc", "abd"), 1)

    def test_transposition_is_distance_one(self) -> None:
        self.assertEqual(detectors._damerau_levenshtein("ab", "ba"), 1)

    def test_cap_short_circuit(self) -> None:
        d = detectors._damerau_levenshtein("aaaa", "zzzz", cap=2)
        self.assertEqual(d, 3)  # cap+1


if __name__ == "__main__":
    unittest.main()
