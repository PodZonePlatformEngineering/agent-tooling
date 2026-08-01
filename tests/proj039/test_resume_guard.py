"""Tests for the T-075 F14 resume-guard in hooks/session-materialise.py.

The Athena T-065 failure (sid ``0e0d37f0``): a CLI restart fired the SessionEnd
finalise (result PR raised, branch deleted, lock released, clone returned to
``main``); the resumed conversation came back on the SAME sid on ``main``,
unbranched and unlocked, and post-resume work committed straight to ``main``
outside the guarded lifecycle.

Both paths are fixture-tested here against a REAL git repo + the real finalise
ledger (env-redirected):

  * after-finalise state (ledger ``complete: true`` + clone on ``main`` + no
    live lock) → HALT: sentinel ``ok: false`` with reason
    ``resumed-after-finalise`` and re-arm instructions; neither materialise
    path runs.
  * mid-session restart (live session branch WITH the lock held, finalise not
    yet complete — or even complete) → passes through unchanged.

Live interactive-resume proof is deliberately NOT simulated here — it is
deferred to the T-066 Phase 2 shakedown (interactive, Martin driving).
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location(
    "session_materialise", str(REPO_ROOT / "hooks" / "session-materialise.py")
)
sm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sm)  # type: ignore

from lib import finalise_ledger, session_guard  # noqa: E402


def _run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


class _ResumeFixture(unittest.TestCase):
    """A real git clone on main, a redirected ledger (PODZONE_LOG_DIR) and a
    redirected LOCK_DIR — the three signals the resume-guard reads."""

    SID = "0e0d37f0-0000-0000-0000-000000000000"
    BRANCH = "session/athena-2026-07-08-t065"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.repo = base / "home-training-athena"
        _run("git", "init", "-b", "main", str(self.repo))
        for k, v in (("user.email", "t@t"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            _run("git", "-C", str(self.repo), "config", k, v)
        (self.repo / "README.md").write_text("hi\n")
        _run("git", "-C", str(self.repo), "add", "README.md")
        _run("git", "-C", str(self.repo), "commit", "-m", "init")

        # T-099: main()'s no-BRIEF_ID paths resolve identity from the repo's
        # identity YAML before reaching materialise — the fixture needs one.
        ident = self.repo / "workspaces" / "identity"
        ident.mkdir(parents=True)
        (ident / "athena.identity.yaml").write_text(
            "agent: Athena\nscope: training\n"
            "role_class: agenticflows/roles/trainer/\n", encoding="utf-8")

        self._env = patch.dict(os.environ, clear=False)
        self._env.start()
        os.environ["PODZONE_LOG_DIR"] = str(base / "ledger")
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        os.environ.pop("BRIEF_ID", None)

        self._orig_lock_dir = session_guard.LOCK_DIR
        session_guard.LOCK_DIR = base / "locks"

    def tearDown(self) -> None:
        session_guard.LOCK_DIR = self._orig_lock_dir
        self._env.stop()
        self._tmp.cleanup()

    def _finalise_completes(self, sid: str) -> None:
        finalise_ledger.begin(sid, "", str(self.repo))
        finalise_ledger.record_step(sid, "brief_pr", "done")
        finalise_ledger.complete(sid)

    def _on_session_branch_with_lock(self, sid: str) -> None:
        _run("git", "-C", str(self.repo), "checkout", "-b", self.BRANCH)
        self.assertTrue(session_guard.SessionLock(str(self.repo), sid).acquire()["ok"])


class TestResumedAfterFinalise(_ResumeFixture):
    def test_after_finalise_state_halts(self) -> None:
        """Ledger complete + clone on main + no lock ⇒ the halt dict, with the
        re-arm instructions (re-branch, re-lock, continue)."""
        self._finalise_completes(self.SID)
        halt = sm.resumed_after_finalise(self.SID, str(self.repo))
        self.assertIsNotNone(halt)
        self.assertFalse(halt["ok"])
        self.assertEqual(halt["reason"], "resumed-after-finalise")
        steps = halt["rearm"]["steps"]
        self.assertTrue(any("checkout -b" in s for s in steps), steps)
        self.assertTrue(any("session_guard.py lock" in s for s in steps), steps)

    def test_mid_session_restart_passes_through(self) -> None:
        """A live session branch WITH the lock held (normal restart BEFORE the
        finalise) must pass through unchanged — no halt."""
        self._on_session_branch_with_lock(self.SID)
        self.assertIsNone(sm.resumed_after_finalise(self.SID, str(self.repo)))

    def test_unfinalised_sid_passes_through(self) -> None:
        """No completed finalise on record ⇒ not the F14 state, even on main."""
        self.assertIsNone(sm.resumed_after_finalise(self.SID, str(self.repo)))

    def test_incomplete_ledger_entry_passes_through(self) -> None:
        """A begun-but-truncated finalise (complete=false) is the T-030 guard's
        territory, not F14's — pass through."""
        finalise_ledger.begin(self.SID, "", str(self.repo))
        self.assertIsNone(sm.resumed_after_finalise(self.SID, str(self.repo)))

    def test_finalised_but_still_on_session_branch_passes_through(self) -> None:
        """complete=true but the clone is NOT back on main (e.g. the end-guard
        kept an unpushed branch) — not the unbranched-main hazard; the launch
        preflight owns that state."""
        self._finalise_completes(self.SID)
        _run("git", "-C", str(self.repo), "checkout", "-b", self.BRANCH)
        self.assertIsNone(sm.resumed_after_finalise(self.SID, str(self.repo)))

    def test_finalised_on_main_but_lock_held_passes_through(self) -> None:
        """A live lock for the clone means a session is armed — not the
        unlocked after-finalise state."""
        self._finalise_completes(self.SID)
        session_guard.SessionLock(str(self.repo), "brief:podzone/x").acquire()
        self.assertIsNone(sm.resumed_after_finalise(self.SID, str(self.repo)))

    def test_rearm_lock_sid_prefers_brief_id(self) -> None:
        """Re-arm keys the new lock to the same brief when BRIEF_ID is visible."""
        self._finalise_completes(self.SID)
        os.environ["BRIEF_ID"] = "podzone/2026-07-08-t075-lifecycle-hardening"
        halt = sm.resumed_after_finalise(self.SID, str(self.repo))
        self.assertEqual(halt["rearm"]["lock_sid"],
                         "podzone/2026-07-08-t075-lifecycle-hardening")


