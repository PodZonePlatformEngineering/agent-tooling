"""Tests for hooks/session-materialise.py — DT-010 (success) + DT-011 (failure-halt).

The failure path is load-bearing (R-006 / SD-3-002): on Qdrant unreachable /
empty brief the hook writes ok:false, emits the HALT, and does NOT fabricate a
`.workspace` from stale state.
"""

from __future__ import annotations

import importlib.util
import json
import os
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

from lib import session_substrate, brief_substrate, session_stash_substrate  # noqa: E402


def write_identity_yaml(cwd: str, *, agent: str = "someone",
                        role_class: str = "agenticflows/roles/coder/") -> Path:
    """A resolvable identity YAML in `cwd` — T-099 single-source resolution
    means every no-BRIEF_ID main() path needs one (or a patched
    _resolve_agent_and_role) to get past the identity gate."""
    ident = Path(cwd) / "workspaces" / "identity"
    ident.mkdir(parents=True, exist_ok=True)
    p = ident / f"martin-{agent}.identity.yaml"
    p.write_text(f"agent: {agent}\nscope: podzone\nrole_class: {role_class}\n",
                 encoding="utf-8")
    return p


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

    def test_pop_surfaces_stash_content_into_status(self) -> None:
        """PROJ-039/T-257 §5.4: an active stash entry's content rides on the
        returned status so main() can prepend it to the emitted context."""
        popped = {}
        with tempfile.TemporaryDirectory() as td, \
             patch.object(brief_substrate, "get_brief", lambda *a, **k: self._brief()), \
             patch.object(session_substrate, "get_session_point", lambda *a, **k: None), \
             patch.object(session_substrate, "create_session_point",
                          lambda **k: {"ok": True}), \
             patch.object(brief_substrate, "append_session_id", lambda *a, **k: {"ok": True}), \
             patch.object(brief_substrate, "start_brief", lambda *a, **k: {"ok": True}), \
             patch.object(session_substrate, "active_work_items", lambda *a, **k: []), \
             patch.object(session_stash_substrate, "pop",
                          lambda bid, sid, **k: popped.update(brief=bid, sid=sid) or
                          {"content": "resume here — mid-refactor of X.", "trigger": "limit_stop"}):
            status = sm.materialise_brief_first("runtime-sid-9", td, "podzone/2026-07-02-t043")
            self.assertTrue(status["ok"])
            self.assertEqual(status["stash_content"], "resume here — mid-refactor of X.")
            # popped with the resolved brief_id and the RUNTIME sid, not some
            # other identifier.
            self.assertEqual(popped, {"brief": "podzone/2026-07-02-t043", "sid": "runtime-sid-9"})

    def test_no_stash_entry_is_a_normal_noop(self) -> None:
        """pop() returning None (the common case — no pending stash) must not
        surface a stash_content key or otherwise change materialise's success
        shape."""
        with tempfile.TemporaryDirectory() as td, \
             patch.object(brief_substrate, "get_brief", lambda *a, **k: self._brief()), \
             patch.object(session_substrate, "get_session_point", lambda *a, **k: None), \
             patch.object(session_substrate, "create_session_point",
                          lambda **k: {"ok": True}), \
             patch.object(brief_substrate, "append_session_id", lambda *a, **k: {"ok": True}), \
             patch.object(brief_substrate, "start_brief", lambda *a, **k: {"ok": True}), \
             patch.object(session_substrate, "active_work_items", lambda *a, **k: []), \
             patch.object(session_stash_substrate, "pop", lambda *a, **k: None):
            status = sm.materialise_brief_first("runtime-sid-9", td, "podzone/2026-07-02-t043")
            self.assertTrue(status["ok"])
            self.assertNotIn("stash_content", status)

    def test_pop_failure_is_soft_materialise_still_succeeds(self) -> None:
        """A pop failure (Qdrant blip) must never block SessionStart — the
        brief-first materialise path completes normally regardless."""
        def boom(*a, **k):
            raise RuntimeError("qdrant unreachable")

        with tempfile.TemporaryDirectory() as td, \
             patch.object(brief_substrate, "get_brief", lambda *a, **k: self._brief()), \
             patch.object(session_substrate, "get_session_point", lambda *a, **k: None), \
             patch.object(session_substrate, "create_session_point",
                          lambda **k: {"ok": True}), \
             patch.object(brief_substrate, "append_session_id", lambda *a, **k: {"ok": True}), \
             patch.object(brief_substrate, "start_brief", lambda *a, **k: {"ok": True}), \
             patch.object(session_substrate, "active_work_items", lambda *a, **k: []), \
             patch.object(session_stash_substrate, "pop", boom):
            status = sm.materialise_brief_first("runtime-sid-9", td, "podzone/2026-07-02-t043")
            self.assertTrue(status["ok"])
            self.assertNotIn("stash_content", status)


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

        # Hermetic against a live session's env: T-045 serial mode runs sessions
        # inside the primary clone, so BRIEF_ID may be set — which would send the
        # hook down the brief-first success path and mask the failure we assert.
        # patch.dict(clear=False) snapshots os.environ and restores it on exit.
        with tempfile.TemporaryDirectory() as td, \
             patch.dict(os.environ, clear=False), \
             patch.object(session_substrate, "get_session_point",
                          lambda *a, **k: None), \
             patch.object(sys, "stdin", io.StringIO(
                 json.dumps({"session_id": "s", "cwd": td}))):
            os.environ.pop("BRIEF_ID", None)
            write_identity_yaml(td)  # past the T-099 identity gate
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = sm.main()
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            ctx = out["hookSpecificOutput"]["additionalContext"]
            self.assertIn("MATERIALISE FAILED", ctx)


