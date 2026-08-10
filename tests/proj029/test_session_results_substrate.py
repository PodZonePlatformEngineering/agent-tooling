"""Tests for lib/session_results_substrate — the PROJ-029/T-257 `session_results`
collection ops (plannerapi Design view v1, design doc §3.3/§3.4).

A session result is written exactly once (no lifecycle mutation the way a brief
has), so unlike test_brief_substrate there is no set_payload-vs-full-upsert
discipline to assert — every write here is (and should be) a full upsert.
These tests intercept qdrant_http.request_json and record calls, mirroring
test_brief_substrate's RecordingQdrant fixture.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import qdrant_http, session_results_substrate as srs, session_substrate  # noqa: E402


class RecordingQdrant:
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
            self.last_vector = pts[0].get("vector")
            self.exists = True
            return {"result": {"status": "acknowledged"}}
        if method.upper() == "GET":
            if not self.exists:
                from lib.qdrant_http import QdrantHTTPError
                raise QdrantHTTPError(404, url, "not found")
            return {"result": {"payload": dict(self.payload)}}
        return {"result": {"status": "ok"}}

    def full_upserts(self):
        return [c for c in self.calls if c[0] == "PUT" and c[1].endswith("/points")]


class TestPointId(unittest.TestCase):
    def test_deterministic_and_folds_in_home_repo(self) -> None:
        import uuid
        pid = srs.point_id_for("home-podzone-hephaestus", "session-2026-08-10-foo.md")
        self.assertEqual(
            pid,
            str(uuid.uuid5(uuid.NAMESPACE_DNS,
                            "home-podzone-hephaestus/session-2026-08-10-foo.md")),
        )
        # Same filename, different home_repo -> different id (the design doc's
        # own reason for compounding the key — filenames collide across repos).
        other = srs.point_id_for("home-podzone-hermes", "session-2026-08-10-foo.md")
        self.assertNotEqual(pid, other)

    def test_convergent(self) -> None:
        self.assertEqual(
            srs.point_id_for("home-x", "f.md"),
            srs.point_id_for("home-x", "f.md"),
        )


class TestProjectRefFromWorkItem(unittest.TestCase):
    def test_splits_prefix(self) -> None:
        self.assertEqual(srs.project_ref_from_work_item("PROJ-011/T-042"), "PROJ-011")

    def test_none_passthrough(self) -> None:
        self.assertIsNone(srs.project_ref_from_work_item(None))

    def test_empty_string_passthrough(self) -> None:
        self.assertIsNone(srs.project_ref_from_work_item(""))

    def test_bare_project_ref_with_no_slash(self) -> None:
        self.assertEqual(srs.project_ref_from_work_item("PROJ-011"), "PROJ-011")


class TestUpsertResult(unittest.TestCase):
    def test_creation_is_one_full_upsert_with_vector(self) -> None:
        rec = RecordingQdrant()
        with patch.dict(os.environ, {"OLLAMA_HOST": "http://fake-ollama.test:11434"}), \
             patch.object(qdrant_http, "request_json", rec), \
             patch.object(session_substrate, "embed_text", lambda *a, **k: [0.1] * 768):
            res = srs.upsert_result(
                home_repo="home-podzone-hephaestus",
                filename="session-2026-08-10-proj-029-t-257-abcd1234.md",
                body="# Session result\n\nDone.",
                work_item="PROJ-029/T-257",
                agent="hephaestus",
                date="2026-08-10",
            )
        self.assertTrue(res["ok"])
        self.assertEqual(res["project_ref"], "PROJ-029")
        self.assertEqual(len(rec.full_upserts()), 1)
        self.assertEqual(len(rec.last_vector["session_result"]), 768)
        self.assertEqual(rec.payload["point_type"], "session_result")
        self.assertEqual(rec.payload["home_repo"], "home-podzone-hephaestus")
        self.assertEqual(rec.payload["work_item"], "PROJ-029/T-257")
        self.assertEqual(rec.payload["project_ref"], "PROJ-029")
        self.assertEqual(rec.payload["body"], "# Session result\n\nDone.")

    def test_no_embed_endpoint_writes_vector_less_with_stderr_note(self) -> None:
        import io
        from contextlib import redirect_stderr

        rec = RecordingQdrant()

        def boom(*a, **k):
            raise AssertionError("embed_text must not be called with no endpoint")

        env = {k: v for k, v in os.environ.items() if k != "OLLAMA_HOST"}
        err = io.StringIO()
        with patch.dict(os.environ, env, clear=True), \
             patch.object(qdrant_http, "request_json", rec), \
             patch.object(session_substrate, "embed_text", boom), \
             redirect_stderr(err):
            res = srs.upsert_result(
                home_repo="home-podzone-hermes",
                filename="session-2026-06-27-proj039-batch2.md",
                body="Old pre-frontmatter content.",
                work_item=None,
                agent="hermes",
                date="2026-06-27",
            )
        self.assertTrue(res["ok"])
        self.assertIsNone(res["project_ref"])
        self.assertEqual(rec.last_vector, {})
        self.assertIn("vector-less", err.getvalue())

    def test_title_defaults_to_filename_stem(self) -> None:
        rec = RecordingQdrant()
        with patch.object(qdrant_http, "request_json", rec), \
             patch.object(session_substrate, "embed_text", lambda *a, **k: [0.1] * 768):
            srs.upsert_result(
                home_repo="home-x", filename="session-2026-08-10-foo.md",
                body="body text", work_item=None, agent="a", date="2026-08-10",
            )
        self.assertEqual(rec.payload["title"], "session-2026-08-10-foo")

    def test_reupsert_same_key_converges(self) -> None:
        rec = RecordingQdrant()
        with patch.object(qdrant_http, "request_json", rec), \
             patch.object(session_substrate, "embed_text", lambda *a, **k: [0.1] * 768):
            r1 = srs.upsert_result(
                home_repo="home-x", filename="f.md", body="v1",
                work_item="PROJ-001/T-001", agent="a", date="2026-08-10",
            )
            r2 = srs.upsert_result(
                home_repo="home-x", filename="f.md", body="v2",
                work_item="PROJ-001/T-001", agent="a", date="2026-08-10",
            )
        self.assertEqual(r1["point_id"], r2["point_id"])
        self.assertEqual(len(rec.full_upserts()), 2)  # two writes, one convergent id
        self.assertEqual(rec.payload["body"], "v2")

    def test_empty_body_rejected(self) -> None:
        with patch.object(session_substrate, "embed_text", lambda *a, **k: [0.1] * 768):
            with self.assertRaises(ValueError):
                srs.upsert_result(
                    home_repo="home-x", filename="f.md", body="   ",
                    work_item=None, agent="a", date="2026-08-10",
                )


class TestGetResult(unittest.TestCase):
    def test_fetch_returns_payload(self) -> None:
        rec = RecordingQdrant({"home_repo": "home-x", "filename": "f.md"})
        with patch.object(qdrant_http, "request_json", rec):
            payload = srs.get_result("home-x", "f.md")
        self.assertEqual(payload["home_repo"], "home-x")

    def test_fetch_missing_returns_none(self) -> None:
        rec = RecordingQdrant(None)
        with patch.object(qdrant_http, "request_json", rec):
            self.assertIsNone(srs.get_result("home-x", "nope.md"))


if __name__ == "__main__":
    unittest.main()
