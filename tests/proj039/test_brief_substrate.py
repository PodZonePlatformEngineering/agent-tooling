"""Tests for lib/brief_substrate — the PROJ-039/T-043 `briefs` collection ops.

The same load-bearing property as session_substrate: creation is the ONE full
upsert (sets the `brief` named vector); every later write — append_session_id,
set_status, start_brief, complete_brief — must use set_payload (POST …/points/
payload), NEVER a full upsert, so the named vector survives (F-2-008 / SD-3-001).
These tests intercept qdrant_http.request_json and record (method, path-suffix)
so the discipline is asserted directly, mirroring test_session_substrate.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import brief_substrate, qdrant_http, session_substrate  # noqa: E402


class RecordingQdrant:
    """Fake qdrant_http.request_json: records calls, serves one point's payload."""

    def __init__(self, initial: dict | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.payload: dict = dict(initial or {})
        self.exists = initial is not None

    def __call__(self, method, url, *, payload=None, api_key=None, timeout=None):
        suffix = url.split("/collections/", 1)[-1]
        self.calls.append((method.upper(), suffix))
        if method.upper() == "PUT" and suffix.endswith("/points"):
            pts = payload["points"]
            self.payload = dict(pts[0].get("payload", {}))
            self.exists = True
            return {"result": {"status": "acknowledged"}}
        if method.upper() == "POST" and suffix.endswith("/points/payload"):
            self.payload.update(payload["payload"])
            return {"result": {"status": "acknowledged"}}
        if method.upper() == "PUT" and suffix.endswith("/points/vectors"):
            return {"result": {"status": "acknowledged"}}
        if method.upper() == "GET":
            if not self.exists:
                from lib.qdrant_http import QdrantHTTPError
                raise QdrantHTTPError(404, url, "not found")
            return {"result": {"payload": dict(self.payload)}}
        return {"result": {"status": "ok"}}

    def full_upserts(self):
        return [c for c in self.calls if c[0] == "PUT" and c[1].endswith("/points")]

    def set_payloads(self):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith("/points/payload")]


BRIEF_ID = "training/2026-07-02-prompt-basics-alex"


class TestPointId(unittest.TestCase):
    def test_deterministic_uuid5(self) -> None:
        import uuid
        self.assertEqual(
            brief_substrate.point_id_for(BRIEF_ID),
            str(uuid.uuid5(uuid.NAMESPACE_DNS, BRIEF_ID)),
        )

    def test_convergent(self) -> None:
        self.assertEqual(
            brief_substrate.point_id_for(BRIEF_ID),
            brief_substrate.point_id_for(BRIEF_ID),
        )


class TestCreateBrief(unittest.TestCase):
    def test_creation_is_one_full_upsert_with_brief_vector(self) -> None:
        rec = RecordingQdrant()
        with patch.object(qdrant_http, "request_json", rec), \
             patch.object(session_substrate, "embed_text", lambda *a, **k: [0.1] * 768):
            res = brief_substrate.create_brief(
                brief_id=BRIEF_ID, team="training", author="athena",
                assignee="alex", assignee_type="trainee", body="Learn prompt basics.",
                status="approved", work_items=["PROJ-050/T-001"],
            )
        self.assertTrue(res["ok"])
        self.assertEqual(len(rec.full_upserts()), 1)  # the ONE creation upsert
        self.assertEqual(rec.payload["point_type"], "brief")
        self.assertEqual(rec.payload["brief_id"], BRIEF_ID)
        self.assertEqual(rec.payload["assignee_type"], "trainee")
        self.assertEqual(rec.payload["status"], "approved")
        self.assertEqual(rec.payload["session_ids"], [])
        self.assertIsNone(rec.payload["completed_at"])
        self.assertEqual(rec.payload["body"], "Learn prompt basics.")

    def test_tooling_update_field_default_none_and_settable(self) -> None:
        rec = RecordingQdrant()
        with patch.object(qdrant_http, "request_json", rec), \
             patch.object(session_substrate, "embed_text", lambda *a, **k: [0.1] * 768):
            brief_substrate.create_brief(
                brief_id=BRIEF_ID, team="training", author="athena",
                assignee="alex", assignee_type="trainee", body="Learn prompt basics.",
                status="approved",
            )
        self.assertIsNone(rec.payload["tooling_update"])

        rec2 = RecordingQdrant()
        with patch.object(qdrant_http, "request_json", rec2), \
             patch.object(session_substrate, "embed_text", lambda *a, **k: [0.1] * 768):
            brief_substrate.create_brief(
                brief_id=BRIEF_ID, team="proj039", author="hermes",
                assignee="hephaestus", body="Self-update to v1.1.0.",
                status="approved", tooling_update="v1.1.0",
            )
        self.assertEqual(rec2.payload["tooling_update"], "v1.1.0")

    def test_rejects_bad_status_and_type_and_empty_body(self) -> None:
        with patch.object(session_substrate, "embed_text", lambda *a, **k: [0.1] * 768):
            with self.assertRaises(ValueError):
                brief_substrate.create_brief(brief_id=BRIEF_ID, team="t", author="a",
                                             assignee="x", body="b", status="bogus")
            with self.assertRaises(ValueError):
                brief_substrate.create_brief(brief_id=BRIEF_ID, team="t", author="a",
                                             assignee="x", body="b", assignee_type="robot")
            with self.assertRaises(ValueError):
                brief_substrate.create_brief(brief_id=BRIEF_ID, team="t", author="a",
                                             assignee="x", body="   ")


