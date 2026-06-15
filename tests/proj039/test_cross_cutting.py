"""Tests for lib/cross_cutting — the AC-008 / DT-012 cross-cutting query.

DT-012 pass criterion: a single query returns brief + activity for a session by
joining `session_substrate` (brief/response) and `claude_session_telemetry` (CST)
**from one Qdrant instance** — no cross-instance app-join (F-2-007 cleared by
PROJ-033/T-019). These tests stub the qdrant_http read layer so the join logic,
the brief-vs-activity rendering, and the single-instance assertion are checked
deterministically (the live run is captured in the session outbox).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import cross_cutting, qdrant_http, session_substrate  # noqa: E402

SESSION_ID = "45474746-5047-4475-b776-d100b5aa4d8d"

# qdrant_http.get_point(with_vector=False) returns the payload dict *directly*.
SESSION_POINT = {
    "point_type": "session",
    "session_id": SESSION_ID,
    "agent": "hephaestus",
    "work_item": "PROJ-035/T-003",
    "brief": {
        "text": "Build the consolidate-tasks reaper for stale STATUS items.",
        "dispatch_ts": "2026-06-10T09:00:00+00:00",
    },
    "response": {
        "text": "Implemented the reaper; 4 stale items removed; tests green.",
        "status_transition": "ready→complete",
        "end_ts": "2026-06-10T12:30:00+00:00",
    },
    "rollup": {"tool_usage": {"Bash": 5}},
}

CST_EVENTS = {
    "result": {
        "points": [
            {"payload": {"event_type": "PreToolUse", "tool_name": "Read",
                         "timestamp": "2026-06-10T09:05:00+00:00", "repository": "agent-tooling"}},
            {"payload": {"event_type": "PreToolUse", "tool_name": "Edit",
                         "timestamp": "2026-06-10T09:10:00+00:00", "repository": "agent-tooling"}},
            {"payload": {"event_type": "PostToolUse", "tool_name": "Read",
                         "timestamp": "2026-06-10T09:06:00+00:00"}},
            {"payload": {"event_type": "Stop", "timestamp": "2026-06-10T12:29:00+00:00"}},
        ]
    }
}


def _fake_get_point(point_id, *, collection, qdrant_url=None, api_key=None, **kw):
    if collection == session_substrate.COLLECTION and point_id == session_substrate.point_id_for(SESSION_ID):
        return SESSION_POINT
    return None


def _fake_scroll(*, collection, body=None, qdrant_url=None, api_key=None, **kw):
    if collection == cross_cutting.CST_COLLECTION:
        return CST_EVENTS
    return {"result": {"points": []}}


class CrossCuttingTests(unittest.TestCase):
    def _run(self):
        with patch.object(qdrant_http, "get_point", _fake_get_point), \
             patch.object(qdrant_http, "scroll", _fake_scroll):
            return cross_cutting.cross_cutting_query(SESSION_ID, api_key="x")

    def test_brief_side_parsed(self):
        r = self._run()
        self.assertTrue(r.found)
        self.assertEqual(r.agent, "hephaestus")
        self.assertEqual(r.work_item, "PROJ-035/T-003")
        self.assertIn("reaper", r.brief_text)
        self.assertEqual(r.response_text, "Implemented the reaper; 4 stale items removed; tests green.")
        self.assertEqual(r.status_transition, "ready→complete")

    def test_activity_side_aggregated(self):
        r = self._run()
        self.assertEqual(r.cst_event_count, 4)
        self.assertEqual(r.event_type_counts, {"PreToolUse": 2, "PostToolUse": 1, "Stop": 1})
        # tool_name only tallied for tool events
        self.assertEqual(r.tool_use_counts, {"Read": 2, "Edit": 1})
        self.assertEqual(r.repositories, ["agent-tooling"])
        self.assertEqual(r.activity_first_ts, "2026-06-10T09:05:00+00:00")
        self.assertEqual(r.activity_last_ts, "2026-06-10T12:29:00+00:00")

    def test_single_instance_property(self):
        """The load-bearing DT-012 assertion: the whole join hit ONE instance."""
        r = self._run()
        self.assertEqual(len(r.instances_touched), 1)
        self.assertTrue(r.single_instance)

    def test_render_has_both_sides(self):
        out = self._run().render()
        self.assertIn("WHAT THE BRIEF ASKED", out)
        self.assertIn("WHAT THE AGENT DID", out)
        self.assertIn("WHAT THE AGENT REPORTED", out)
        self.assertIn("single-instance: True", out)

    def test_missing_session_point(self):
        with patch.object(qdrant_http, "get_point", lambda *a, **k: None), \
             patch.object(qdrant_http, "scroll", _fake_scroll):
            r = cross_cutting.cross_cutting_query("no-such-session", api_key="x")
        self.assertFalse(r.found)
        self.assertEqual(r.cst_event_count, 4)  # CST stub still answers
        self.assertTrue(r.single_instance)

    def test_no_cst_activity(self):
        with patch.object(qdrant_http, "get_point", _fake_get_point), \
             patch.object(qdrant_http, "scroll", lambda **k: {"result": {"points": []}}):
            r = cross_cutting.cross_cutting_query(SESSION_ID, api_key="x")
        self.assertTrue(r.found)
        self.assertEqual(r.cst_event_count, 0)
        self.assertEqual(r.tool_use_counts, {})


if __name__ == "__main__":
    unittest.main()
