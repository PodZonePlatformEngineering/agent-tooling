"""Tests for lib/session_substrate — the PROJ-039 canonical `session` point ops.

The load-bearing property is upsert discipline (SD-3-001 / OT-006): every write
after creation must use set_payload (POST …/points/payload), NEVER a full
upsert (PUT …/points), because a full upsert nulls any named vectors the point
carries. These tests intercept the qdrant_http HTTP layer (``request_json``)
and record every (method, path) so the discipline can be asserted directly —
this is the unit-level realisation of OT-006, DT-004b and DT-005.

PROJ-041/T-002 (operator decision 2026-07-11) layer: lifecycle writes are
**payload-only** (upsert_response patches no vector), and authoring
(create_session_point) is **embed-optional** — it embeds the `brief` vector
only when an embed endpoint is explicitly configured (OLLAMA_HOST env /
ollama_host argument) and otherwise writes ``"vector": {}`` with a stderr note.
"""

from __future__ import annotations

import os
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

FAKE_OLLAMA = "http://fake-ollama.test:11434"


def _with_endpoint():
    """Context: OLLAMA_HOST explicitly configured (embed_text is patched in
    tests, so the value is never dialled)."""
    return patch.dict(os.environ, {"OLLAMA_HOST": FAKE_OLLAMA})


def _without_endpoint():
    """Context: explicit embed opt-out (OLLAMA_HOST=""). Since the WF friction
    fix (2026-08-16) a merely-unset OLLAMA_HOST now defaults to
    http://localhost:11434 (embed_endpoint()) rather than opting out, so this
    helper sets it empty explicitly to keep the "no endpoint anywhere"
    scenario deterministic regardless of what's running on the test executor.
    """
    env = {k: v for k, v in os.environ.items() if k != "OLLAMA_HOST"}
    env["OLLAMA_HOST"] = ""
    return patch.dict(os.environ, env, clear=True)


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
        captured = {}

        def recorder(method, url, *, payload=None, api_key=None, timeout=None):
            if method.upper() == "PUT" and url.endswith("/points"):
                captured["vector"] = payload["points"][0]["vector"]
            return rec(method, url, payload=payload, api_key=api_key, timeout=timeout)

        with _with_endpoint(), \
             patch.object(qdrant_http, "request_json", recorder), \
             patch.object(session_substrate, "embed_text", lambda *a, **k: [0.1] * 768):
            session_substrate.create_session_point(
                session_id=SESSION_ID, agent="hephaestus",
                work_item="PROJ-039/T-006", brief_text="build the substrate",
            )
        # exactly one full upsert (creation), targeting /points
        self.assertEqual(len(rec.full_upserts()), 1)
        # endpoint configured → the brief vector is embedded
        self.assertEqual(len(captured["vector"]["brief"]), 768)
        # payload shape
        self.assertEqual(rec.payload["point_type"], "session")
        self.assertEqual(rec.payload["session_stop"], [])
        self.assertIsNone(rec.payload["response"])
        self.assertEqual(rec.payload["brief"]["text"], "build the substrate")
        self.assertEqual(rec.payload["brief"]["target_agent"], "hephaestus")

    def test_no_endpoint_writes_vector_less_with_stderr_note(self) -> None:
        # PROJ-041/T-002 embed-optional authoring: with no embed endpoint the
        # point is written with an EMPTY named-vector map ({} — an omitted
        # `vector` key is a Qdrant 400; proven live 2026-07-12), the FULL brief
        # text still lands in the payload, and the degradation is announced on
        # stderr rather than silent.
        import io
        from contextlib import redirect_stderr

        rec = RecordingQdrant()
        captured = {}

        def recorder(method, url, *, payload=None, api_key=None, timeout=None):
            if method.upper() == "PUT" and url.endswith("/points"):
                captured["vector"] = payload["points"][0]["vector"]
            return rec(method, url, payload=payload, api_key=api_key, timeout=timeout)

        def boom(*a, **k):
            raise AssertionError("embed_text must not be called with no endpoint")

        err = io.StringIO()
        with _without_endpoint(), \
             patch.object(qdrant_http, "request_json", recorder), \
             patch.object(session_substrate, "embed_text", boom), \
             redirect_stderr(err):
            r = session_substrate.create_session_point(
                session_id=SESSION_ID, agent="hephaestus",
                work_item="PROJ-041/T-002", brief_text="build the substrate",
            )
        self.assertTrue(r["ok"])
        self.assertEqual(captured["vector"], {})
        self.assertEqual(rec.payload["brief"]["text"], "build the substrate")
        self.assertIn("vector-less", err.getvalue())

    def test_explicit_ollama_host_argument_beats_absent_env(self) -> None:
        # The ollama_host= argument alone configures embedding (authoring tools
        # pass it through); env absence must not force vector-less then.
        rec = RecordingQdrant()
        captured = {}

        def recorder(method, url, *, payload=None, api_key=None, timeout=None):
            if method.upper() == "PUT" and url.endswith("/points"):
                captured["vector"] = payload["points"][0]["vector"]
            return rec(method, url, payload=payload, api_key=api_key, timeout=timeout)

        with _without_endpoint(), \
             patch.object(qdrant_http, "request_json", recorder), \
             patch.object(session_substrate, "embed_text", lambda *a, **k: [0.3] * 768):
            session_substrate.create_session_point(
                session_id=SESSION_ID, agent="hephaestus",
                work_item="PROJ-041/T-002", brief_text="explicit host",
                ollama_host=FAKE_OLLAMA,
            )
        self.assertEqual(len(captured["vector"]["brief"]), 768)


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
    def test_response_is_payload_only_set_payload(self) -> None:
        # PROJ-041/T-002: the response write is set_payload ALONE — no
        # update-vectors patch (this runs on the SessionEnd hook path, which
        # never embeds) and, per OT-006, never a full upsert.
        rec = RecordingQdrant()

        def boom(*a, **k):
            raise AssertionError("upsert_response must never embed")

        with _with_endpoint(), \
             patch.object(qdrant_http, "request_json", rec), \
             patch.object(session_substrate, "embed_text", boom):
            session_substrate.upsert_response(
                SESSION_ID, text="did the thing", status_transition="in_progress->done",
            )
        paths = rec.methods_paths()
        # set_payload for the response object
        self.assertIn(("POST", f"{session_substrate.COLLECTION}/points/payload"), paths)
        # payload-only: no vector patch, even with an embed endpoint configured
        self.assertNotIn(("PUT", f"{session_substrate.COLLECTION}/points/vectors"), paths)
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
        self.assertNotIn("tooling", rec.payload)

    def test_attach_rollup_stamps_tooling_version_when_given(self) -> None:
        # PROJ-039/T-055 — the telemetry-payload half of "log entries and git PRs
        # record the tooling version".
        rec = RecordingQdrant()
        with patch.object(qdrant_http, "request_json", rec):
            session_substrate.attach_rollup(
                SESSION_ID, {"tool_usage": {}, "cost_tokens": {}},
                tooling_version="v1.0.0",
            )
        self.assertEqual(rec.payload["tooling"], "v1.0.0")

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


