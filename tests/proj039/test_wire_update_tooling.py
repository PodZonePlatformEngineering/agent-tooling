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
