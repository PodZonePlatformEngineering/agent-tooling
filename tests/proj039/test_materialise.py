"""Tests for hooks/session-materialise.py — DT-010 (success) + DT-011 (failure-halt).

The failure path is load-bearing (R-006 / SD-3-002): on Qdrant unreachable /
empty brief the hook writes ok:false, emits the HALT, and does NOT fabricate a
`.workspace` from stale state.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location(
    "session_materialise", str(REPO_ROOT / "hooks" / "session-materialise.py")
)
sm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sm)  # type: ignore

from lib import session_substrate, brief_substrate  # noqa: E402


class TestBriefFirstMaterialise(unittest.TestCase):
    """PROJ-039/T-043: BRIEF_ID path — resolve a first-class brief, stand up the
    session point under the RUNTIME sid, append the sid to session_ids[]."""

    def _brief(self, status="approved"):
        return {
            "brief_id": "podzone/2026-07-02-t043", "body": "Build the briefs collection.",
            "status": status, "assignee": "hephaestus",
            "work_items": ["PROJ-039/T-043"], "session_ids": [],
        }

    def test_brief_first_creates_session_point_and_appends_sid(self) -> None:
        created, appended, started = {}, {}, {}
        with tempfile.TemporaryDirectory() as td, \
             patch.object(brief_substrate, "get_brief", lambda *a, **k: self._brief()), \
             patch.object(session_substrate, "get_session_point", lambda *a, **k: None), \
             patch.object(session_substrate, "create_session_point",
                          lambda **k: created.update(k) or {"ok": True}), \
             patch.object(brief_substrate, "append_session_id",
                          lambda bid, sid, **k: appended.update(brief=bid, sid=sid) or {"ok": True}), \
             patch.object(brief_substrate, "start_brief",
                          lambda bid, **k: started.update(brief=bid) or {"ok": True, "changed": True}), \
             patch.object(session_substrate, "active_work_items", lambda *a, **k: []):
            status = sm.materialise_brief_first("runtime-sid-9", td, "podzone/2026-07-02-t043")
            ws = Path(td) / ".workspace"
            self.assertTrue(status["ok"])
            self.assertEqual(status["source"], "brief")
            self.assertEqual(status["brief_id"], "podzone/2026-07-02-t043")
            # session point created under the RUNTIME sid, with the brief_id ref
            self.assertEqual(created["session_id"], "runtime-sid-9")
            self.assertEqual(created["brief_id"], "podzone/2026-07-02-t043")
            self.assertEqual(created["agent"], "hephaestus")
            # sid appended to the reverse link; brief advanced
            self.assertEqual(appended, {"brief": "podzone/2026-07-02-t043", "sid": "runtime-sid-9"})
            self.assertEqual(started, {"brief": "podzone/2026-07-02-t043"})
            # workspace materialised from the brief body; identity carries brief_id
            self.assertEqual((ws / "brief.md").read_text(), "Build the briefs collection.")
            self.assertEqual(
                json.loads((ws / "identity.json").read_text())["brief_id"],
                "podzone/2026-07-02-t043",
            )

    def test_resume_does_not_recreate_session_point(self) -> None:
        with tempfile.TemporaryDirectory() as td, \
             patch.object(brief_substrate, "get_brief", lambda *a, **k: self._brief("in_progress")), \
             patch.object(session_substrate, "get_session_point", lambda *a, **k: {"exists": True}), \
             patch.object(session_substrate, "create_session_point",
                          lambda **k: (_ for _ in ()).throw(AssertionError("must not create on resume"))), \
             patch.object(brief_substrate, "append_session_id", lambda *a, **k: {"ok": True}), \
             patch.object(brief_substrate, "start_brief", lambda *a, **k: {"ok": True}), \
             patch.object(session_substrate, "active_work_items", lambda *a, **k: []):
            status = sm.materialise_brief_first("runtime-sid-9", td, "podzone/2026-07-02-t043")
            self.assertTrue(status["ok"])

    def test_unapproved_brief_halts_no_fabrication(self) -> None:
        with tempfile.TemporaryDirectory() as td, \
             patch.object(brief_substrate, "get_brief", lambda *a, **k: self._brief("draft")):
            status = sm.materialise_brief_first("runtime-sid-9", td, "podzone/2026-07-02-t043")
            ws = Path(td) / ".workspace"
            self.assertFalse(status["ok"])
            self.assertIn("brief-not-approved", status["reason"])
            self.assertFalse((ws / "brief.md").exists())

    def test_missing_brief_halts(self) -> None:
        with tempfile.TemporaryDirectory() as td, \
             patch.object(brief_substrate, "get_brief", lambda *a, **k: None):
            status = sm.materialise_brief_first("runtime-sid-9", td, "podzone/nope")
            self.assertFalse(status["ok"])
            self.assertIn("brief-not-found", status["reason"])


class TestMaterialiseSuccess(unittest.TestCase):
    def test_dt010_populates_workspace_and_status_ok(self) -> None:
        point = {
            "agent": "hephaestus", "work_item": "PROJ-039/T-006",
            "brief": {"text": "Build the substrate to MVP."},
        }
        tasks = [{"work_item": "PROJ-039/T-006", "status": "in_progress"}]
        with tempfile.TemporaryDirectory() as td, \
             patch.object(session_substrate, "get_session_point", lambda *a, **k: point), \
             patch.object(session_substrate, "active_work_items", lambda *a, **k: tasks):
            status = sm.materialise("sess-1", td, agent="hephaestus")
            ws = Path(td) / ".workspace"
            self.assertTrue(status["ok"])
            self.assertEqual(status["counts"], {"brief": 1, "tasks": 1})
            self.assertEqual((ws / "brief.md").read_text(), "Build the substrate to MVP.")
            self.assertEqual(json.loads((ws / "tasks.json").read_text()), tasks)
            self.assertEqual(json.loads((ws / "identity.json").read_text())["agent"], "hephaestus")
            sentinel = json.loads((ws / ".materialise-status.json").read_text())
            self.assertTrue(sentinel["ok"])
            self.assertEqual(sentinel["source"], "qdrant")


class TestMaterialiseFailureHalt(unittest.TestCase):
    def test_dt011_qdrant_unreachable_writes_ok_false_no_fabrication(self) -> None:
        def boom(*a, **k):
            raise RuntimeError("could not reach Qdrant")
        with tempfile.TemporaryDirectory() as td, \
             patch.object(session_substrate, "get_session_point", boom):
            status = sm.materialise("sess-2", td)
            ws = Path(td) / ".workspace"
            self.assertFalse(status["ok"])
            self.assertIn("qdrant-unreachable", status["reason"])
            # sentinel written, but NO fabricated brief/tasks/identity
            self.assertTrue((ws / ".materialise-status.json").exists())
            self.assertFalse((ws / "brief.md").exists())
            self.assertFalse((ws / "tasks.json").exists())

    def test_dt011_empty_brief_halts(self) -> None:
        with tempfile.TemporaryDirectory() as td, \
             patch.object(session_substrate, "get_session_point", lambda *a, **k: None):
            status = sm.materialise("sess-3", td)
            ws = Path(td) / ".workspace"
            self.assertFalse(status["ok"])
            self.assertEqual(status["reason"], "empty-brief")
            self.assertFalse((ws / "brief.md").exists())

    def test_halt_message_is_explicit(self) -> None:
        self.assertIn("Do NOT begin tasking work", sm.HALT_MESSAGE)
        self.assertIn("MATERIALISE FAILED", sm.HALT_MESSAGE)


class TestMainEmitsContext(unittest.TestCase):
    def test_failure_main_emits_halt_context(self) -> None:
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as td, \
             patch.object(session_substrate, "get_session_point",
                          lambda *a, **k: None), \
             patch.object(sys, "stdin", io.StringIO(
                 json.dumps({"session_id": "s", "cwd": td}))):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = sm.main()
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            ctx = out["hookSpecificOutput"]["additionalContext"]
            self.assertIn("MATERIALISE FAILED", ctx)


if __name__ == "__main__":
    unittest.main()
