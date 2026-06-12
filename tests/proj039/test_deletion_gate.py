"""Tests for the session-end deletion gate — DT-007 (R-013, § 2.4 step 5 / C-006).

The raw PreToolUse/PostToolUse deletion from CST must run ONLY after a successful
telemetry push. With the push forced to fail, the raw points must remain. Covers
both the cst_cleanup primitive (correct scoped filter) and the gate logic in
session-end-finalise.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import cst_cleanup, qdrant_http  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "session_end_finalise", str(REPO_ROOT / "hooks" / "session-end-finalise.py")
)
sef = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sef)  # type: ignore


class TestCstCleanupPrimitive(unittest.TestCase):
    def test_delete_uses_scoped_filter(self) -> None:
        calls = []

        def fake(method, url, *, payload=None, api_key=None, timeout=None):
            calls.append((method, url, payload))
            if url.endswith("/points/scroll"):
                return {"result": {"points": [{"id": "a"}, {"id": "b"}]}}
            return {"result": {"status": "acknowledged"}}

        with patch.object(qdrant_http, "request_json", fake):
            res = cst_cleanup.delete_raw_tool_events("sess-1")

        self.assertEqual(res["deleted_before_count"], 2)
        # the delete call carried the scoped filter: session_id + the two raw types
        delete_call = [c for c in calls if c[1].endswith("/points/delete")][0]
        must = delete_call[2]["filter"]["must"]
        keys = {m["key"] for m in must}
        self.assertEqual(keys, {"session_id", "event_type"})
        event_filter = [m for m in must if m["key"] == "event_type"][0]
        self.assertEqual(set(event_filter["match"]["any"]),
                         {"PreToolUse", "PostToolUse"})
        # targets CST, not session_substrate (DC-1)
        self.assertIn("claude_session_telemetry", delete_call[1])


class TestDeletionGate(unittest.TestCase):
    """The gate: delete only when telemetry push landed (DT-007 negative case)."""

    def _run_finalise(self, *, pushed: bool):
        deleted = {"called": False}

        def fake_delete(session_id, **k):
            deleted["called"] = True
            return {"deleted_before_count": 3, "ok": True}

        stdin = json.dumps({"session_id": "sess-gate", "transcript_path": ""})

        with patch.object(sys, "stdin", io.StringIO(stdin)), \
             patch("lib.session_substrate.upsert_response", lambda *a, **k: {"ok": True}), \
             patch("lib.telemetry_repo.commit_and_push",
                   lambda *a, **k: {"committed": True, "pushed": pushed, "reason": ""}), \
             patch("lib.cst_cleanup.delete_raw_tool_events", fake_delete):
            buf = io.StringIO()
            with redirect_stderr(buf):
                rc = sef.main()
        return rc, deleted["called"], buf.getvalue()

    def test_push_success_triggers_delete(self) -> None:
        rc, called, _log = self._run_finalise(pushed=True)
        self.assertEqual(rc, 0)
        self.assertTrue(called, "delete should run after a successful push")

    def test_push_failure_blocks_delete(self) -> None:
        rc, called, log = self._run_finalise(pushed=False)
        self.assertEqual(rc, 0)
        self.assertFalse(called, "delete MUST NOT run when push failed (C-006)")
        self.assertIn("RETAINED", log)


if __name__ == "__main__":
    unittest.main()
