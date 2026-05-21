"""Tests for lib/sessions_upsert."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import sessions_upsert  # noqa: E402


def _iso_ago(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _mock_response(status_code: int = 200, json_body: dict | None = None) -> MagicMock:
    m = MagicMock()
    m.status_code = status_code
    m.raise_for_status = MagicMock()
    m.json = MagicMock(return_value=json_body or {})
    if status_code >= 400:
        m.raise_for_status.side_effect = RuntimeError(f"http {status_code}")
    return m


class TestUpsertSession(unittest.TestCase):

    def setUp(self) -> None:
        # Ensure API key is set so we exercise the HTTP path
        self.env_patch = patch.dict(
            "os.environ", {"PODZONE_QDRANT_APIKEY": "test-key"}, clear=False
        )
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()

    # T1: payload + data_source=backfill → POST to right URL with right point id
    def test_t1_basic_upsert(self) -> None:
        with patch("lib.sessions_upsert.requests") as rq:
            rq.put.return_value = _mock_response(200)
            payload = {
                "session_id": "abc-123",
                "jsonl_mtime": _iso_ago(60),
            }
            result = sessions_upsert.upsert_session(payload, data_source="backfill")
            self.assertTrue(result["ok"])
            self.assertEqual(result["point_id"], sessions_upsert.point_id_for("abc-123"))
            call = rq.put.call_args
            self.assertIn(
                f"{sessions_upsert.SESSIONS_COLLECTION}/points",
                call.args[0],
            )
            sent_payload = call.kwargs["json"]["points"][0]["payload"]
            self.assertEqual(sent_payload["data_source"], "backfill")
            self.assertIn("updated_at", sent_payload)
            self.assertIn("status", sent_payload)

    # T2: status=None + jsonl_mtime 5 min ago → derived in_progress
    def test_t2_derive_in_progress(self) -> None:
        status = sessions_upsert.derive_status(_iso_ago(5 * 60))
        self.assertEqual(status, "in_progress")

    # T3: status=None + jsonl_mtime 1 h ago → derived idle
    def test_t3_derive_idle(self) -> None:
        status = sessions_upsert.derive_status(_iso_ago(60 * 60))
        self.assertEqual(status, "idle")

    # T4: status=None + jsonl_mtime 10 h ago → derived ended
    def test_t4_derive_ended(self) -> None:
        status = sessions_upsert.derive_status(_iso_ago(10 * 3600))
        self.assertEqual(status, "ended")

    # T5: missing PODZONE_QDRANT_APIKEY → log and return gracefully (no raise)
    def test_t5_missing_api_key(self) -> None:
        with patch.dict("os.environ", {"PODZONE_QDRANT_APIKEY": ""}, clear=False):
            result = sessions_upsert.upsert_session(
                {"session_id": "abc-123", "jsonl_mtime": _iso_ago(60)},
                data_source="backfill",
            )
            self.assertFalse(result["ok"])
            self.assertIn("PODZONE_QDRANT_APIKEY", result["reason"])

    # T6: Qdrant returns 5xx → log and return gracefully (no raise)
    def test_t6_qdrant_5xx(self) -> None:
        with patch("lib.sessions_upsert.requests") as rq:
            rq.put.return_value = _mock_response(500)
            result = sessions_upsert.upsert_session(
                {"session_id": "abc-123", "jsonl_mtime": _iso_ago(60)},
                data_source="backfill",
            )
            self.assertFalse(result["ok"])
            self.assertIn("upsert failed", result["reason"])

    def test_explicit_status_overrides_derivation(self) -> None:
        with patch("lib.sessions_upsert.requests") as rq:
            rq.put.return_value = _mock_response(200)
            result = sessions_upsert.upsert_session(
                {"session_id": "abc-123", "jsonl_mtime": _iso_ago(10 * 3600)},
                data_source="stop_hook",
                status="in_progress",
            )
            self.assertTrue(result["ok"])
            sent = rq.put.call_args.kwargs["json"]["points"][0]["payload"]
            self.assertEqual(sent["status"], "in_progress")

    def test_last_heartbeat_ts_override(self) -> None:
        with patch("lib.sessions_upsert.requests") as rq:
            rq.put.return_value = _mock_response(200)
            hb = _iso_ago(0)
            sessions_upsert.upsert_session(
                {"session_id": "abc-123", "jsonl_mtime": _iso_ago(60)},
                data_source="stop_hook",
                last_heartbeat_ts=hb,
            )
            sent = rq.put.call_args.kwargs["json"]["points"][0]["payload"]
            self.assertEqual(sent["last_heartbeat_ts"], hb)

    def test_invalid_data_source(self) -> None:
        result = sessions_upsert.upsert_session(
            {"session_id": "abc-123"}, data_source="bogus"
        )
        self.assertFalse(result["ok"])
        self.assertIn("invalid data_source", result["reason"])

    def test_missing_session_id(self) -> None:
        result = sessions_upsert.upsert_session({}, data_source="backfill")
        self.assertFalse(result["ok"])
        self.assertIn("missing session_id", result["reason"])

    def test_point_id_deterministic(self) -> None:
        self.assertEqual(
            sessions_upsert.point_id_for("abc-123"),
            sessions_upsert.point_id_for("abc-123"),
        )
        self.assertNotEqual(
            sessions_upsert.point_id_for("abc-123"),
            sessions_upsert.point_id_for("abc-124"),
        )


class TestShouldSkipBackfill(unittest.TestCase):

    def test_skip_when_session_end_skill_newer_or_equal(self) -> None:
        with patch("lib.sessions_upsert._get_existing") as ge:
            ge.return_value = {
                "data_source": "session_end_skill",
                "jsonl_mtime": "2026-05-21T10:00:00+00:00",
            }
            self.assertTrue(
                sessions_upsert.should_skip_backfill("s", "2026-05-21T09:00:00+00:00")
            )
            self.assertTrue(
                sessions_upsert.should_skip_backfill("s", "2026-05-21T10:00:00+00:00")
            )

    def test_no_skip_when_backfill_is_newer(self) -> None:
        with patch("lib.sessions_upsert._get_existing") as ge:
            ge.return_value = {
                "data_source": "session_end_skill",
                "jsonl_mtime": "2026-05-21T10:00:00+00:00",
            }
            self.assertFalse(
                sessions_upsert.should_skip_backfill("s", "2026-05-21T11:00:00+00:00")
            )

    def test_no_skip_when_existing_is_stop_hook(self) -> None:
        with patch("lib.sessions_upsert._get_existing") as ge:
            ge.return_value = {
                "data_source": "stop_hook",
                "jsonl_mtime": "2026-05-21T10:00:00+00:00",
            }
            self.assertFalse(
                sessions_upsert.should_skip_backfill("s", "2026-05-21T09:00:00+00:00")
            )

    def test_no_skip_when_no_existing(self) -> None:
        with patch("lib.sessions_upsert._get_existing") as ge:
            ge.return_value = None
            self.assertFalse(
                sessions_upsert.should_skip_backfill("s", "2026-05-21T10:00:00+00:00")
            )


if __name__ == "__main__":
    unittest.main()
