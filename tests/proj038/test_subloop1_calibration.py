"""Tests for PROJ-038 Iter-2 Strand 1 calibration changes.

Covers:
  - Change A (R-015 dual-path transcript pre-filter): JSONL tool_use stripping
    (Path 1), XML-literal envelope stripping (Path 2), code-fence / URL / path /
    YAML stripping, and the type-scoping (only session / transcript-ref).
  - Change B (Cat 6 guards): stop-word vocab load, min-token-length guard,
    edit-distance / token-length ratio guard.
  - Change C (Cat 3 guards): stop-word phrase filter, SD-2-010 proper-noun
    guard (>= 2 distinct artefact types).
  - C-010 boundary (SD-2-008): span = 6 -> medium, span = 7 -> high. Confirms
    the baseline is already correct (regression guard for the bundled item).
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib.decay import detectors  # noqa: E402
from lib.decay.detectors import DetectionContext  # noqa: E402
from lib.decay.events import severity_for_span  # noqa: E402
from lib.decay.prefilter import (  # noqa: E402
    build_filtered_bodies,
    prefilter_transcript,
)
from lib.decay.stopwords import (  # noqa: E402
    is_stop_word,
    load_stop_words,
    phrase_is_all_stop,
)


@dataclass
class _FakeEntry:
    index: int
    type: str


class _FakeManifest:
    def __init__(self, entries):
        self.entries = entries

    def __iter__(self):
        return iter(self.entries)


# ---------------------------------------------------------------------------
# Change A — R-015 pre-filter


class TestPrefilterPath1Jsonl(unittest.TestCase):
    """Path 1: structured JSONL tool_use / tool_result blocks dropped."""

    def test_tool_use_block_dropped_text_kept(self) -> None:
        import json
        line = json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": "keepthisword"},
                {"type": "tool_use", "name": "Bash",
                 "input": {"command": "dropthissecret"}},
            ]},
        })
        out = prefilter_transcript(line)
        self.assertIn("keepthisword", out)
        self.assertNotIn("dropthissecret", out)

    def test_tool_result_message_dropped(self) -> None:
        import json
        lines = "\n".join([
            json.dumps({"type": "assistant", "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "kept_assistant"}]}}),
            json.dumps({"type": "tool_result", "message": {
                "role": "tool",
                "content": [{"type": "text", "text": "dropped_result"}]}}),
        ])
        out = prefilter_transcript(lines)
        self.assertIn("kept_assistant", out)
        self.assertNotIn("dropped_result", out)


class TestPrefilterPath2Xml(unittest.TestCase):
    """Path 2: XML-literal tool envelopes in assistant text stripped."""

    def test_function_calls_envelope_stripped(self) -> None:
        text = "before <function_calls>SECRETCALL</function_calls> after"
        out = prefilter_transcript(text)
        self.assertNotIn("SECRETCALL", out)
        self.assertIn("before", out)
        self.assertIn("after", out)

    def test_tool_use_tag_stripped(self) -> None:
        text = "x <tool_use>NESTEDTOOL</tool_use> y"
        out = prefilter_transcript(text)
        self.assertNotIn("NESTEDTOOL", out)


class TestPrefilterRegexStrips(unittest.TestCase):

    def test_code_fence_stripped(self) -> None:
        out = prefilter_transcript("keep ```py SECRETCODE ``` keep2")
        self.assertNotIn("SECRETCODE", out)
        self.assertIn("keep2", out)

    def test_url_stripped(self) -> None:
        out = prefilter_transcript("see https://example.com/secretpath here")
        self.assertNotIn("secretpath", out)
        self.assertIn("here", out)

    def test_abs_and_rel_paths_stripped(self) -> None:
        out = prefilter_transcript("at /etc/secretfile and ./rel/secretrel ok")
        self.assertNotIn("secretfile", out)
        self.assertNotIn("secretrel", out)
        self.assertIn("ok", out)

    def test_yaml_block_stripped_but_prose_colon_kept(self) -> None:
        yaml_block = "alpha: 1\nbeta: two\ngamma: three\ndelta: four"
        out = prefilter_transcript(yaml_block)
        self.assertNotIn("gamma", out)
        # A single prose colon line is not a config block.
        prose = "Decision: ship the widget now"
        out2 = prefilter_transcript(prose)
        self.assertIn("widget", out2)


class TestPrefilterTypeScoping(unittest.TestCase):
    """Only session / transcript-ref entries are pre-filtered (SD-2-011)."""

    def test_only_transcript_types_filtered(self) -> None:
        bodies = {
            0: "<function_calls>A</function_calls> sess",
            1: "<function_calls>B</function_calls> brief",
            2: "<function_calls>C</function_calls> tref",
        }
        man = _FakeManifest([
            _FakeEntry(0, "session"),
            _FakeEntry(1, "brief"),
            _FakeEntry(2, "transcript-ref"),
        ])
        filtered = build_filtered_bodies(man, bodies)
        self.assertNotIn("A", filtered[0])      # session filtered
        self.assertIn("B", filtered[1])         # brief untouched
        self.assertNotIn("C", filtered[2])      # transcript-ref filtered


# ---------------------------------------------------------------------------
# Change B + C — stop words


class TestStopWords(unittest.TestCase):

    def setUp(self) -> None:
        self.sw = load_stop_words()

    def test_vocab_size_is_318(self) -> None:
        self.assertEqual(len(self.sw), 318)

    def test_common_word_is_stop(self) -> None:
        self.assertTrue(is_stop_word("The", self.sw))
        self.assertTrue(is_stop_word("of", self.sw))

    def test_domain_term_is_not_stop(self) -> None:
        self.assertFalse(is_stop_word("cluster", self.sw))

    def test_phrase_all_stop(self) -> None:
        self.assertTrue(phrase_is_all_stop("The Of", self.sw))
        self.assertFalse(phrase_is_all_stop("Conformance Bridge", self.sw))

    def test_empty_vocab_is_noop(self) -> None:
        self.assertFalse(phrase_is_all_stop("The Of", frozenset()))
        self.assertFalse(is_stop_word("the", frozenset()))


# ---------------------------------------------------------------------------
# Change B — Cat 6 guards (min-token + ratio) via detect_terminology_drift


def _drift_events(bodies, types, glossary_terms, *, min_token_len=4,
                  ratio_threshold=0.25, stop_words=frozenset()):
    """Run only the Cat 6 detector against in-memory bodies."""
    from lib.decay.manifest import ManifestEntry, Manifest
    from datetime import datetime, timezone
    entries = []
    for i, t in enumerate(types):
        entries.append(ManifestEntry(
            index=i, type=t, timestamp="2026-05-25T10:00:00+00:00",
            timestamp_dt=datetime(2026, 5, 25, 10, tzinfo=timezone.utc),
            role="hephaestus", path=f"a{i}.md"))
    man = Manifest(entries=entries)
    ctx = DetectionContext(
        glossary_terms=glossary_terms, stop_words=stop_words,
        min_token_len=min_token_len, ratio_threshold=ratio_threshold,
        pre_playbook_only=False)
    return detectors.detect_terminology_drift(man, bodies, ctx)


class TestCat6MinTokenGuard(unittest.TestCase):

    def test_short_token_rejected(self) -> None:
        # canonical "Trajectory"; observed "abc" (len 3) must never match, but
        # use a short near-miss of a short-ish canonical to isolate the guard.
        # canonical "node"; observed "nod" (len 3) is DL-1 but below min len 4.
        bodies = {0: "node glossary", 1: "the nod is wrong"}
        evs = _drift_events(bodies, ["brief", "session"], ["node"],
                            min_token_len=4)
        self.assertEqual(evs, [])

    def test_token_at_min_len_allowed(self) -> None:
        # canonical "nodes"; observed "ndoes" (len 5, DL-1 transposition).
        # Not a morphology variant, so it survives all guards and is flagged.
        bodies = {0: "nodes glossary", 1: "the ndoes thing"}
        evs = _drift_events(bodies, ["brief", "session"], ["nodes"],
                            min_token_len=4)
        self.assertTrue(any("ndoes" in e.description for e in evs))


class TestCat6RatioGuard(unittest.TestCase):

    def test_loose_match_rejected_by_ratio(self) -> None:
        # canonical "cluster" (7) vs observed "clust" — DL distance 2 over
        # max-len 7 = 0.285 > 0.25 -> rejected.
        bodies = {0: "cluster glossary", 1: "the clust here"}
        evs = _drift_events(bodies, ["brief", "session"], ["cluster"],
                            ratio_threshold=0.25)
        self.assertEqual(evs, [])

    def test_tight_match_allowed_under_higher_ratio(self) -> None:
        bodies = {0: "cluster glossary", 1: "the clsuter here"}
        # transposition -> DL 1 over 7 = 0.142 < 0.25 -> allowed.
        evs = _drift_events(bodies, ["brief", "session"], ["cluster"],
                            ratio_threshold=0.25)
        self.assertTrue(any("clsuter" in e.description for e in evs))


class TestCat6MorphologyGuard(unittest.TestCase):
    """Plural / inflection / case+separator variants are not drift."""

    def test_plural_variant_rejected(self) -> None:
        bodies = {0: "the cluster here", 1: "many clusters there"}
        evs = _drift_events(bodies, ["brief", "session"], ["cluster"])
        self.assertEqual(evs, [])

    def test_case_separator_variant_rejected(self) -> None:
        bodies = {0: "the SessionStart hook", 1: "run the session-start now"}
        evs = _drift_events(bodies, ["brief", "session"], ["SessionStart"])
        self.assertEqual(evs, [])

    def test_genuine_misspelling_survives(self) -> None:
        # "Trajektory" is a real misspelling of "Trajectory" (differing stem).
        bodies = {0: "the Trajectory map", 1: "see the Trajektory map"}
        evs = _drift_events(bodies, ["brief", "session"], ["Trajectory"])
        self.assertTrue(any("Trajektory" in e.description for e in evs))

    def test_helper_direct(self) -> None:
        self.assertTrue(detectors._is_morphology_variant("tasks", "task"))
        self.assertTrue(detectors._is_morphology_variant("team", "team/"))
        self.assertFalse(
            detectors._is_morphology_variant("trajektory", "trajectory"))
        self.assertFalse(detectors._is_morphology_variant("stale", "state"))


class TestCat6StopWordCanonical(unittest.TestCase):

    def test_stop_word_canonical_dropped(self) -> None:
        # A glossary term that is a stop word ("there") should not seed matches.
        bodies = {0: "there glossary", 1: "their thing"}
        evs = _drift_events(bodies, ["brief", "session"], ["there"],
                            stop_words=load_stop_words())
        self.assertEqual(evs, [])


# ---------------------------------------------------------------------------
# Change C — Cat 3 proper-noun guard (SD-2-010)


def _over_emphasis_events(bodies, types, *, stop_words=frozenset()):
    from lib.decay.manifest import ManifestEntry, Manifest
    from datetime import datetime, timezone
    entries = []
    for i, t in enumerate(types):
        entries.append(ManifestEntry(
            index=i, type=t, timestamp="2026-05-25T10:00:00+00:00",
            timestamp_dt=datetime(2026, 5, 25, 10, tzinfo=timezone.utc),
            role="hephaestus", path=f"a{i}.md"))
    man = Manifest(entries=entries)
    ctx = DetectionContext(stop_words=stop_words, pre_playbook_only=False)
    return detectors.detect_over_emphasis(man, bodies, ctx)


class TestCat3ProperNounGuard(unittest.TestCase):

    def test_single_artefact_type_not_flagged(self) -> None:
        # "Widget Frobnicator" appears only in session-type artefacts -> guard.
        term = "Widget Frobnicator"
        bodies = {i: term for i in range(4)}
        types = ["session", "session", "session", "session"]
        evs = _over_emphasis_events(bodies, types)
        self.assertFalse(any("Widget Frobnicator" in e.description
                             for e in evs))

    def test_two_artefact_types_flagged(self) -> None:
        # Same term spanning session + brief types -> survives the guard and,
        # introduced in a session (reactive) with >= 3 downstream, fires.
        term = "Widget Frobnicator"
        bodies = {0: term, 1: term, 2: term, 3: term}
        types = ["session", "brief", "brief", "brief"]
        evs = _over_emphasis_events(bodies, types)
        self.assertTrue(any("Widget Frobnicator" in e.description
                            for e in evs))


# ---------------------------------------------------------------------------
# C-010 boundary (SD-2-008) — bundled regression guard


class TestC010Boundary(unittest.TestCase):

    def test_span_six_is_medium(self) -> None:
        self.assertEqual(severity_for_span(6), "medium")

    def test_span_seven_is_high(self) -> None:
        self.assertEqual(severity_for_span(7), "high")


if __name__ == "__main__":
    unittest.main()