class TestMainPrependsStashContent(unittest.TestCase):
    """PROJ-039/T-257 §5.4: main() prepends a popped stash entry's content
    ahead of the normal brief-first success context, on the same
    `additionalContext` channel — never a separate emission."""

    def _brief(self):
        return {
            "brief_id": "podzone/2026-07-02-t043", "body": "Build the briefs collection.",
            "status": "approved", "assignee": "hephaestus",
            "work_items": ["PROJ-039/T-043"], "session_ids": [],
        }

    def test_stash_content_prepended_ahead_of_success_message(self) -> None:
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as td, \
             patch.dict(os.environ, clear=False), \
             patch.object(brief_substrate, "get_brief", lambda *a, **k: self._brief()), \
             patch.object(session_substrate, "get_session_point", lambda *a, **k: None), \
             patch.object(session_substrate, "create_session_point", lambda **k: {"ok": True}), \
             patch.object(brief_substrate, "append_session_id", lambda *a, **k: {"ok": True}), \
             patch.object(brief_substrate, "start_brief", lambda *a, **k: {"ok": True}), \
             patch.object(session_substrate, "active_work_items", lambda *a, **k: []), \
             patch.object(session_stash_substrate, "pop",
                          lambda *a, **k: {"content": "was mid-way through the launch.sh wiring."}), \
             patch.object(sys, "stdin", io.StringIO(
                 json.dumps({"session_id": "runtime-sid-9", "cwd": td}))):
            os.environ["BRIEF_ID"] = "podzone/2026-07-02-t043"
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = sm.main()
            finally:
                os.environ.pop("BRIEF_ID", None)
            self.assertEqual(rc, 0)
            ctx = json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]
            self.assertIn("was mid-way through the launch.sh wiring.", ctx)
            self.assertIn("Session materialised from brief", ctx)
            # the stash content leads, the normal success message follows.
            self.assertLess(
                ctx.index("was mid-way through"), ctx.index("Session materialised from brief")
            )


