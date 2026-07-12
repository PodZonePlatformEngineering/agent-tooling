"""Tests for lib/training_substrate — the PROJ-011/T-031 training-collection
schema helpers.

Offline: pure construction — deterministic uuid5 point ids (re-author/retry
convergence), payload shapes against collections/training_briefs.yaml, and
the T-002 payload-only discipline (``"vector": {}`` present, never omitted).
"""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import training_substrate as ts  # noqa: E402


class TestBriefIds(unittest.TestCase):
    def test_training_brief_id_has_no_date(self):
        bid = ts.brief_id_for("norma", slug="prompt-engineering")
        self.assertEqual(bid, "training/norma/prompt-engineering")

    def test_operational_brief_id(self):
        self.assertEqual(ts.brief_id_for("norma", channel="operational"),
                         "training/norma/operational")

    def test_training_channel_requires_slug(self):
        with self.assertRaises(ValueError):
            ts.brief_id_for("norma")

    def test_brief_point_id_is_uuid5_convergent(self):
        a = ts.brief_point_id("training/norma/prompt-engineering")
        b = ts.brief_point_id("training/norma/prompt-engineering")
        self.assertEqual(a, b)
        self.assertEqual(
            a, str(uuid.uuid5(uuid.NAMESPACE_DNS,
                              "training/norma/prompt-engineering")))

    def test_message_point_id_retry_idempotent(self):
        args = ("training/norma/operational", "sid-123", 2)
        self.assertEqual(ts.message_point_id(*args), ts.message_point_id(*args))
        self.assertNotEqual(ts.message_point_id(*args),
                            ts.message_point_id("training/norma/operational",
                                                "sid-123", 3))


class TestBriefPoint(unittest.TestCase):
    def test_reauthor_converges_on_same_point_id(self):
        bid = ts.brief_id_for("norma", slug="prompt-engineering")
        rev1 = ts.build_brief_point(brief_id=bid, trainee="norma",
                                    channel="training", body="v1")
        rev2 = ts.build_brief_point(brief_id=bid, trainee="norma",
                                    channel="training", body="v2", revision=2)
        self.assertEqual(rev1["id"], rev2["id"])
        self.assertEqual(rev2["payload"]["revision"], 2)
        self.assertEqual(rev2["payload"]["body"], "v2")

    def test_payload_shape_and_direction(self):
        p = ts.build_brief_point(
            brief_id="training/norma/operational", trainee="norma",
            channel="operational", body="update tooling", author="athena")
        pl = p["payload"]
        self.assertEqual(pl["point_type"], "brief")
        self.assertEqual(pl["direction"], "to_trainee")
        self.assertEqual(pl["status"], "active")
        self.assertEqual(pl["session_ids"], [])
        self.assertEqual(p["vector"], {})  # payload-only default (T-002)

    def test_lifecycle_has_no_complete_state(self):
        self.assertNotIn("complete", ts.BRIEF_STATUSES)
        with self.assertRaises(ValueError):
            ts.build_brief_point(brief_id="training/x/y", trainee="x",
                                 channel="training", body="b",
                                 status="complete")

    def test_invalid_channel_rejected(self):
        with self.assertRaises(ValueError):
            ts.build_brief_point(brief_id="training/x/y", trainee="x",
                                 channel="fleet", body="b")

    def test_trainer_side_vector_passthrough(self):
        vec = {"brief": [0.0] * 768}
        p = ts.build_brief_point(brief_id="training/x/y", trainee="x",
                                 channel="training", body="b", vector=vec)
        self.assertIs(p["vector"], vec)


class TestMessagePoint(unittest.TestCase):
    def test_message_is_payload_only_from_trainee(self):
        p = ts.build_message_point(
            brief_id="training/norma/prompt-engineering", trainee="norma",
            session_id="sid-1", seq=1, message_type="question",
            body="what does X mean?")
        self.assertEqual(p["vector"], {})
        pl = p["payload"]
        self.assertEqual(pl["point_type"], "message")
        self.assertEqual(pl["direction"], "from_trainee")
        self.assertEqual(pl["status"], "open")
        self.assertEqual(pl["sender"], "norma")
        self.assertEqual(pl["recipient"], "trainer")
        self.assertEqual(pl["session_id"], "sid-1")

    def test_ack_requires_revision(self):
        with self.assertRaises(ValueError):
            ts.build_message_point(brief_id="training/x/operational",
                                   trainee="x", session_id="s", seq=1,
                                   message_type="ack", body="approved")
        p = ts.build_message_point(brief_id="training/x/operational",
                                   trainee="x", session_id="s", seq=1,
                                   message_type="ack", body="approved",
                                   channel="operational", ack_of_revision=3)
        self.assertEqual(p["payload"]["ack_of_revision"], 3)

    def test_invalid_message_type_rejected(self):
        with self.assertRaises(ValueError):
            ts.build_message_point(brief_id="b", trainee="x", session_id="s",
                                   seq=1, message_type="chatter", body="hi")


class TestTelemetryPoint(unittest.TestCase):
    def test_wraps_cst_payload_with_trainee(self):
        event = {"event_type": "Stop", "session_id": "sid-9",
                 "timestamp": "2026-07-12T00:00:00+00:00"}
        p = ts.build_telemetry_point(event, trainee="norma", point_id="abc")
        self.assertEqual(p["id"], "abc")
        self.assertEqual(p["vector"], {})
        self.assertEqual(p["payload"]["trainee"], "norma")
        self.assertEqual(p["payload"]["event_type"], "Stop")
        self.assertNotIn("trainee", event)  # input not mutated


class TestConstants(unittest.TestCase):
    def test_registry_not_in_trainee_scope(self):
        self.assertNotIn(ts.TOKEN_REGISTRY, ts.TRAINEE_COLLECTIONS)
        self.assertEqual(set(ts.TRAINEE_COLLECTIONS),
                         {"training_briefs", "training_session_telemetry"})


if __name__ == "__main__":
    unittest.main()
