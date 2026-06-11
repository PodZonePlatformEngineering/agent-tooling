"""Tests for lib/session_substrate — the PROJ-039 canonical `session` point ops.

The load-bearing property is upsert discipline (SD-3-001 / OT-006): every write
after creation must use set_payload (POST …/points/payload) or update-vectors
(PUT …/points/vectors), NEVER a full upsert (PUT …/points), because a full
upsert nulls the `brief`/`response` named vectors. These tests intercept the
qdrant_http HTTP layer (``request_json``) and record every (method, path) so the
discipline can be asserted directly — this is the unit-level realisation of
OT-006, DT-004b and DT-005.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import qdrant_http, session_substrate, sessions_upsert  # noqa: E402


class RecordingQdrant:
    """A fake qdrant_http.request_json that records calls and serves point state.

    Holds one point's payload so append (read-modify-write) round-trips. Returns
    the canonical Qdrant envelope shapes the lib expects.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []  # (method, path-suffix)
        self.payload: dict = {}

    def __call__(self, method, url, *, payload=None, api_key=None, timeout=None):
        # Record the path after "/collections/…" so assertions are URL-agnostic.
        suffix = url.split("/collections/", 1)[-1]
        self.calls.append((method.upper(), suffix))

        if method.upper() == "PUT" and suffix.endswith("/points"):
            # full upsert (creation) — store payload + vectors
            pts = payload["points"]
            self.payload = dict(pts[0].get("payload", {}))
            return {"result": {"status": "acknowledged"}}
        if method.upper() == "POST" and suffix.endswith("/points/payload"):
            self.payload.update(payload["payload"])
            return {"result": {"status": "acknowledged"}}
        if method.upper() == "PUT" and suffix.endswith("/points/vectors"):
            return {"result": {"status": "acknowledged"}}
        if method.upper() == "GET":
            return {"result": {"payload": dict(self.payload)}}
        if method.upper() == "POST" and suffix.endswith("/points/scroll"):
            return {"result": {"points": []}}
        return {"result": {"status": "ok"}}

    # convenience predicates
    def methods_paths(self):
        return list(self.calls)

    def full_upserts(self):
        return [c for c in self.calls if c[0] == "PUT" and c[1].endswith("/points")]


SESSION_ID = "sess-abc-123"


class TestPointId(unittest.TestCase):
    def test_matches_sessions_upsert(self) -> None:
        # The per-Stop / session-end target must be addressable identically to
        # the existing sessions convention (DTD § 2.3).
        self.assertEqual(
            session_substrate.point_id_for(SESSION_ID),
            sessions_upsert.point_id_for(SESSION_ID),
        )


class TestCreateSessionPoint(unittest.TestCase):
    def test_creation_is_the_one_full_upsert_with_brief_vector(self) -> None:
        rec = RecordingQdrant()
        with patch.object(qdrant_http, "request_json", rec), \
             patch.object(session_substrate, "embed_text", lambda *a, **k: [0.1] * 768):
            session_substrate.create_session_point(
                session_id=SESSION_ID, agent="hephaestus",
                work_item="PROJ-039/T-006", brief_text="build the substrate",
            )
        # exactly one full upsert (creation), targeting /points
        self.assertEqual(len(rec.full_upserts()), 1)
        # payload shape
        self.assertEqual(rec.payload["point_type"], "session")
        self.assertEqual(rec.payload["session_stop"], [])
        self.assertIsNone(rec.payload["response"])
        self.assertEqual(rec.payload["brief"]["text"], "build the substrate")
        self.assertEqual(rec.payload["brief"]["target_agent"], "hephaestus")


class TestAppendSessionStop(unittest.TestCase):
    def test_append_uses_set_payload_not_full_upsert(self) -> None:
        rec = RecordingQdrant()
        rec.payload = {"session_stop": [{"ts": "t0"}]}  # one existing entry
        with patch.object(qdrant_http, "request_json", rec):
            r = session_substrate.append_session_stop(
                SESSION_ID, {"ts": "t1", "response_delta": None, "tool_uses": 3}
            )
        self.assertEqual(r["length"], 2)  # grew by exactly one (DT-004b)
        self.assertEqual(len(rec.payload["session_stop"]), 2)
        self.assertEqual(rec.payload["session_stop"][-1]["tool_uses"], 3)
        # OT-006: no full upsert on an existing point
        self.assertEqual(rec.full_upserts(), [])
        # and it did set_payload
        self.assertIn(
            ("POST", f"{session_substrate.COLLECTION}/points/payload"),
            rec.methods_paths(),
        )

    def test_append_from_empty(self) -> None:
        rec = RecordingQdrant()
        rec.payload = {}  # no session_stop yet
        with patch.object(qdrant_http, "request_json", rec):
            r = session_substrate.append_session_stop(SESSION_ID, {"ts": "t1"})
        self.assertEqual(r["length"], 1)


