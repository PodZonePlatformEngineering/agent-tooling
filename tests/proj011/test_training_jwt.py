"""Tests for tools/training-jwt.py — offline claim construction + registry
call discipline. The live claim-set behaviour (self-signed rejected on our
cloud tier) is exercised by test-training-jwt-live.sh, key-gated.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location(
    "training_jwt", REPO_ROOT / "tools" / "training-jwt.py")
training_jwt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_spec and training_jwt)

from lib import qdrant_http  # noqa: E402


def _decode_segment(seg: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))


class TestClaims(unittest.TestCase):
    def test_designed_claim_set(self):
        claims = training_jwt.build_claims(token_id="tid-1",
                                           expires_epoch=1234567890)
        self.assertEqual(claims["exp"], 1234567890)
        self.assertEqual(
            claims["access"],
            [{"collection": "training_briefs", "access": "rw"},
             {"collection": "training_session_telemetry", "access": "rw"}])
        self.assertEqual(
            claims["value_exists"],
            {"collection": "training_token_registry",
             "matches": [{"key": "token_id", "value": "tid-1"}]})

    def test_no_fleet_collection_in_scope(self):
        claims = training_jwt.build_claims(token_id="t", expires_epoch=1)
        scoped = {a["collection"] for a in claims["access"]}
        for fleet in ("briefs", "session_substrate", "claude_session_telemetry"):
            self.assertNotIn(fleet, scoped)


class TestSigning(unittest.TestCase):
    def test_hs256_signature_verifies(self):
        claims = {"exp": 99}
        tok = training_jwt.sign_jwt(claims, "test-key")
        h, p, s = tok.split(".")
        self.assertEqual(_decode_segment(h), {"alg": "HS256", "typ": "JWT"})
        self.assertEqual(_decode_segment(p), claims)
        expected = base64.urlsafe_b64encode(
            hmac.new(b"test-key", f"{h}.{p}".encode(), hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        self.assertEqual(s, expected)

    def test_decode_claims_roundtrip(self):
        claims = training_jwt.build_claims(token_id="t9", expires_epoch=5)
        tok = training_jwt.sign_jwt(claims, "k")
        self.assertEqual(training_jwt.decode_claims(tok), claims)

    def test_decode_claims_rejects_non_jwt(self):
        with self.assertRaises(ValueError):
            training_jwt.decode_claims("opaque-database-key")

    def test_fingerprint_is_not_the_token(self):
        fp = training_jwt.fingerprint("secret-token")
        self.assertEqual(len(fp), 16)
        self.assertNotIn(fp, "secret-token")
        self.assertEqual(fp, training_jwt.fingerprint("secret-token"))


class TestRegistryDiscipline(unittest.TestCase):
    """Registry writes carry the schema fields and hit the right endpoints."""

    def test_registry_upsert_shape(self):
        calls = []

        def fake(method, url, *, payload=None, api_key=None, timeout=None):
            calls.append((method, url, payload))
            return {"result": {"status": "acknowledged"}}

        with patch.object(qdrant_http, "request_json", fake):
            training_jwt.registry_upsert(
                token_id="00000000-0000-0000-0000-000000000001",
                trainee="norma", kind="self_signed", token_fp="ab" * 8,
                expires_at="2026-08-11", claims_summary="rw:x exp:30d")
        (method, url, payload), = calls
        self.assertEqual(method, "PUT")
        self.assertIn("/collections/training_token_registry/points", url)
        point = payload["points"][0]
        self.assertEqual(point["vector"], {})  # payload-only registry
        pl = point["payload"]
        self.assertEqual(pl["trainee"], "norma")
        self.assertEqual(pl["kind"], "self_signed")
        self.assertEqual(pl["active"], "true")
        self.assertEqual(point["id"], pl["token_id"])

    def test_registry_scroll_filters(self):
        calls = []

        def fake(method, url, *, payload=None, api_key=None, timeout=None):
            calls.append(payload)
            return {"result": {"points": []}}

        with patch.object(qdrant_http, "request_json", fake):
            training_jwt.registry_scroll(trainee="norma", active_only=True)
        must = calls[0]["filter"]["must"]
        self.assertIn({"key": "trainee", "match": {"value": "norma"}}, must)
        self.assertIn({"key": "active", "match": {"value": "true"}}, must)


if __name__ == "__main__":
    unittest.main()
