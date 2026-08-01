"""Tests for tools/ensure-local-settings.py (PROJ-039/T-132).

The headless write-capability posture tool: merge-not-clobber apply into a
clone's .claude/settings.local.json, and the --check gate /launch-session
headless prep runs before emitting a launch command.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[2] / "tools" / "ensure-local-settings.py"

# The Athena shape the brief requires merges to preserve: hooks + _comment +
# permissions.allow, no defaultMode.
ATHENA_SHAPE = {
    "hooks": {
        "SessionStart": [
            {"matcher": "startup|resume",
             "hooks": [{"type": "command",
                        "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/session-materialise.py"}]}
        ]
    },
    "_comment": "Migrated-launch wiring (brief-first, PROJ-039/T-043).",
    "permissions": {"allow": ["mcp__secrets__secret_run"]},
}


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True)


class LocalSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.path = self.repo / ".claude" / "settings.local.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, data) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(data if isinstance(data, str) else json.dumps(data))

    def read(self) -> dict:
        return json.loads(self.path.read_text())

    # --- apply ---

    def test_apply_creates_file_when_absent(self) -> None:
        res = run_tool("--repo", str(self.repo))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.read()["permissions"]["defaultMode"],
                         "bypassPermissions")

    def test_apply_merges_not_clobbers_athena_shape(self) -> None:
        self.write(ATHENA_SHAPE)
        res = run_tool("--repo", str(self.repo))
        self.assertEqual(res.returncode, 0, res.stderr)
        out = self.read()
        self.assertEqual(out["permissions"]["defaultMode"], "bypassPermissions")
        # every pre-existing key survives
        self.assertEqual(out["permissions"]["allow"], ["mcp__secrets__secret_run"])
        self.assertEqual(out["hooks"], ATHENA_SHAPE["hooks"])
        self.assertEqual(out["_comment"], ATHENA_SHAPE["_comment"])

    def test_apply_is_idempotent(self) -> None:
        self.write(ATHENA_SHAPE)
        run_tool("--repo", str(self.repo))
        first = self.path.read_text()
        res = run_tool("--repo", str(self.repo))
        self.assertEqual(res.returncode, 0)
        self.assertIn("already grants", res.stdout)
        self.assertEqual(self.path.read_text(), first)

    def test_apply_refuses_invalid_json(self) -> None:
        self.write("{not json")
        res = run_tool("--repo", str(self.repo))
        self.assertEqual(res.returncode, 2)
        self.assertIn("not valid JSON", res.stderr)
        self.assertEqual(self.path.read_text(), "{not json")  # untouched

    def test_apply_result_parses_with_json_tool(self) -> None:
        self.write(ATHENA_SHAPE)
        run_tool("--repo", str(self.repo))
        res = subprocess.run([sys.executable, "-m", "json.tool", str(self.path)],
                             capture_output=True)
        self.assertEqual(res.returncode, 0)

    # --- check ---

    def test_check_fails_loud_when_missing(self) -> None:
        res = run_tool("--repo", str(self.repo), "--check")
        self.assertEqual(res.returncode, 1)
        self.assertIn("HALT", res.stderr)

    def test_check_fails_on_allow_only_shape(self) -> None:
        self.write(ATHENA_SHAPE)
        res = run_tool("--repo", str(self.repo), "--check")
        self.assertEqual(res.returncode, 1)
        self.assertIn("HALT", res.stderr)

    def test_check_passes_on_bypass(self) -> None:
        self.write({"permissions": {"defaultMode": "bypassPermissions"}})
        res = run_tool("--repo", str(self.repo), "--check")
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_check_passes_on_sufficient_allow_list(self) -> None:
        self.write({"permissions": {"allow": ["Write", "Edit", "Bash"]}})
        res = run_tool("--repo", str(self.repo), "--check")
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_check_fails_when_bypass_but_write_denied(self) -> None:
        self.write({"permissions": {"defaultMode": "bypassPermissions",
                                    "deny": ["Write"]}})
        res = run_tool("--repo", str(self.repo), "--check")
        self.assertEqual(res.returncode, 1)

    def test_check_never_writes(self) -> None:
        run_tool("--repo", str(self.repo), "--check")
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
