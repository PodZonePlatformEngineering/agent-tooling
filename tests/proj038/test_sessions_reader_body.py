"""PROJ-034/T-021 — `sessions_reader.fetch_point_body` + loader drift guard.

Covers:
  - success path: the qdrant artefact path returns a populated body when the
    reader is healthy and the point carries a body-bearing payload field;
  - field resolution: explicit `payload_field`, default field order, the
    no-body sessions-schema case, and 404;
  - regression path: when the reader drifts (no `fetch_point_body`), the loader
    emits exactly ONE warning across many entries and degrades to empty bodies
    rather than crashing or warning per entry.
"""

from __future__ import annotations

import io
import os
import sys
import types
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import lib  # noqa: E402
from lib import sessions_reader  # noqa: E402
from lib.decay.loader import ArtefactLoader  # noqa: E402
from lib.decay.manifest import ManifestEntry  # noqa: E402


def _qdrant_entry(point_id: str = "abc-123",
                  collection: str = "sessions") -> ManifestEntry:
    return ManifestEntry(
        index=0,
        type="session",
        timestamp="2026-05-25T10:00:00+00:00",
        timestamp_dt=datetime(2026, 5, 25, 10, tzinfo=timezone.utc),
        role="hephaestus",
        qdrant_collection=collection,
        qdrant_id=point_id,
    )


def _fake_response(status_code: int, payload: dict | None = None):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = {"result": {"payload": payload or {}}}
    resp.raise_for_status = mock.Mock()
    return resp


class TestFetchPointBody(unittest.TestCase):
    """Unit coverage for the reader function itself (mocked HTTP)."""

    def test_returns_default_body_field(self) -> None:
        resp = _fake_response(200, {"body": "hello transcript"})
        with mock.patch.object(sessions_reader, "requests") as rq:
            rq.get.return_value = resp
            body = sessions_reader.fetch_point_body(
                "sessions", "p1", api_key="k")
        self.assertEqual(body, "hello transcript")

    def test_default_field_resolution_order(self) -> None:
        # No `body`, but `text` present -> picked up next in order.
        resp = _fake_response(200, {"text": "from text field"})
        with mock.patch.object(sessions_reader, "requests") as rq:
            rq.get.return_value = resp
            body = sessions_reader.fetch_point_body(
                "sessions", "p1", api_key="k")
        self.assertEqual(body, "from text field")

    def test_explicit_payload_field(self) -> None:
        resp = _fake_response(200, {"work_items": "do the thing"})
        with mock.patch.object(sessions_reader, "requests") as rq:
            rq.get.return_value = resp
            body = sessions_reader.fetch_point_body(
                "sessions", "p1", api_key="k", payload_field="work_items")
        self.assertEqual(body, "do the thing")

    def test_sessions_metadata_only_payload_yields_empty(self) -> None:
        # The current `sessions` schema: metadata, no transcript body field.
        resp = _fake_response(200, {
            "session_id": "s1",
            "agent": "hephaestus",
            "total_tokens": 1234,
        })
        with mock.patch.object(sessions_reader, "requests") as rq:
            rq.get.return_value = resp
            body = sessions_reader.fetch_point_body(
                "sessions", "p1", api_key="k")
        self.assertEqual(body, "")

    def test_not_found_returns_empty(self) -> None:
        resp = _fake_response(404, None)
        with mock.patch.object(sessions_reader, "requests") as rq:
            rq.get.return_value = resp
            body = sessions_reader.fetch_point_body(
                "sessions", "missing", api_key="k")
        self.assertEqual(body, "")
        resp.raise_for_status.assert_not_called()


class TestLoaderQdrantHealthy(unittest.TestCase):
    """Acceptance: no silent empty-body when the reader is healthy."""

    def test_populated_body_when_reader_healthy(self) -> None:
        loader = ArtefactLoader()
        entry = _qdrant_entry()
        with mock.patch.dict(os.environ, {"PODZONE_QDRANT_APIKEY": "k"}), \
                mock.patch.object(sessions_reader, "fetch_point_body",
                                  return_value="real body") as fetch:
            body = loader.load(entry)
        self.assertEqual(body, "real body")
        fetch.assert_called_once_with("sessions", "abc-123")

    def test_result_cached_across_repeat_loads(self) -> None:
        loader = ArtefactLoader()
        entry = _qdrant_entry()
        with mock.patch.dict(os.environ, {"PODZONE_QDRANT_APIKEY": "k"}), \
                mock.patch.object(sessions_reader, "fetch_point_body",
                                  return_value="real body") as fetch:
            loader.load(entry)
            loader.load(entry)
        fetch.assert_called_once()  # second load served from cache


class TestLoaderApiDrift(unittest.TestCase):
    """Regression: a reader without fetch_point_body warns ONCE, not per entry,
    and degrades gracefully to empty bodies (does not crash)."""

    def test_single_warning_on_missing_method(self) -> None:
        loader = ArtefactLoader()
        entries = [_qdrant_entry(point_id=f"id-{i}") for i in range(5)]

        # A reader stub that lacks `fetch_point_body` entirely.
        drifted = types.SimpleNamespace()
        self.assertFalse(hasattr(drifted, "fetch_point_body"))

        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"PODZONE_QDRANT_APIKEY": "k"}), \
                mock.patch.object(lib, "sessions_reader", drifted), \
                mock.patch.dict(sys.modules,
                                {"lib.sessions_reader": drifted}), \
                redirect_stderr(buf):
            bodies = [loader.load(e) for e in entries]

        self.assertEqual(bodies, [""] * 5)  # graceful: all empty, no crash
        warnings = [ln for ln in buf.getvalue().splitlines()
                    if "API drift" in ln]
        self.assertEqual(len(warnings), 1)  # exactly one, not 5


if __name__ == "__main__":
    unittest.main()