class TestResumedAfterFinaliseTrunkMode(_ResumeFixture):
    """PROJ-039/T-121 — a `lifecycle_mode: trunk` repo's finalise commits and
    pushes straight to `main`; there is no result branch to re-arm. The
    branch-mode re-arm (`checkout -b`) would strand the resumed work on an
    orphan branch nobody merges — the exact hazard T-086 exists to prevent.
    The trunk-mode re-arm must be lock-only, matching `/launch-session`
    Step 3's lifecycle-mode fork for the normal dispatch path."""

    def _flip_trunk_mode(self) -> None:
        claude_dir = self.repo / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "tooling-manifest.json").write_text(
            json.dumps({"lifecycle_mode": "trunk"}), encoding="utf-8")

    def test_trunk_repo_rearm_has_no_branch_step(self) -> None:
        self._flip_trunk_mode()
        self._finalise_completes(self.SID)
        halt = sm.resumed_after_finalise(self.SID, str(self.repo))
        self.assertIsNotNone(halt)
        steps = halt["rearm"]["steps"]
        self.assertFalse(any("checkout -b" in s for s in steps), steps)
        self.assertTrue(any("session_guard.py lock" in s for s in steps), steps)
        self.assertIsNone(halt["rearm"]["branch"])

    def test_branch_mode_repo_unaffected(self) -> None:
        """No manifest (default branch mode) keeps the existing two-step re-arm."""
        self._finalise_completes(self.SID)
        halt = sm.resumed_after_finalise(self.SID, str(self.repo))
        steps = halt["rearm"]["steps"]
        self.assertTrue(any("checkout -b" in s for s in steps), steps)


class TestMainHaltsBeforeMaterialise(_ResumeFixture):
    def test_main_writes_sentinel_and_emits_halt(self) -> None:
        """main() must HALT before EITHER materialise path can overwrite the
        sentinel with ok:true — even with BRIEF_ID set (brief-first resume)."""
        self._finalise_completes(self.SID)
        os.environ["BRIEF_ID"] = "podzone/2026-07-08-t075-lifecycle-hardening"

        called = {"brief_first": False, "legacy": False}
        with patch.object(sm, "materialise_brief_first",
                          lambda *a, **k: called.update(brief_first=True) or {"ok": True}), \
             patch.object(sm, "materialise",
                          lambda *a, **k: called.update(legacy=True) or {"ok": True}), \
             patch.object(sys, "stdin", io.StringIO(
                 json.dumps({"session_id": self.SID, "cwd": str(self.repo)}))):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = sm.main()

        self.assertEqual(rc, 0)
        self.assertFalse(called["brief_first"], "brief-first path must not run")
        self.assertFalse(called["legacy"], "legacy path must not run")
        sentinel = json.loads(
            (self.repo / ".workspace" / ".materialise-status.json").read_text())
        self.assertFalse(sentinel["ok"])
        self.assertEqual(sentinel["reason"], "resumed-after-finalise")
        ctx = json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertIn("RESUMED-AFTER-FINALISE", ctx)
        self.assertIn("Re-arm", ctx)

    def test_main_passes_through_on_mid_session_restart(self) -> None:
        """The same stdin on a live session branch with the lock held reaches
        the normal materialise path unchanged."""
        self._on_session_branch_with_lock(self.SID)

        called = {"legacy": False}
        with patch.object(sm, "materialise",
                          lambda *a, **k: called.update(legacy=True) or {"ok": True}), \
             patch.object(sys, "stdin", io.StringIO(
                 json.dumps({"session_id": self.SID, "cwd": str(self.repo)}))):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = sm.main()

        self.assertEqual(rc, 0)
        self.assertTrue(called["legacy"], "normal materialise path must run")


if __name__ == "__main__":
    unittest.main()