class TestTeamLeadNoBriefSkip(unittest.TestCase):
    """F15 (PROJ-039/T-078): team-lead + no BRIEF_ID must not HALT; non-team-lead
    + no BRIEF_ID must HALT unchanged (regression guard); team-lead + BRIEF_ID set
    must take the unchanged brief-first path."""

    def _run_main(self, cwd: str, *, resolved_role: str):
        import io
        from contextlib import redirect_stdout

        with patch.dict(os.environ, clear=False), \
             patch.object(sm, "_resolve_agent_and_role",
                          lambda cwd: ("someone", resolved_role)), \
             patch.object(sys, "stdin", io.StringIO(
                 json.dumps({"session_id": "s", "cwd": cwd}))):
            os.environ.pop("BRIEF_ID", None)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = sm.main()
            return rc, json.loads(buf.getvalue())

    def test_team_lead_no_brief_id_no_halt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rc, out = self._run_main(td, resolved_role="team-lead")
            ctx = out["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(rc, 0)
            self.assertNotIn("MATERIALISE FAILED", ctx)
            self.assertIn("no tasking brief expected", ctx)
            status = json.loads((Path(td) / ".workspace" / ".materialise-status.json").read_text())
            self.assertTrue(status["ok"])
            self.assertEqual(status["reason"], "team-lead-no-brief")

    def test_non_team_lead_no_brief_id_still_halts(self) -> None:
        with tempfile.TemporaryDirectory() as td, \
             patch.object(session_substrate, "get_session_point", lambda *a, **k: None):
            rc, out = self._run_main(td, resolved_role="coder")
            ctx = out["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(rc, 0)
            self.assertIn("MATERIALISE FAILED", ctx)

    def test_team_lead_with_brief_id_takes_brief_first_path(self) -> None:
        brief = {
            "brief_id": "podzone/2026-07-09-t078", "body": "Lead-to-lead sync.",
            "status": "approved", "assignee": "hermes",
            "work_items": ["PROJ-039/T-078"], "session_ids": [],
        }
        with tempfile.TemporaryDirectory() as td, \
             patch.dict(os.environ, {"BRIEF_ID": "podzone/2026-07-09-t078"}), \
             patch.object(brief_substrate, "get_brief", lambda *a, **k: brief), \
             patch.object(session_substrate, "get_session_point", lambda *a, **k: None), \
             patch.object(session_substrate, "create_session_point", lambda **k: {"ok": True}), \
             patch.object(brief_substrate, "append_session_id", lambda *a, **k: {"ok": True}), \
             patch.object(brief_substrate, "start_brief", lambda *a, **k: {"ok": True}), \
             patch.object(session_substrate, "active_work_items", lambda *a, **k: []):
            import io
            from contextlib import redirect_stdout
            with patch.object(sys, "stdin", io.StringIO(
                    json.dumps({"session_id": "s", "cwd": td}))):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = sm.main()
            out = json.loads(buf.getvalue())
            ctx = out["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(rc, 0)
            self.assertIn("Session materialised from brief", ctx)
            self.assertTrue((Path(td) / ".workspace" / "brief.md").exists())


class TestIdentityGate(unittest.TestCase):
    """T-099 (CC-409): main() resolves identity from the home repo's identity
    YAML — the single source. Unresolved identity HALTs with its own sentinel
    (never a decayed ("unknown","unknown") that surfaces as `empty-brief`);
    a real team-lead YAML fires the F15 skip end-to-end (regression for the
    2026-07-11 home-podzone-hermes halt)."""

    def _run_main(self, cwd: str):
        import io
        from contextlib import redirect_stdout

        with patch.dict(os.environ, clear=False), \
             patch.object(sys, "stdin", io.StringIO(
                 json.dumps({"session_id": "s", "cwd": cwd}))):
            os.environ.pop("BRIEF_ID", None)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = sm.main()
            return rc, json.loads(buf.getvalue())

    def test_no_identity_yaml_halts_identity_unresolved(self) -> None:
        """No YAML + no BRIEF_ID → identity-unresolved sentinel + halt that
        names the YAML path and does NOT misdirect to Qdrant/API-key work."""
        with tempfile.TemporaryDirectory() as td:
            rc, out = self._run_main(td)
            ctx = out["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(rc, 0)
            self.assertIn("IDENTITY UNRESOLVED", ctx)
            self.assertNotIn("MATERIALISE FAILED", ctx)
            status = json.loads(
                (Path(td) / ".workspace" / ".materialise-status.json").read_text())
            self.assertFalse(status["ok"])
            self.assertTrue(status["reason"].startswith("identity-unresolved:"))

    def test_placeholder_yaml_halts_identity_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            write_identity_yaml(td, agent="FILL_IN")
            rc, out = self._run_main(td)
            ctx = out["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(rc, 0)
            self.assertIn("IDENTITY UNRESOLVED", ctx)
            status = json.loads(
                (Path(td) / ".workspace" / ".materialise-status.json").read_text())
            self.assertIn("placeholder", status["reason"])

    def test_team_lead_yaml_no_brief_id_fires_f15_skip(self) -> None:
        """The 2026-07-11 regression: a team-lead identity YAML (Hermes's live
        `team-lead-apex` variant, inline comment included) + no BRIEF_ID must
        take the F15 skip — resolved from the YAML itself, no monkeypatching."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "home-podzone-hermes"
            ident = repo / "workspaces" / "identity"
            ident.mkdir(parents=True)
            (ident / "martin-hermes.identity.yaml").write_text(
                "agent: Hermes  # FILL IN — capitalised agent name confirmed\n"
                "scope: programme\n"
                "role_class: agenticflows/roles/team-lead-apex/\n",
                encoding="utf-8")
            rc, out = self._run_main(str(repo))
            ctx = out["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(rc, 0)
            self.assertIn("no tasking brief expected", ctx)
            status = json.loads(
                (repo / ".workspace" / ".materialise-status.json").read_text())
            self.assertTrue(status["ok"])
            self.assertEqual(status["reason"], "team-lead-no-brief")

    def test_brief_id_path_bypasses_identity_gate(self) -> None:
        """A BRIEF_ID session keys its agent off the brief's assignee — a
        broken/missing identity YAML must NOT block the brief-first path
        (workers survived the legacy decay for exactly this reason)."""
        brief = {
            "brief_id": "podzone/2026-07-11-t099", "body": "Resolver core.",
            "status": "approved", "assignee": "hephaestus",
            "work_items": ["PROJ-039/T-099"], "session_ids": [],
        }
        with tempfile.TemporaryDirectory() as td, \
             patch.dict(os.environ, {"BRIEF_ID": "podzone/2026-07-11-t099"}), \
             patch.object(brief_substrate, "get_brief", lambda *a, **k: brief), \
             patch.object(session_substrate, "get_session_point", lambda *a, **k: None), \
             patch.object(session_substrate, "create_session_point", lambda **k: {"ok": True}), \
             patch.object(brief_substrate, "append_session_id", lambda *a, **k: {"ok": True}), \
             patch.object(brief_substrate, "start_brief", lambda *a, **k: {"ok": True}), \
             patch.object(session_substrate, "active_work_items", lambda *a, **k: []):
            import io
            from contextlib import redirect_stdout
            with patch.object(sys, "stdin", io.StringIO(
                    json.dumps({"session_id": "s", "cwd": td}))):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = sm.main()
            ctx = json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(rc, 0)
            self.assertIn("Session materialised from brief", ctx)
            self.assertNotIn("IDENTITY UNRESOLVED", ctx)


if __name__ == "__main__":
    unittest.main()