class TestUpsertResponse(unittest.TestCase):
    def test_response_uses_set_payload_and_update_vectors_only(self) -> None:
        rec = RecordingQdrant()
        with patch.object(qdrant_http, "request_json", rec), \
             patch.object(session_substrate, "embed_text", lambda *a, **k: [0.2] * 768):
            session_substrate.upsert_response(
                SESSION_ID, text="did the thing", status_transition="in_progress->done",
            )
        paths = rec.methods_paths()
        # set_payload for the response object
        self.assertIn(("POST", f"{session_substrate.COLLECTION}/points/payload"), paths)
        # update-vectors for the response named vector (IMPL-1)
        self.assertIn(("PUT", f"{session_substrate.COLLECTION}/points/vectors"), paths)
        # OT-006: never a full upsert
        self.assertEqual(rec.full_upserts(), [])
        self.assertEqual(rec.payload["response"]["text"], "did the thing")
        self.assertEqual(
            rec.payload["response"]["status_transition"], "in_progress->done"
        )


class TestRollup(unittest.TestCase):
    def test_attach_rollup_uses_set_payload(self) -> None:
        rec = RecordingQdrant()
        with patch.object(qdrant_http, "request_json", rec):
            session_substrate.attach_rollup(
                SESSION_ID, {"tool_usage": {"Bash": 2}, "cost_tokens": {}}
            )
        self.assertEqual(rec.full_upserts(), [])
        self.assertEqual(rec.payload["rollup"]["tool_usage"]["Bash"], 2)

    def test_compute_rollup_reconciles_with_jsonl_scrape(self) -> None:
        import json
        import tempfile
        from lib import jsonl_scrape

        lines = [
            json.dumps({
                "type": "assistant", "timestamp": "2026-06-11T10:00:00.000Z",
                "message": {"model": "claude-opus-4-8",
                            "usage": {"input_tokens": 10, "output_tokens": 5},
                            "content": [
                                {"type": "tool_use", "name": "Bash", "input": {}},
                                {"type": "tool_use", "name": "Read", "input": {}},
                            ]},
            }),
            json.dumps({
                "type": "assistant", "timestamp": "2026-06-11T10:00:05.000Z",
                "message": {"model": "claude-opus-4-8",
                            "usage": {"input_tokens": 4, "output_tokens": 2},
                            "content": [{"type": "tool_use", "name": "Bash", "input": {}}]},
            }),
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines) + "\n")
            path = f.name
        try:
            rollup = session_substrate.compute_rollup(path)
            scraped = jsonl_scrape.scrape(path)
            # tool_usage: Bash x2, Read x1
            self.assertEqual(rollup["tool_usage"], {"Bash": 2, "Read": 1})
            # cost_tokens mirrors model_usage exactly (DT-006 reconciliation)
            self.assertEqual(rollup["cost_tokens"], scraped["model_usage"])
            self.assertEqual(
                rollup["cost_tokens"]["claude-opus-4-8"]["input_tokens"], 14
            )
        finally:
            Path(path).unlink()


class TestFindByWorkItem(unittest.TestCase):
    def test_scroll_filter_shape(self) -> None:
        captured = {}

        def fake(method, url, *, payload=None, api_key=None, timeout=None):
            captured["url"] = url
            captured["payload"] = payload
            return {"result": {"points": [{"payload": {"session_id": "found-1"}}]}}

        with patch.object(qdrant_http, "request_json", fake):
            sid = session_substrate.find_session_id_by_work_item(
                "hephaestus", "PROJ-039/T-006"
            )
        self.assertEqual(sid, "found-1")
        must = captured["payload"]["filter"]["must"]
        keys = {m["key"] for m in must}
        self.assertEqual(keys, {"point_type", "agent", "work_item"})


if __name__ == "__main__":
    unittest.main()
