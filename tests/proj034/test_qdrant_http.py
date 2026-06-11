"""Tests for lib/qdrant_http — the PROJ-033/T-016 canonical Qdrant primitive.

Covers the two load-bearing guarantees:
  1. A missing/empty PODZONE_QDRANT_APIKEY raises QdrantAuthError — no path
     proceeds to an unauthenticated (silent zero-write / 403) request.
  2. No third-party dependency: the HTTP path is pure stdlib urllib, so it
     works in an interpreter that has no `requests` (the system python3) and
     from a Bash-tool subprocess. The subprocess tests below prove this
     end-to-end against a local stdlib HTTP stub.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import qdrant_http  # noqa: E402


class TestResolveApiKey(unittest.TestCase):

    def test_empty_env_raises(self) -> None:
        with patch.dict(os.environ, {"PODZONE_QDRANT_APIKEY": ""}, clear=False):
            with self.assertRaises(qdrant_http.QdrantAuthError):
                qdrant_http.resolve_api_key()

    def test_missing_env_raises(self) -> None:
        env = dict(os.environ)
        env.pop("PODZONE_QDRANT_APIKEY", None)
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(qdrant_http.QdrantAuthError):
                qdrant_http.resolve_api_key()

    def test_explicit_arg_wins(self) -> None:
        with patch.dict(os.environ, {"PODZONE_QDRANT_APIKEY": ""}, clear=False):
            self.assertEqual(qdrant_http.resolve_api_key("explicit"), "explicit")

    def test_env_used(self) -> None:
        with patch.dict(os.environ, {"PODZONE_QDRANT_APIKEY": "env-key"}, clear=False):
            self.assertEqual(qdrant_http.resolve_api_key(), "env-key")


class TestSSLContext(unittest.TestCase):

    def test_ca_bundle_is_loaded(self) -> None:
        """Guards the urllib regression: on framework Python builds with no
        default CA bundle and no certifi, the context must still load certs
        from a system bundle — otherwise every https call fails verification."""
        qdrant_http._SSL_CONTEXT = None  # reset cache
        try:
            ctx = qdrant_http._ssl_context()
            self.assertGreater(
                len(ctx.get_ca_certs()), 0,
                "no CA certs loaded — https Qdrant calls would fail verification",
            )
        finally:
            qdrant_http._SSL_CONTEXT = None


# --- A local stdlib HTTP stub standing in for cloud Qdrant -------------------

class _StubHandler(BaseHTTPRequestHandler):
    last_auth = None  # capture the api-key header the client sent

    def log_message(self, *_args) -> None:  # silence
        pass

    def _respond(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        type(self).last_auth = self.headers.get("api-key")
        body = json.dumps({"result": {"status": "ok"}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_PUT = _respond
    do_POST = _respond
    do_GET = _respond


class _StubServer:
    def __init__(self) -> None:
        self.httpd = HTTPServer(("127.0.0.1", 0), _StubHandler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> "_StubServer":
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=2)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class TestRequestJson(unittest.TestCase):

    def test_put_sends_auth_header_and_parses_body(self) -> None:
        with _StubServer() as stub:
            body = qdrant_http.request_json(
                "PUT",
                f"{stub.url}/collections/sessions/points",
                payload={"points": []},
                api_key="abc123",
            )
            self.assertEqual(body["result"]["status"], "ok")
            self.assertEqual(_StubHandler.last_auth, "abc123")

    def test_no_key_raises_before_any_request(self) -> None:
        with patch.dict(os.environ, {"PODZONE_QDRANT_APIKEY": ""}, clear=False):
            with self.assertRaises(qdrant_http.QdrantAuthError):
                qdrant_http.request_json("GET", "http://127.0.0.1:1/collections/x")


# --- End-to-end subprocess tests (AC1/AC4) -----------------------------------

# A self-contained script: import the shared wrapper and attempt an upsert.
# Exit 0 on success, 1 on a handled best-effort failure. An unhandled
# QdrantAuthError (no key) propagates → non-zero exit with a loud traceback.
_SCRIPT = (
    "import sys; "
    "from lib import sessions_upsert; "
    "r = sessions_upsert.upsert_session("
    "{'session_id': 'subproc-1', 'jsonl_mtime': '2026-06-10T10:00:00+00:00'}, "
    "data_source='backfill'); "
    "sys.exit(0 if r['ok'] else 2)"
)


def _run_subprocess(env_extra: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PODZONE_QDRANT_APIKEY", None)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestSubprocessCanonicalPath(unittest.TestCase):

    def test_subprocess_succeeds_when_key_resolvable(self) -> None:
        """A Qdrant-touching script succeeds from a subprocess via the canonical
        path (stdlib urllib, no `requests`), with the key injected as it would
        be by the harness env block / `secretctl run`."""
        with _StubServer() as stub:
            proc = _run_subprocess(
                {"PODZONE_QDRANT_APIKEY": "test-key", "PODZONE_QDRANT_URL": stub.url}
            )
        self.assertEqual(proc.returncode, 0, msg=f"stderr: {proc.stderr}")

    def test_subprocess_fails_loudly_without_key(self) -> None:
        """Without the bootstrap key, the script fails LOUDLY (non-zero exit,
        key named in stderr) rather than silently no-op'ing a zero-write."""
        proc = _run_subprocess({})  # no PODZONE_QDRANT_APIKEY
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("PODZONE_QDRANT_APIKEY", proc.stderr)
        self.assertIn("QdrantAuthError", proc.stderr)


if __name__ == "__main__":
    unittest.main()
