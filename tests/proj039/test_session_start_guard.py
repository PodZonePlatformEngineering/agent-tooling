"""Tests for the session-start.sh TOOLING_UPDATE loud-fail guard
(PROJ-039/T-069, T-065 F1/F12).

The defect class: TOOLING_UPDATE set at launch + the updater not wired (or its
tool file missing, or its hook killed) = silent no-op — stale tooling while the
brief's audit field claims the new version was delivered. The guard is
symmetrical to the T-052 materialise guard: no ok:true sentinel keyed to THIS
startup's session_id → HALT loudly on stdout (the agent's context), write an
ok:false sentinel for the orientation ritual, and never proceed silently.

Runs the real hooks/session-start.sh with a crafted hook-stdin JSON against a
temp cwd. Telemetry/guard sub-steps are best-effort by design and degrade to
no-ops in the unconfigured test environment. TOOLING_GUARD_WAIT_SECS=1 keeps
the missing-sentinel poll short.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "hooks" / "session-start.sh"

SID = "aaaabbbb-0000-0000-0000-000000000000"


class TestToolingUpdateGuard(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self._tmp.name)
        self.sentinel = self.cwd / ".workspace" / ".tooling-update-status.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *, tooling_update: str | None = "v9.9.9") -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.pop("TOOLING_UPDATE", None)
        env.pop("BRIEF_ID", None)
        if tooling_update is not None:
            env["TOOLING_UPDATE"] = tooling_update
        env["TOOLING_GUARD_WAIT_SECS"] = "1"
        stdin = json.dumps(
            {"session_id": SID, "cwd": str(self.cwd), "transcript_path": ""})
        return subprocess.run(
            ["bash", str(HOOK)], input=stdin, env=env, cwd=str(self.cwd),
            capture_output=True, text=True, timeout=120,
        )

    def _write_sentinel(self, payload: dict) -> None:
        self.sentinel.parent.mkdir(parents=True, exist_ok=True)
        self.sentinel.write_text(json.dumps(payload))

    def test_no_sentinel_halts_loudly_and_writes_ok_false(self) -> None:
        cp = self._run()
        self.assertEqual(cp.returncode, 0)  # SessionStart must never break startup
        self.assertIn("⛔ TOOLING_UPDATE=v9.9.9", cp.stdout)  # agent-visible
        self.assertIn("HALT", cp.stdout)
        self.assertIn("HALT", cp.stderr)  # operator-visible too
        written = json.loads(self.sentinel.read_text())
        self.assertFalse(written["ok"])
        self.assertIn("updater-never-ran", written["reason"])
        self.assertEqual(written["session_id"], SID)

    def test_ok_sentinel_for_this_sid_passes(self) -> None:
        self._write_sentinel(
            {"ok": True, "session_id": SID, "requested": "v9.9.9", "tag": "v9.9.9"})
        cp = self._run()
        self.assertEqual(cp.returncode, 0)
        self.assertNotIn("HALT", cp.stdout)

    def test_refusal_sentinel_halts_with_reason(self) -> None:
        self._write_sentinel(
            {"ok": False, "session_id": SID,
             "reason": "dirty-tree: refusing to self-update over uncommitted changes"})
        cp = self._run()
        self.assertIn("HALT", cp.stdout)
        self.assertIn("dirty-tree", cp.stdout)

    def test_stale_sentinel_from_other_startup_halts(self) -> None:
        # A manual pre-launch run (sid "") or an earlier session's sentinel must
        # NOT satisfy the guard — "ran this startup" is the contract.
        self._write_sentinel({"ok": True, "session_id": "", "requested": "v9.9.9"})
        cp = self._run()
        self.assertIn("HALT", cp.stdout)
        # the stale record is replaced by THIS startup's ok:false one, so the
        # orientation ritual refuses on current state, not last week's success
        written = json.loads(self.sentinel.read_text())
        self.assertFalse(written["ok"])
        self.assertEqual(written["session_id"], SID)

    def test_env_unset_no_guard_no_halt(self) -> None:
        cp = self._run(tooling_update=None)
        self.assertEqual(cp.returncode, 0)
        self.assertNotIn("HALT", cp.stdout)
        self.assertFalse(self.sentinel.exists())


if __name__ == "__main__":
    unittest.main()