class TestAppendSessionId(unittest.TestCase):
    def test_append_uses_set_payload_never_full_upsert(self) -> None:
        rec = RecordingQdrant({"brief_id": BRIEF_ID, "session_ids": []})
        with patch.object(qdrant_http, "request_json", rec):
            r1 = brief_substrate.append_session_id(BRIEF_ID, "sid-1")
        self.assertTrue(r1["appended"])
        self.assertEqual(r1["session_ids"], ["sid-1"])
        self.assertEqual(len(rec.full_upserts()), 0)  # discipline: no full upsert
        self.assertEqual(len(rec.set_payloads()), 1)

    def test_two_distinct_sessions_accumulate(self) -> None:
        rec = RecordingQdrant({"brief_id": BRIEF_ID, "session_ids": []})
        with patch.object(qdrant_http, "request_json", rec):
            brief_substrate.append_session_id(BRIEF_ID, "sid-1")
            r2 = brief_substrate.append_session_id(BRIEF_ID, "sid-2")
        self.assertEqual(r2["session_ids"], ["sid-1", "sid-2"])  # many-sessions-per-brief

    def test_idempotent_on_same_sid(self) -> None:
        rec = RecordingQdrant({"brief_id": BRIEF_ID, "session_ids": []})
        with patch.object(qdrant_http, "request_json", rec):
            brief_substrate.append_session_id(BRIEF_ID, "sid-1")
            r2 = brief_substrate.append_session_id(BRIEF_ID, "sid-1")  # resume
        self.assertFalse(r2["appended"])
        self.assertEqual(r2["session_ids"], ["sid-1"])
        self.assertEqual(len(rec.set_payloads()), 1)  # only the first wrote

    def test_placeholder_sid_refused_never_writes(self) -> None:
        # T-047 (CC-348): the live T-045 point gained a literal 's' when a
        # non-hermetic hook test's dummy `{"session_id": "s"}` reached the brief-
        # first path (ambient BRIEF_ID). append_session_id must refuse it loudly and
        # write nothing — belt-and-braces so the class is structurally impossible.
        rec = RecordingQdrant({"brief_id": BRIEF_ID, "session_ids": []})
        with patch.object(qdrant_http, "request_json", rec):
            with self.assertRaises(ValueError):
                brief_substrate.append_session_id(BRIEF_ID, "s")
        self.assertEqual(len(rec.set_payloads()), 0)  # no live write

    def test_conventional_synthetic_sid_still_accepted(self) -> None:
        # The guard rejects only implausibly-short placeholders; conventional
        # synthetic sids and real UUIDs pass unchanged.
        rec = RecordingQdrant({"brief_id": BRIEF_ID, "session_ids": []})
        with patch.object(qdrant_http, "request_json", rec):
            r = brief_substrate.append_session_id(
                BRIEF_ID, "0c7908ea-f18f-43be-bb58-88464ae698e0")
        self.assertTrue(r["appended"])


class TestStartBrief(unittest.TestCase):
    def test_approved_advances_to_in_progress(self) -> None:
        rec = RecordingQdrant({"brief_id": BRIEF_ID, "status": "approved"})
        with patch.object(qdrant_http, "request_json", rec):
            r = brief_substrate.start_brief(BRIEF_ID)
        self.assertTrue(r["changed"])
        self.assertEqual(r["status"], "in_progress")
        self.assertEqual(len(rec.full_upserts()), 0)

    def test_in_progress_is_noop(self) -> None:
        rec = RecordingQdrant({"brief_id": BRIEF_ID, "status": "in_progress"})
        with patch.object(qdrant_http, "request_json", rec):
            r = brief_substrate.start_brief(BRIEF_ID)
        self.assertFalse(r["changed"])
        self.assertEqual(len(rec.set_payloads()), 0)

    def test_draft_is_refused_execution_gate(self) -> None:
        rec = RecordingQdrant({"brief_id": BRIEF_ID, "status": "draft"})
        with patch.object(qdrant_http, "request_json", rec):
            with self.assertRaises(ValueError):
                brief_substrate.start_brief(BRIEF_ID)


class TestCompleteBrief(unittest.TestCase):
    def test_complete_sets_status_and_timestamp_via_set_payload(self) -> None:
        rec = RecordingQdrant({"brief_id": BRIEF_ID, "status": "in_progress"})
        with patch.object(qdrant_http, "request_json", rec):
            r = brief_substrate.complete_brief(BRIEF_ID, completed_at="2026-07-02T00:00:00+00:00")
        self.assertEqual(r["status"], "complete")
        self.assertEqual(rec.payload["status"], "complete")
        self.assertEqual(rec.payload["completed_at"], "2026-07-02T00:00:00+00:00")
        self.assertEqual(len(rec.full_upserts()), 0)  # never a full upsert


if __name__ == "__main__":
    unittest.main()