class RecordingEmbed:
    """Stand-in for ``embed_text`` that records each input it was handed and
    returns a fixed 768-dim vector — lets a test assert the embed *input* was
    bounded while the stored payload keeps the full text."""

    def __init__(self, dim: int = session_substrate.EMBED_DIM) -> None:
        self.dim = dim
        self.inputs: list[str] = []

    def __call__(self, text, *a, **k):
        self.inputs.append(text)
        return [0.0] * self.dim


def _over_limit_brief() -> str:
    """A brief comfortably past nomic-embed-text's ~2048-token limit.

    Distinct, signal-bearing head (title/scope/acceptance) followed by enough
    body that even the conservative 3-chars/token estimate clears 2048 tokens —
    the regression condition T-027 fixes (the real failure was a 7275-char brief).
    """
    head = (
        "# PROJ-039/T-999 — Over-limit brief fixture\n\n"
        "## Scope\nHead-of-brief signal that must survive into the embed input.\n\n"
        "## Acceptance\n- authors without an HTTP 500\n\n## Body\n"
    )
    body = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 200)
    text = head + body
    # guard the fixture itself: must exceed the budget (and thus ~2048 tokens)
    assert len(text) > session_substrate._EMBED_CHAR_BUDGET
    return text


class TestEmbedInputBounding(unittest.TestCase):
    """T-027 (CC-326): a >2048-token brief must author without a 500 — the
    embed input is bounded, the stored text stays full. Since PROJ-041/T-002
    this applies only to embed-configured authoring (create_session_point with
    an endpoint); the response path never embeds."""

    def test_large_brief_authors_full_text_stored_embed_bounded(self) -> None:
        import io
        from contextlib import redirect_stderr

        brief = _over_limit_brief()
        rec = RecordingQdrant()
        embed = RecordingEmbed()
        captured = {}

        def recorder(method, url, *, payload=None, api_key=None, timeout=None):
            if method.upper() == "PUT" and url.endswith("/points"):
                captured["vector"] = payload["points"][0]["vector"]["brief"]
            return rec(method, url, payload=payload, api_key=api_key, timeout=timeout)

        err = io.StringIO()
        with _with_endpoint(), \
             patch.object(qdrant_http, "request_json", recorder), \
             patch.object(session_substrate, "embed_text", embed), \
             redirect_stderr(err):
            r = session_substrate.create_session_point(
                session_id=SESSION_ID, agent="hephaestus",
                work_item="PROJ-039/T-999", brief_text=brief,
            )

        self.assertTrue(r["ok"])
        # a 768-dim brief vector is written (no 500)
        self.assertEqual(len(captured["vector"]), session_substrate.EMBED_DIM)
        # embed input was bounded below the budget...
        self.assertEqual(len(embed.inputs), 1)
        self.assertLessEqual(
            len(embed.inputs[0]), session_substrate._EMBED_CHAR_BUDGET
        )
        # ...but the FULL brief text is stored in the payload, unchanged
        self.assertEqual(rec.payload["brief"]["text"], brief)
        # truncation is announced on stderr, not silent
        self.assertIn("head-truncated", err.getvalue())

    def test_large_response_stores_full_text_no_embed(self) -> None:
        # PROJ-041/T-002: the response path is payload-only — a large response
        # simply stores its full text; no bounding is exercised because no
        # embed happens at all.
        big_response = "RESPONSE HEAD. " + ("filler tokens here " * 400)
        self.assertGreater(len(big_response), session_substrate._EMBED_CHAR_BUDGET)
        rec = RecordingQdrant()
        embed = RecordingEmbed()
        with _with_endpoint(), \
             patch.object(qdrant_http, "request_json", rec), \
             patch.object(session_substrate, "embed_text", embed):
            session_substrate.upsert_response(SESSION_ID, text=big_response)
        self.assertEqual(embed.inputs, [])
        # full response text stored unchanged
        self.assertEqual(rec.payload["response"]["text"], big_response)

    def test_within_budget_text_passes_through_unchanged(self) -> None:
        import io
        from contextlib import redirect_stderr

        small = "build the substrate"
        rec = RecordingQdrant()
        embed = RecordingEmbed()
        err = io.StringIO()
        with _with_endpoint(), \
             patch.object(qdrant_http, "request_json", rec), \
             patch.object(session_substrate, "embed_text", embed), \
             redirect_stderr(err):
            session_substrate.create_session_point(
                session_id=SESSION_ID, agent="hephaestus",
                work_item="PROJ-039/T-006", brief_text=small,
            )
        # passed through verbatim, no warning
        self.assertEqual(embed.inputs, [small])
        self.assertEqual(err.getvalue(), "")


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


