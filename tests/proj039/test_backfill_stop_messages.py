"""Tests for tools/backfill-stop-messages.py (T-072, CC-382) — the stop-event
derivation and enrich-or-create reconciliation logic. Pure fixture tests: no
Qdrant, no Ollama (the tool's write path is exercised by the supervised live
run; these guard the derivation rules the run depends on)."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location(
    "backfill_stop_messages", REPO_ROOT / "tools" / "backfill-stop-messages.py"
)
bsm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bsm)


def rec(text=None, *, stop_reason=None, sidechain=False, uuid_="u-x",
        ts="2026-07-01T10:00:00.000Z", sid="sess-1"):
    content = ([{"type": "text", "text": text}] if text else [])
    return {
        "type": "assistant", "isSidechain": sidechain, "uuid": uuid_,
        "timestamp": ts, "sessionId": sid,
        "cwd": "/Users/x/workspace/repo-a", "gitBranch": "main",
        "message": {"role": "assistant", "content": content,
                    "stop_reason": stop_reason},
    }


def write_transcript(records, td) -> Path:
    path = Path(td) / "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


class TestDeriveStopEvents(unittest.TestCase):
    def test_end_turn_events_found_sidechain_and_empty_skipped(self):
        records = [
            rec("turn one", stop_reason="end_turn", uuid_="u-1",
                ts="2026-07-01T10:00:00Z"),
            rec("subagent", stop_reason="end_turn", sidechain=True),
            rec(None, stop_reason="end_turn", uuid_="u-empty"),
            rec("turn two", stop_reason="end_turn", uuid_="u-2",
                ts="2026-07-01T10:05:00Z"),
        ]
        with tempfile.TemporaryDirectory() as td:
            out = bsm.derive_stop_events(write_transcript(records, td))
        self.assertEqual([e["uuid"] for e in out["events"]], ["u-1", "u-2"])
        self.assertEqual(out["skipped_sidechain"], 1)
        self.assertEqual(out["skipped_empty"], 1)
        self.assertFalse(any(e["cut"] for e in out["events"]))
        self.assertEqual(out["events"][0]["session_id"], "sess-1")
        self.assertEqual(out["events"][0]["git_branch"], "main")

    def test_transcript_ending_without_end_turn_marks_cut(self):
        records = [
            rec("turn one", stop_reason="end_turn", uuid_="u-1"),
            rec("mid-flight text", stop_reason="tool_use", uuid_="u-cut"),
        ]
        with tempfile.TemporaryDirectory() as td:
            out = bsm.derive_stop_events(write_transcript(records, td))
        self.assertEqual([(e["uuid"], e["cut"]) for e in out["events"]],
                         [("u-1", False), ("u-cut", True)])

    def test_transcript_ending_with_end_turn_adds_no_cut_event(self):
        records = [rec("final", stop_reason="end_turn", uuid_="u-only")]
        with tempfile.TemporaryDirectory() as td:
            out = bsm.derive_stop_events(write_transcript(records, td))
        self.assertEqual([(e["uuid"], e["cut"]) for e in out["events"]],
                         [("u-only", False)])

    def test_include_cut_false_suppresses_trailing_cut_event(self):
        records = [
            rec("turn one", stop_reason="end_turn", uuid_="u-1"),
            rec("mid-flight text", stop_reason="tool_use", uuid_="u-cut"),
        ]
        with tempfile.TemporaryDirectory() as td:
            out = bsm.derive_stop_events(
                write_transcript(records, td), include_cut=False)
        self.assertEqual([(e["uuid"], e["cut"]) for e in out["events"]],
                         [("u-1", False)])

    def test_active_transcript_detection_by_mtime(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            path = write_transcript(
                [rec("x", stop_reason="end_turn")], td)
            now = path.stat().st_mtime
            self.assertTrue(bsm._is_active_transcript(path, now=now + 1))
            self.assertFalse(bsm._is_active_transcript(
                path, now=now + bsm.ACTIVE_TRANSCRIPT_WINDOW_S + 1))
            os.unlink(path)
            self.assertFalse(bsm._is_active_transcript(path, now=now))

    def test_message_capped(self):
        big = "x" * (20 * 1024)
        with tempfile.TemporaryDirectory() as td:
            out = bsm.derive_stop_events(write_transcript(
                [rec(big, stop_reason="end_turn")], td))
        self.assertEqual(len(out["events"][0]["text"]), 16 * 1024)


class TestReconciliation(unittest.TestCase):
    def test_backfill_point_id_deterministic_and_distinct(self):
        a = bsm.backfill_point_id("sess-1", "u-1")
        self.assertEqual(a, bsm.backfill_point_id("sess-1", "u-1"))
        self.assertNotEqual(a, bsm.backfill_point_id("sess-1", "u-2"))

    def test_match_within_tolerance_nearest_and_claiming(self):
        base = bsm._epoch("2026-07-01T10:00:00+00:00")
        index = {"by_session": {"sess-1": [
            (base + 4.0, "pt-far", False),
            (base + 1.0, "pt-near", False),
            (base + 60.0, "pt-out", False),
        ]}, "ids": set()}
        claimed: set = set()
        match = bsm.match_live_point(index, "sess-1", base, claimed)
        self.assertEqual(match, ("pt-near", False))
        claimed.add("pt-near")
        match = bsm.match_live_point(index, "sess-1", base, claimed)
        self.assertEqual(match, ("pt-far", False))
        claimed.add("pt-far")
        self.assertIsNone(bsm.match_live_point(index, "sess-1", base, claimed))
        self.assertIsNone(bsm.match_live_point(index, "sess-9", base, set()))

    def test_created_point_payload_shape(self):
        payload = bsm._created_point({
            "uuid": "u-9", "timestamp": "2026-07-01T10:00:00Z",
            "session_id": "sess-9", "cwd": "/Users/x/workspace/repo-b",
            "git_branch": "feat/x", "text": "the message", "cut": True,
        })
        self.assertEqual(payload["event_type"], "Stop")
        self.assertEqual(payload["data_source"], "stop_backfill")
        self.assertEqual(payload["repository"],
                         {"name": "repo-b", "git_branch": "feat/x",
                          "commit_sha": "unknown"})
        self.assertEqual(payload["last_assistant_message"], "the message")
        self.assertEqual(payload["message_source"], "cut")
        self.assertTrue(payload["transcript_cut"])
        self.assertEqual(payload["turn_uuid"], "u-9")


if __name__ == "__main__":
    unittest.main()
