"""Regression test for launch.sh's working-repo write-capability gate
(PROJ-039/T-132 follow-up, found live 2026-09-02 — ACP-474/ACP-475
dispatches, home-podzone-hermes).

Before this fix, launch.sh's write-capability gate (`ensure-local-settings.py
--check`) only ever ran against the home repo. Working-repo clones under
`.workspace/` — which this same wrapper clones itself — never got their own
`.claude/settings.local.json`, so headless write capability there was
ambient/undefined rather than guaranteed. Live symptom: one working-repo
clone worked, a sibling clone of the identical shape (fresh git clone, same
wrapper, same session) reproducibly denied every Edit/Write/git-write call
across three separate headless attempts.

Two things are checked:
  1. The underlying primitive (`ensure-local-settings.py`) does what the new
     launch.sh loop now relies on: a fresh clone with no `.claude/` fails
     --check, `apply` (default mode) fixes it, and --check then passes.
  2. launch.sh's source actually contains the new per-working-repo gate loop
     (a structural guard against the fix silently regressing back out).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


class TestEnsureLocalSettingsPrimitive(unittest.TestCase):
    """The primitive launch.sh's new working-repo loop calls."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.clone = Path(self._tmp.name) / "working-repo"
        self.clone.mkdir()
        _run("git", "init", "-b", "main", str(self.clone))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _tool(self, *args):
        return _run(sys.executable, str(REPO_ROOT / "tools" / "ensure-local-settings.py"), *args)

    def test_fresh_clone_fails_check(self):
        res = self._tool("--check", "--repo", str(self.clone))
        self.assertNotEqual(res.returncode, 0)

    def test_apply_then_check_passes(self):
        applied = self._tool("--repo", str(self.clone))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        settings = self.clone / ".claude" / "settings.local.json"
        self.assertTrue(settings.exists())
        self.assertIn("bypassPermissions", settings.read_text())

        checked = self._tool("--check", "--repo", str(self.clone))
        self.assertEqual(checked.returncode, 0, checked.stderr)


class TestLaunchShWorkingRepoGate(unittest.TestCase):
    """Structural guard: the per-working-repo gate loop must exist in
    launch.sh, not just the home-repo gate — this is exactly the gap that
    shipped silently the first time."""

    def test_source_applies_gate_to_each_working_repo(self):
        src = (REPO_ROOT / "tools" / "launch.sh").read_text()
        home_gate_idx = src.index('ensure-local-settings.py" --check --repo "${HOME_REPO_DIR}"')
        working_gate_idx = src.index('ensure-local-settings.py" --repo "${d}"')
        working_check_idx = src.index('ensure-local-settings.py" --check --repo "${d}"')
        # Both the apply and the check must exist, after the home-repo gate,
        # and inside a loop over REPOS (not a one-off).
        self.assertGreater(working_gate_idx, home_gate_idx)
        self.assertGreater(working_check_idx, working_gate_idx)
        loop_start = src.rindex('for r in "${REPOS[@]:-}"; do', 0, working_gate_idx)
        self.assertLess(loop_start, working_gate_idx)


if __name__ == "__main__":
    unittest.main()