class TestEmbedEndpointDefault(unittest.TestCase):
    """WF friction fix (2026-08-16): an unset OLLAMA_HOST now defaults to
    http://localhost:11434 instead of opting out of embedding entirely, since
    Ollama is present on essentially every workstation/dispatch sandbox but
    the env var itself is rarely actually exported."""

    def test_unset_defaults_to_localhost(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "OLLAMA_HOST"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(session_substrate.embed_endpoint(), "http://localhost:11434")

    def test_explicit_empty_still_opts_out(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "OLLAMA_HOST"}
        env["OLLAMA_HOST"] = ""
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(session_substrate.embed_endpoint())

    def test_explicit_value_still_wins(self) -> None:
        with _with_endpoint():
            self.assertEqual(session_substrate.embed_endpoint(), FAKE_OLLAMA)

    def test_maybe_embed_degrades_gracefully_on_unreachable_default(self) -> None:
        """An unreachable localhost default must not crash the caller — it was
        never an explicit operator choice, so a connection failure degrades to
        vector-less exactly like the no-endpoint-configured case, not a raise."""
        import io
        import urllib.error
        from contextlib import redirect_stderr

        def refuse(*a, **k):
            raise urllib.error.URLError("Connection refused")

        env = {k: v for k, v in os.environ.items() if k != "OLLAMA_HOST"}
        err = io.StringIO()
        with patch.dict(os.environ, env, clear=True), \
             patch.object(session_substrate, "embed_text", refuse), \
             redirect_stderr(err):
            result = session_substrate.maybe_embed("some text", label="test")
        self.assertIsNone(result)
        self.assertIn("unreachable", err.getvalue())


if __name__ == "__main__":
    unittest.main()
