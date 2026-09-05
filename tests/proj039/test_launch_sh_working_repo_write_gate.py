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

    def test_apply_gitignores_settings_file_when_missing(self):
        """2026-09-04 regression: academy-frontend#137 accidentally committed
        settings.local.json because its .gitignore never excluded it — this
        tool only assumed the file was gitignored, never verified/enforced
        it. `apply` must now append the entry itself."""
        applied = self._tool("--repo", str(self.clone))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        gitignore = self.clone / ".gitignore"
        self.assertTrue(gitignore.exists())
        self.assertIn(".claude/settings.local.json", gitignore.read_text().splitlines())
        # git itself must agree, not just a text match.
        check = _run("git", "-C", str(self.clone), "check-ignore", "-q",
                      ".claude/settings.local.json")
        self.assertEqual(check.returncode, 0)

    def test_apply_does_not_duplicate_an_existing_broader_pattern(self):
        """A repo whose .gitignore already excludes the whole .claude/ dir
        (or the exact path) must not get a redundant second entry appended."""
        (self.clone / ".gitignore").write_text(".claude/\n")
        applied = self._tool("--repo", str(self.clone))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        lines = (self.clone / ".gitignore").read_text().splitlines()
        self.assertEqual(lines, [".claude/"])

    def test_apply_is_idempotent_on_gitignore_across_two_calls(self):
        self._tool("--repo", str(self.clone))
        first = (self.clone / ".gitignore").read_text()
        self._tool("--repo", str(self.clone))
        second = (self.clone / ".gitignore").read_text()
        self.assertEqual(first, second)


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

    def test_gitignore_fix_is_committed_immediately_after_apply(self):
        """2026-09-05 regression: academy-api PR #35 recurred the exact
        .claude/settings.local.json leak this gate exists to prevent, DESPITE
        ensure-local-settings.py's own gitignore-enforcement (already
        verified working in isolation, see TestEnsureLocalSettingsPrimitive
        above). Root cause: the apply call leaves a genuine, tracked
        .gitignore edit sitting UNCOMMITTED on the freshly-staged session
        branch; unlike the untracked settings.local.json file itself (which
        survives a `git checkout .`/`git reset --hard`), that pending
        MODIFICATION does not, so an inner session discarding any unrelated
        local change can silently revert just the gitignore half. launch.sh
        must commit the .gitignore fix on its own, immediately after the
        apply call, before the inner session ever starts -- and it must stay
        inside the same per-working-repo loop this test's sibling
        (test_source_applies_gate_to_each_working_repo) already anchors, not
        deferred to bank_all_repos or some later loop-exit boundary."""
        src = (REPO_ROOT / "tools" / "launch.sh").read_text()
        apply_idx = src.index('ensure-local-settings.py" --repo "${d}"')
        check_idx = src.index('ensure-local-settings.py" --check --repo "${d}"', apply_idx)
        commit_idx = src.index('git -C "${d}" commit -m "chore: gitignore', check_idx)
        push_idx = src.index('git -C "${d}" push', commit_idx)
        self.assertGreater(commit_idx, check_idx)
        self.assertGreater(push_idx, commit_idx)
        # Same loop: the nearest preceding "for r in ...REPOS..." must be the
        # loop this commit/push pair is inside, and the nearest following
        # "done" must close that same loop (i.e. no unrelated loop boundary
        # sits between the gate calls and the commit/push).
        loop_start = src.rindex('for r in "${REPOS[@]:-}"; do', 0, commit_idx)
        loop_end = src.index("\ndone", push_idx)
        self.assertLess(loop_start, check_idx)
        self.assertGreater(loop_end, push_idx)
        # No other "for"/"done" boundary sits between the loop opening and
        # this commit -- i.e. it's the same loop as the gate calls, not a
        # later one that happens to reuse the same REPOS iteration pattern.
        between = src[loop_start + len('for r in "${REPOS[@]:-}"; do'):commit_idx]
        self.assertNotIn("\ndone", between)


if __name__ == "__main__":
    unittest.main()
