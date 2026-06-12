"""Tests for the PROJ-039 additions to lib/qdrant_http: the partial-write and
collection-management helpers (set_payload, update_vectors, create_collection,
create_payload_index, search, get_point with_vector). Verified end-to-end against
a recording stdlib HTTP stub so the exact endpoint + verb + body are asserted.
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import qdrant_http  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    records: list = []

    def log_message(self, *_a) -> None:
        pass

    def _respond(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        body = {}
        if raw:
            try:
                body = json.loads(raw)
            except Exception:
                body = {}
        type(self).records.append((self.command, self.path, body))
        # A search returns a result list; everything else a status envelope.
        if self.path.endswith("/points/search"):
            out = {"result": [{"id": "p1", "score": 0.99, "payload": {"x": 1}}]}
        elif self.command == "GET" and "/points/" in self.path:
            out = {"result": {"id": "p1", "payload": {"k": "v"},
                              "vector": {"brief": [0.1, 0.2]}}}
        else:
            out = {"result": {"status": "acknowledged"}}
        data = json.dumps(out).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_PUT = _respond
    do_POST = _respond
    do_GET = _respond


class _Server:
    def __enter__(self):
        _Handler.records = []
        self.httpd = HTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()
        self.url = f"http://127.0.0.1:{self.port}"
        return self

    def __exit__(self, *_e):
        self.httpd.shutdown()
        self.t.join(timeout=2)
        self.httpd.server_close()


KEY = "test-key"
COLL = "session_substrate"


class TestPartialWrites(unittest.TestCase):

    def test_set_payload_posts_to_points_payload(self) -> None:
        with _Server() as s:
            qdrant_http.set_payload(
                {"response": {"text": "hi"}}, ["p1"],
                collection=COLL, qdrant_url=s.url, api_key=KEY,
            )
            cmd, path, body = _Handler.records[-1]
        self.assertEqual(cmd, "POST")
        self.assertTrue(path.endswith(f"/collections/{COLL}/points/payload"))
        self.assertEqual(body["points"], ["p1"])
        self.assertEqual(body["payload"]["response"]["text"], "hi")

    def test_update_vectors_puts_to_points_vectors(self) -> None:
        with _Server() as s:
            qdrant_http.update_vectors(
                [{"id": "p1", "vector": {"response": [0.0] * 768}}],
                collection=COLL, qdrant_url=s.url, api_key=KEY,
            )
            cmd, path, body = _Handler.records[-1]
        self.assertEqual(cmd, "PUT")
        self.assertTrue(path.endswith(f"/collections/{COLL}/points/vectors"))
        self.assertEqual(body["points"][0]["vector"]["response"], [0.0] * 768)

    def test_create_collection_and_index(self) -> None:
        with _Server() as s:
            qdrant_http.create_collection(
                {"brief": {"size": 768, "distance": "Cosine"}},
                collection=COLL, qdrant_url=s.url, api_key=KEY,
            )
            cmd1, path1, body1 = _Handler.records[-1]
            qdrant_http.create_payload_index(
                "point_type", collection=COLL, qdrant_url=s.url, api_key=KEY,
            )
            cmd2, path2, body2 = _Handler.records[-1]
        self.assertEqual(cmd1, "PUT")
        self.assertTrue(path1.endswith(f"/collections/{COLL}"))
        self.assertEqual(body1["vectors"]["brief"]["size"], 768)
        self.assertEqual(cmd2, "PUT")
        self.assertTrue(path2.endswith(f"/collections/{COLL}/index"))
        self.assertEqual(body2["field_name"], "point_type")
        self.assertEqual(body2["field_schema"], "keyword")

    def test_search_uses_named_vector(self) -> None:
        with _Server() as s:
            res = qdrant_http.search(
                [0.0] * 768, collection=COLL, using="brief", limit=3,
                qdrant_url=s.url, api_key=KEY,
            )
            cmd, path, body = _Handler.records[-1]
        self.assertEqual(cmd, "POST")
        self.assertTrue(path.endswith(f"/collections/{COLL}/points/search"))
        # named-vector form: {"vector": {"name": "brief", "vector": [...]}}
        self.assertEqual(body["vector"]["name"], "brief")
        self.assertEqual(body["limit"], 3)
        self.assertEqual(res[0]["id"], "p1")

    def test_get_point_with_vector_returns_full_result(self) -> None:
        with _Server() as s:
            full = qdrant_http.get_point(
                "p1", collection=COLL, qdrant_url=s.url, api_key=KEY,
                with_vector=True,
            )
            _, path, _ = _Handler.records[-1]
        self.assertIn("with_vector=true", path)
        self.assertIn("vector", full)
        self.assertIn("brief", full["vector"])

    def test_get_point_default_returns_payload(self) -> None:
        with _Server() as s:
            payload = qdrant_http.get_point(
                "p1", collection=COLL, qdrant_url=s.url, api_key=KEY,
            )
        self.assertEqual(payload, {"k": "v"})


if __name__ == "__main__":
    unittest.main()
