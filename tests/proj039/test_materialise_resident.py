"""T-052 (CC-362) — session-materialise.py is committed-resident, and a botched
sync fails LOUDLY instead of silently skipping.

Defect (Thoth T-022, first serial launch): materialise was hand-copied + wired via
``settings.local.json``; the launch script omitted the copy, the SessionStart wiring
pointed at a missing file and skipped **silently** → no brief-first session point,
``session_ids[]`` never appended, finalise ran "not brief-first" (brief left
``approved`` despite ``Brief-Status: complete``), no result PR until manual repair.

Two guards here:
  * **Resident + committed-wired** — ``session-materialise.py`` is in the synced hook
    set for every migrated role, and its SessionStart wiring is in the committed
    ``settings.json`` (``$CLAUDE_PROJECT_DIR``-anchored) for all non-trainee roles.
    The trainee wires it via UserPromptSubmit (first-prompt-brief), not SessionStart.
  * **Loud fail** — ``session-start.sh`` (always resident) HALTs loudly (ok:false
    sentinel + stderr) when ``BRIEF_ID`` is set but the materialise hook file is
    missing, so an incomplete sync can never fail silently again.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAFFOLD = REPO_ROOT / "scaffold.sh"
SESSION_START = REPO_ROOT / "hooks" / "session-start.sh"

MIGRATED_ROLES = ("team-lead", "coder", "archivist", "trainer", "cluster-operator",
                  "curriculum-developer", "historian", "strategist", "trainee")


def _scaffold(role: str, dest: Path) -> None:
    subprocess.run(
        ["bash", str(SCAFFOLD), "podzone", "testagent", role, "--target-dir", str(dest)],
        capture_output=True, text=True, check=True,
    )


def _session_start_hooks(settings: dict) -> list[str]:
    entries = settings["hooks"]["SessionStart"]
    cmds = []
    for entry in entries:
        for h in entry["hooks"]:
            cmds.append(h["command"].split("/")[-1])
    return cmds


class TestResidentWiring(unittest.TestCase):
    def test_materialise_resident_and_wired_for_every_migrated_role(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            for role in MIGRATED_ROLES:
                dest = Path(td) / role
                _scaffold(role, dest)
                # The hook file is resident for every role (committed, synced).
                self.assertTrue(
                    (dest / ".claude" / "hooks" / "session-materialise.py").exists(),
                    f"{role}: session-materialise.py not resident")
                settings = json.loads(
                    (dest / ".claude" / "settings.json").read_text())
                ss = _session_start_hooks(settings)
                if role == "trainee":
                    # The trainee wires materialise via UserPromptSubmit, not SessionStart.
                    self.assertNotIn("session-materialise.py", ss,
                                     "trainee must not wire materialise on SessionStart")
                    ups = [h["command"].split("/")[-1]
                           for e in settings["hooks"]["UserPromptSubmit"]
                           for h in e["hooks"]]
                    self.assertIn("first-prompt-brief.py", ups)
                else:
                    self.assertIn("session-materialise.py", ss,
                                  f"{role}: materialise not wired on SessionStart")
                # Every wired SessionStart command is $CLAUDE_PROJECT_DIR-anchored (T-050).
                for entry in settings["hooks"]["SessionStart"]:
                    for h in entry["hooks"]:
                        self.assertIn("$CLAUDE_PROJECT_DIR", h["command"],
                                      f"{role}: SessionStart cmd not project-dir-anchored")

    def test_no_settings_local_json_wiring_emitted(self) -> None:
        """The launch-session skill must no longer emit a settings.local.json that
        WIRES materialise on SessionStart (env-only is fine) — that was the load-
        bearing file the copy step forgot."""
        skill = (REPO_ROOT / "skills" / "launch-session" / "SKILL.md").read_text()
        # No settings.local.json hooks-block wiring materialise remains in the skill.
        self.assertNotIn('"command": "python3 \\"$CLAUDE_PROJECT_DIR\\"/.claude/hooks/'
                         'session-materialise.py"', skill.replace("\\n", ""),
                         "skill still emits a materialise hook command (settings.local wiring)")


class TestLoudFailOnMissingHook(unittest.TestCase):
    """session-start.sh (always resident) HALTs loudly when BRIEF_ID is set but the
    materialise hook file is missing — the silent-skip class the defect exposed."""

    def _run_session_start(self, hooks_dir: Path, cwd: Path, *, brief_id: str | None):
        env = dict(os.environ)
        env.pop("PODZONE_QDRANT_APIKEY", None)
        if brief_id is not None:
            env["BRIEF_ID"] = brief_id
        else:
            env.pop("BRIEF_ID", None)
        stdin = json.dumps({"session_id": "s-1", "cwd": str(cwd), "transcript_path": ""})
        return subprocess.run(
            ["bash", str(hooks_dir / "session-start.sh")],
            input=stdin, env=env, capture_output=True, text=True, timeout=60,
        )

    def _hooks_dir(self, base: Path, *, with_materialise: bool) -> Path:
        hooks = base / "hooks"
        hooks.mkdir()
        shutil.copy(SESSION_START, hooks / "session-start.sh")
        # primitives/log_primitive.sh is always resident alongside hooks/ in a real
        # repo (session-start.sh sources it); mirror that so the test is faithful.
        prims = base / "primitives"
        prims.mkdir()
        shutil.copy(REPO_ROOT / "primitives" / "log_primitive.sh",
                    prims / "log_primitive.sh")
        if with_materialise:
            (hooks / "session-materialise.py").write_text("# stub\n")
        return hooks

    def test_missing_materialise_with_brief_id_halts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            hooks = self._hooks_dir(base, with_materialise=False)
            cwd = base / "proj"
            cwd.mkdir()
            p = self._run_session_start(hooks, cwd, brief_id="podzone/2026-07-05-x")
            self.assertEqual(p.returncode, 0, msg=p.stderr)  # best-effort: never breaks startup
            self.assertIn("session-materialise.py MISSING", p.stderr)
            sentinel = cwd / ".workspace" / ".materialise-status.json"
            self.assertTrue(sentinel.exists(), "no halt sentinel written")
            status = json.loads(sentinel.read_text())
            self.assertFalse(status["ok"])
            self.assertEqual(status["reason"], "materialise-hook-missing")

    def test_present_materialise_with_brief_id_does_not_halt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            hooks = self._hooks_dir(base, with_materialise=True)
            cwd = base / "proj"
            cwd.mkdir()
            p = self._run_session_start(hooks, cwd, brief_id="podzone/2026-07-05-x")
            self.assertNotIn("MISSING", p.stderr)
            # No false halt sentinel from session-start.sh (materialise itself owns it).
            sentinel = cwd / ".workspace" / ".materialise-status.json"
            self.assertFalse(sentinel.exists())

    def test_no_brief_id_never_halts_even_if_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            hooks = self._hooks_dir(base, with_materialise=False)
            cwd = base / "proj"
            cwd.mkdir()
            p = self._run_session_start(hooks, cwd, brief_id=None)
            self.assertNotIn("MISSING", p.stderr)
            self.assertFalse((cwd / ".workspace" / ".materialise-status.json").exists())


if __name__ == "__main__":
    unittest.main()
