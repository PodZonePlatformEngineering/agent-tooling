"""Tests for lib/session_stash_substrate — the PROJ-039/T-255 `session_stash`
collection ops (design doc t254-session-stash-design.md §2/§3).

Unlike test_session_results_substrate (a session result is written exactly
once), a stash entry is a *replace* on every push at the same deterministic
point id — the load-bearing assertion here is that a second push with
different content overwrites the first rather than appending to it.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import qdrant_http, session_stash_substrate as sss  # noqa: E402


class RecordingQdrant:
    def __init__(self, initial: dict | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.payload: dict = dict(initial or {})
        self.exists = initial is not None
        self.last_vector = None

    def __call__(self, method, url, *, payload=None, api_key=None, timeout=None):
        suffix = url.split("/collections/", 1)[-1]
        self.calls.append((method.upper(), suffix))
        if method.upper() == "PUT" and suffix.endswith("/points"):
            pts = payload["points"]
            self.payload = dict(pts[0].get("payload", {}))
            self.last_vector = pts[0].get("vector")
            self.exists = True
            return {"result": {"status": "acknowledged"}}
        if method.upper() == "POST" and suffix.endswith("/points/payload"):
            if not self.exists:
                from lib.qdrant_http import QdrantHTTPError
                raise QdrantHTTPError(404, url, "not found")
            self.payload.update(payload["payload"])
            return {"result": {"status": "acknowledged"}}
        if method.upper() == "GET":
            if not self.exists:
                from lib.qdrant_http import QdrantHTTPError
                raise QdrantHTTPError(404, url, "not found")
            return {"result": {"payload": dict(self.payload)}}
        return {"result": {"status": "ok"}}

    def full_upserts(self):
        return [c for c in self.calls if c[0] == "PUT" and c[1].endswith("/points")]

    def set_payload_calls(self):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith("/points/payload")]


class TestPointId(unittest.TestCase):
    def test_matches_stash_prefixed_uuid5(self) -> None:
        import uuid
        brief_id = "hephaestus/2026-08-13-t255-session-stash-push"
        pid = sss.point_id_for(brief_id)
        self.assertEqual(
            pid, str(uuid.uuid5(uuid.NAMESPACE_DNS, f"stash/{brief_id}"))
        )

    def test_convergent(self) -> None:
        self.assertEqual(
            sss.point_id_for("team/2026-08-13-foo"),
            sss.point_id_for("team/2026-08-13-foo"),
        )

    def test_distinct_from_bare_brief_id_uuid5(self) -> None:
        # The "stash/" prefix must keep this out of the same uuid5 bucket a
        # brief point's own id (uuid5(NAMESPACE_DNS, brief_id), no prefix)
        # already occupies — otherwise a stash point and its brief point
        # would collide.
        import uuid
        brief_id = "team/2026-08-13-foo"
        self.assertNotEqual(
            sss.point_id_for(brief_id),
            str(uuid.uuid5(uuid.NAMESPACE_DNS, brief_id)),
        )


class TestPush(unittest.TestCase):
    def _push(self, rec, **overrides):
        kwargs = dict(
            brief_id="team/2026-08-13-foo",
            session_id="sid-1",
            agent="hephaestus",
            work_item="PROJ-039/T-255",
            trigger="explicit",
            content="resume here: step 3",
        )
        kwargs.update(overrides)
        with patch.object(qdrant_http, "request_json", rec):
            return sss.push(**kwargs)

    def test_creation_is_one_payload_only_upsert(self) -> None:
        rec = RecordingQdrant()
        res = self._push(rec)
        self.assertTrue(res["ok"])
        self.assertEqual(len(rec.full_upserts()), 1)
        self.assertEqual(rec.last_vector, {})
        self.assertEqual(rec.payload["point_type"], "session_stash")
        self.assertEqual(rec.payload["brief_id"], "team/2026-08-13-foo")
        self.assertEqual(rec.payload["session_id"], "sid-1")
        self.assertEqual(rec.payload["trigger"], "explicit")
        self.assertEqual(rec.payload["content"], "resume here: step 3")
        self.assertEqual(rec.payload["status"], "active")
        self.assertIsNone(rec.payload["consumed_at"])
        self.assertIsNone(rec.payload["consumed_by_session_id"])
        self.assertTrue(rec.payload["pushed_at"])

    def test_second_push_replaces_not_appends(self) -> None:
        rec = RecordingQdrant()
        r1 = self._push(rec, content="first snapshot", session_id="sid-1")
        r2 = self._push(rec, content="second snapshot", session_id="sid-2")
        self.assertEqual(r1["point_id"], r2["point_id"])
        self.assertEqual(len(rec.full_upserts()), 2)  # two writes, one convergent id
        # Only the second push's content/session_id are retrievable — a
        # get_stash call after the second push must never surface the first.
        self.assertEqual(rec.payload["content"], "second snapshot")
        self.assertEqual(rec.payload["session_id"], "sid-2")

    def test_push_after_consumed_reactivates_status(self) -> None:
        rec = RecordingQdrant()
        self._push(rec)
        rec.payload["status"] = "consumed"
        rec.payload["consumed_at"] = "2026-08-13T10:00:00+00:00"
        rec.payload["consumed_by_session_id"] = "sid-1"
        self._push(rec, content="re-armed", session_id="sid-3")
        self.assertEqual(rec.payload["status"], "active")
        self.assertIsNone(rec.payload["consumed_at"])
        self.assertIsNone(rec.payload["consumed_by_session_id"])

    def test_reupsert_same_brief_id_converges_on_same_point(self) -> None:
        rec = RecordingQdrant()
        r1 = self._push(rec, content="v1")
        r2 = self._push(rec, content="v1")
        self.assertEqual(r1["point_id"], r2["point_id"])

    def test_empty_brief_id_rejected(self) -> None:
        rec = RecordingQdrant()
        with self.assertRaises(ValueError):
            self._push(rec, brief_id="")

    def test_empty_content_rejected(self) -> None:
        rec = RecordingQdrant()
        with self.assertRaises(ValueError):
            self._push(rec, content="   ")

    def test_invalid_trigger_rejected(self) -> None:
        rec = RecordingQdrant()
        with self.assertRaises(ValueError):
            self._push(rec, trigger="not-a-real-trigger")


class TestGetStash(unittest.TestCase):
    def test_fetch_returns_payload(self) -> None:
        rec = RecordingQdrant({"brief_id": "team/x", "status": "active"})
        with patch.object(qdrant_http, "request_json", rec):
            payload = sss.get_stash("team/x")
        self.assertEqual(payload["brief_id"], "team/x")

    def test_fetch_missing_returns_none(self) -> None:
        rec = RecordingQdrant(None)
        with patch.object(qdrant_http, "request_json", rec):
            self.assertIsNone(sss.get_stash("team/nope"))


class TestPop(unittest.TestCase):
    def _active_payload(self, **overrides):
        payload = dict(
            point_type="session_stash",
            brief_id="team/2026-08-13-foo",
            session_id="sid-1",
            agent="hephaestus",
            work_item="PROJ-039/T-255",
            trigger="explicit",
            content="resume here: step 3",
            pushed_at="2026-08-13T09:00:00+00:00",
            status="active",
            consumed_at=None,
            consumed_by_session_id=None,
        )
        payload.update(overrides)
        return payload

    def test_no_point_returns_none(self) -> None:
        rec = RecordingQdrant(None)
        with patch.object(qdrant_http, "request_json", rec):
            result = sss.pop("team/2026-08-13-foo", "sid-2")
        self.assertIsNone(result)
        self.assertEqual(rec.set_payload_calls(), [])

    def test_already_consumed_returns_none(self) -> None:
        rec = RecordingQdrant(self._active_payload(status="consumed"))
        with patch.object(qdrant_http, "request_json", rec):
            result = sss.pop("team/2026-08-13-foo", "sid-2")
        self.assertIsNone(result)
        self.assertEqual(rec.set_payload_calls(), [])

    def test_active_entry_returns_original_payload_and_marks_consumed(self) -> None:
        rec = RecordingQdrant(self._active_payload())
        with patch.object(qdrant_http, "request_json", rec):
            result = sss.pop("team/2026-08-13-foo", "sid-2")
        self.assertEqual(result["content"], "resume here: step 3")
        self.assertEqual(result["trigger"], "explicit")
        self.assertEqual(result["pushed_at"], "2026-08-13T09:00:00+00:00")
        self.assertEqual(result["status"], "active")  # returned payload is pre-consume

        self.assertEqual(len(rec.set_payload_calls()), 1)
        self.assertEqual(rec.payload["status"], "consumed")
        self.assertEqual(rec.payload["consumed_by_session_id"], "sid-2")
        self.assertTrue(rec.payload["consumed_at"])

    def test_consume_preserves_other_fields(self) -> None:
        rec = RecordingQdrant(self._active_payload())
        with patch.object(qdrant_http, "request_json", rec):
            sss.pop("team/2026-08-13-foo", "sid-2")
        self.assertEqual(rec.payload["content"], "resume here: step 3")
        self.assertEqual(rec.payload["trigger"], "explicit")
        self.assertEqual(rec.payload["pushed_at"], "2026-08-13T09:00:00+00:00")
        self.assertEqual(rec.payload["agent"], "hephaestus")
        self.assertEqual(rec.payload["work_item"], "PROJ-039/T-255")
        self.assertEqual(rec.payload["brief_id"], "team/2026-08-13-foo")

    def test_second_pop_is_idempotent_noop(self) -> None:
        rec = RecordingQdrant(self._active_payload())
        with patch.object(qdrant_http, "request_json", rec):
            first = sss.pop("team/2026-08-13-foo", "sid-2")
            second = sss.pop("team/2026-08-13-foo", "sid-3")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(rec.set_payload_calls()), 1)
        # the first consumer wins the record
        self.assertEqual(rec.payload["consumed_by_session_id"], "sid-2")

    def test_no_delete_calls_ever(self) -> None:
        rec = RecordingQdrant(self._active_payload())
        with patch.object(qdrant_http, "request_json", rec):
            sss.pop("team/2026-08-13-foo", "sid-2")
        self.assertFalse(
            any(c[1].endswith("/points/delete") for c in rec.calls)
        )


if __name__ == "__main__":
    unittest.main()
