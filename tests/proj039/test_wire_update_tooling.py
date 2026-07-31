"""Tests for tools/wire-update-tooling.py — the settings.json SessionStart
wiring patcher/verifier (PROJ-039/T-069, T-065 F1).

settings.json is per-repo (env block), so it joins the sync set structurally:
the patcher must insert the updater at its canonical position (first; last for
trainee), normalise shape idempotently, and leave everything else — env, other
hook events — byte-untouched in structure.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "wire_update_tooling", str(REPO_ROOT / "tools" / "wire-update-tooling.py")
)
wire_update_tooling = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire_update_tooling)  # type: ignore

UPDATER_CMD = 'python3 "$CLAUDE_PROJECT_DIR"/.claude/tools/update-tooling.py'

# The pre-T-069 fleet shape (v1.1.1 repos): substrate hooks wired, no updater,
# per-repo env block — what the one-time delivery PRs start from.
UNWIRED = {
    "env": {"PODZONE_TELEMETRY_REMOTE": "https://example.test/t.git"},
    "hooks": {
        "SessionStart": [
            {"matcher": "startup|resume", "hooks": [
                {"type": "command", "command": 'bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/session-start.sh'},
                {"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/session-materialise.py'},
            ]}
        ],
        "Stop": [
            {"matcher": "", "hooks": [
                {"type": "command", "command": 'bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/stop.sh'}]}
        ],
    },
}


def _commands(settings: dict) -> list[str]:
    return [c["command"] for c in settings["hooks"]["SessionStart"][0]["hooks"]]


class TestWire(unittest.TestCase):
    def test_inserts_first_for_non_trainee(self) -> None:
        settings, changed = wire_update_tooling.wire(json.loads(json.dumps(UNWIRED)), "coder")
        self.assertTrue(changed)
        cmds = _commands(settings)
        self.assertEqual(cmds[0], UPDATER_CMD)
        self.assertEqual(len(cmds), 3)
        first = settings["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertEqual(first["timeout"], 300)

    def test_appends_last_for_trainee(self) -> None:
        settings, changed = wire_update_tooling.wire(json.loads(json.dumps(UNWIRED)), "trainee")
        self.assertTrue(changed)
        self.assertEqual(_commands(settings)[-1], UPDATER_CMD)

    def test_idempotent_and_normalising(self) -> None:
        settings, _ = wire_update_tooling.wire(json.loads(json.dumps(UNWIRED)), "coder")
        again, changed = wire_update_tooling.wire(settings, "coder")
        self.assertFalse(changed)
        # an out-of-position / shape-drifted entry converges to canonical
        drifted = json.loads(json.dumps(UNWIRED))
        drifted["hooks"]["SessionStart"][0]["hooks"].append(
            {"type": "command", "command": UPDATER_CMD})  # wrong slot, no timeout
        fixed, changed = wire_update_tooling.wire(drifted, "coder")
        self.assertTrue(changed)
        self.assertEqual(_commands(fixed)[0], UPDATER_CMD)
        self.assertEqual(_commands(fixed).count(UPDATER_CMD), 1)

    def test_env_and_other_hooks_untouched(self) -> None:
        settings, _ = wire_update_tooling.wire(json.loads(json.dumps(UNWIRED)), "coder")
        self.assertEqual(settings["env"], UNWIRED["env"])
        self.assertEqual(settings["hooks"]["Stop"], UNWIRED["hooks"]["Stop"])


class TestCheck(unittest.TestCase):
    def test_unwired_is_a_defect(self) -> None:
        self.assertIsNotNone(wire_update_tooling.check(json.loads(json.dumps(UNWIRED)), "coder"))

    def test_wired_passes(self) -> None:
        settings, _ = wire_update_tooling.wire(json.loads(json.dumps(UNWIRED)), "coder")
        self.assertIsNone(wire_update_tooling.check(settings, "coder"))

    def test_wrong_position_is_a_defect(self) -> None:
        settings = json.loads(json.dumps(UNWIRED))
        settings["hooks"]["SessionStart"][0]["hooks"].append(
            {"type": "command", "command": UPDATER_CMD, "timeout": 300})
        self.assertIsNotNone(wire_update_tooling.check(settings, "coder"))
        # …but that IS the canonical trainee position
        self.assertIsNone(wire_update_tooling.check(settings, "trainee"))


class TestTraineePython3Guard(unittest.TestCase):
    """PROJ-011/T-125 (CC-519) — the python3 shell guard on the trainee preflight
    command is enforced structurally, exactly like the updater wiring.

    The six already-live trainee repos were scaffolded before T-121, so their
    committed settings.json carries the BARE `python3 .../trainee-preflight.py`
    command: on a machine with no python3 the trainee's first session dies with a
    raw Python error instead of the plain-English message.
    """

    #: The pre-T-121 live-trainee shape (verbatim from home-training-martin).
    UNGUARDED_PREFLIGHT = 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/trainee-preflight.py'

    def _live_trainee_settings(self) -> dict:
        return {
            "env": {"TRAINEE_RUNTIME": "1"},
            "hooks": {
                "SessionStart": [
                    {"matcher": "startup|resume", "hooks": [
                        {"type": "command", "command": self.UNGUARDED_PREFLIGHT},
                        {"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/trainee-finalise.py --guard'},
                        {"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/trainee-session-branch.py'},
                        {"type": "command", "command": UPDATER_CMD, "timeout": 300},
                        {"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/trainee-materialise.py'},
                    ]}
                ],
            },
        }

    def test_check_flags_the_unguarded_live_shape(self) -> None:
        settings = self._live_trainee_settings()
        defect = wire_update_tooling.check(settings, "trainee")
        self.assertIsNotNone(defect)
        self.assertIn("shell guard", defect)

    def test_guard_is_applied_and_idempotent(self) -> None:
        settings = self._live_trainee_settings()
        self.assertTrue(wire_update_tooling.guard_trainee_preflight(settings))
        self.assertIsNone(wire_update_tooling.check(settings, "trainee"))
        # Second pass changes nothing.
        self.assertFalse(wire_update_tooling.guard_trainee_preflight(settings))

    def test_guard_preserves_position_env_and_siblings(self) -> None:
        settings = self._live_trainee_settings()
        wire_update_tooling.guard_trainee_preflight(settings)
        cmds = settings["hooks"]["SessionStart"][0]["hooks"]
        self.assertIn("trainee-preflight.py", cmds[0]["command"])   # still first
        self.assertEqual(settings["env"], {"TRAINEE_RUNTIME": "1"})  # env untouched
        self.assertEqual(cmds[3]["timeout"], 300)                    # siblings intact
        self.assertEqual(len(cmds), 5)                               # nothing added

    def test_guard_command_survives_a_shell_run_without_python3(self) -> None:
        """The whole point: with python3 absent from PATH the command must exit 0
        and print the plain-English message rather than erroring."""
        import shutil
        import subprocess
        cmd = wire_update_tooling.GUARDED_PREFLIGHT_COMMAND
        bash = shutil.which("bash") or "/bin/bash"
        empty_path = tempfile.mkdtemp()  # a PATH with no python3 in it
        r = subprocess.run([bash, "-c", cmd], capture_output=True, text=True,
                           env={"PATH": empty_path, "CLAUDE_PROJECT_DIR": "/nonexistent"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Python 3 is not installed", r.stdout)

    def test_non_trainee_role_is_untouched(self) -> None:
        settings = self._live_trainee_settings()
        self.assertIsNotNone(wire_update_tooling.check(settings, "trainee"))
        # A coder repo has no preflight hook at all -> the guard is a no-op there;
        # check() for a non-trainee role never reports the trainee defect.
        coder = {"hooks": {"SessionStart": [{"matcher": "startup|resume", "hooks": [
            {"type": "command", "command": UPDATER_CMD, "timeout": 300},
        ]}]}}
        self.assertIsNone(wire_update_tooling.check(coder, "coder"))

    def test_guard_string_matches_scaffold_byte_for_byte(self) -> None:
        """Lockstep: the constant here IS what scaffold.sh writes into a fresh
        trainee repo — a drift between them would ship two different guards."""
        import subprocess, os
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "home-training-lockstep"
            subprocess.run(
                ["bash", str(REPO_ROOT / "scaffold.sh"), "podzone", "lockstep", "trainee",
                 "--target-dir", str(target), "--force"],
                cwd=str(REPO_ROOT), check=True, capture_output=True, text=True,
                env={**os.environ, "NO_TELEMETRY_BOOTSTRAP": "1"},
            )
            fresh = json.loads((target / ".claude" / "settings.json").read_text())
            scaffolded = [c["command"] for c in fresh["hooks"]["SessionStart"][0]["hooks"]
                          if "trainee-preflight.py" in c["command"]]
            self.assertEqual(scaffolded, [wire_update_tooling.GUARDED_PREFLIGHT_COMMAND])


class TestCli(unittest.TestCase):
    def test_check_exit_codes_and_patch_roundtrip(self) -> None:
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            path.write_text(json.dumps(UNWIRED))
            tool = str(REPO_ROOT / "tools" / "wire-update-tooling.py")
            base = [sys.executable, tool, "--settings", str(path), "--role", "coder"]
            self.assertEqual(subprocess.run(base + ["--check"], capture_output=True).returncode, 2)
            self.assertEqual(subprocess.run(base, capture_output=True).returncode, 0)
            self.assertEqual(subprocess.run(base + ["--check"], capture_output=True).returncode, 0)
            self.assertEqual(
                subprocess.run([sys.executable, tool, "--settings", str(Path(td) / "missing.json"),
                                "--role", "coder"], capture_output=True).returncode, 1)


if __name__ == "__main__":
    unittest.main()
