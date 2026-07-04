"""T-050 (CC-355) — hook commands must be cwd-independent (`$CLAUDE_PROJECT_DIR`).

Defect (Athena T-007, first serial simple-repo run): the shell ended with cwd in
``memory/`` and the resident SessionEnd command (`python3 .claude/hooks/…` —
**relative**) resolved against that cwd, so finalise never ran (no brief stamp, no
return-to-main, locks held). Serial mode makes this a standing hazard — agents
``cd`` freely in the primary clone.

Two guards here:
  * **Mechanism** (dynamic): the mandated command form
    ``<runner> "$CLAUDE_PROJECT_DIR"/.claude/hooks/<hook>`` executes the hook even
    when the shell cwd is a *subdirectory* of the project — and the old relative
    form does NOT (it fails to resolve). This is the brief's regression:
    "fire SessionEnd (and SessionStart) with cwd set to a repo subdirectory;
    assert the hook executes."
  * **Regression scan** (static): no canonical settings source (scaffold.sh
    role_settings_json, the launch-session skill's emitted snippet) may reintroduce
    a project-relative ``.claude/hooks/`` command.
"""

from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAFFOLD = REPO_ROOT / "scaffold.sh"
LAUNCH_SKILL = REPO_ROOT / "skills" / "launch-session" / "SKILL.md"

# The exact command forms T-050 mandates, one per runner.
SESSION_END_CMD = 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/probe.py'
SESSION_START_CMD = 'bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/probe.sh'
# The defective forms, for the negative assertion.
SESSION_END_REL = "python3 .claude/hooks/probe.py"
SESSION_START_REL = "bash .claude/hooks/probe.sh"


def _make_project(tmp: Path) -> tuple[Path, Path, Path]:
    """A synthetic project: root/.claude/hooks/{probe.py,probe.sh} + a subdir.

    Each probe writes a marker file (whose existence proves the hook ran) into a
    location that does NOT depend on cwd, then exits 0.
    """
    root = tmp / "proj"
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    subdir = root / "memory"
    subdir.mkdir()
    marker_py = root / "ran-py.marker"
    marker_sh = root / "ran-sh.marker"
    (hooks / "probe.py").write_text(
        "import pathlib, os\n"
        f"pathlib.Path(r'{marker_py}').write_text('py')\n",
        encoding="utf-8",
    )
    (hooks / "probe.sh").write_text(
        "#!/usr/bin/env bash\n"
        f"echo sh > '{marker_sh}'\n",
        encoding="utf-8",
    )
    return root, marker_py, marker_sh


def _run_cmd(cmd: str, *, cwd: Path, project_dir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    return subprocess.run(
        ["bash", "-c", cmd], cwd=str(cwd), env=env,
        input="{}", capture_output=True, text=True, timeout=30,
    )


class TestMechanismFromSubdir(unittest.TestCase):
    def test_absolute_form_runs_from_subdir(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root, marker_py, marker_sh = _make_project(Path(td))
            subdir = root / "memory"
            # SessionEnd (python3) — cwd is a subdirectory of the project.
            p = _run_cmd(SESSION_END_CMD, cwd=subdir, project_dir=root)
            self.assertEqual(p.returncode, 0, msg=p.stderr)
            self.assertTrue(marker_py.exists(), "SessionEnd hook did not run from subdir")
            # SessionStart (bash) — same.
            p = _run_cmd(SESSION_START_CMD, cwd=subdir, project_dir=root)
            self.assertEqual(p.returncode, 0, msg=p.stderr)
            self.assertTrue(marker_sh.exists(), "SessionStart hook did not run from subdir")

    def test_relative_form_fails_from_subdir(self) -> None:
        """Proves the regression is real: the old relative form does NOT resolve
        when the shell cwd is a subdirectory."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root, marker_py, marker_sh = _make_project(Path(td))
            subdir = root / "memory"
            p = _run_cmd(SESSION_END_REL, cwd=subdir, project_dir=root)
            self.assertNotEqual(p.returncode, 0)
            self.assertFalse(marker_py.exists())
            p = _run_cmd(SESSION_START_REL, cwd=subdir, project_dir=root)
            self.assertNotEqual(p.returncode, 0)
            self.assertFalse(marker_sh.exists())

    def test_absolute_form_still_runs_from_root(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root, marker_py, _ = _make_project(Path(td))
            p = _run_cmd(SESSION_END_CMD, cwd=root, project_dir=root)
            self.assertEqual(p.returncode, 0, msg=p.stderr)
            self.assertTrue(marker_py.exists())


# A hook `command` string that runs a project-resident hook by RELATIVE path —
# the exact defect. Matches e.g. `"command": "python3 .claude/hooks/x.py"`.
_RELATIVE_HOOK_CMD = re.compile(
    r'"command":\s*"(?:bash|python3)\s+\.claude/hooks/'
)


class TestNoRelativeHookCommands(unittest.TestCase):
    def _scan(self, path: Path) -> list[str]:
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if _RELATIVE_HOOK_CMD.search(line)
        ]

    def test_scaffold_role_settings_absolute(self) -> None:
        offenders = self._scan(SCAFFOLD)
        self.assertEqual(offenders, [], f"relative hook commands in scaffold.sh: {offenders}")

    def test_launch_skill_snippet_absolute(self) -> None:
        offenders = self._scan(LAUNCH_SKILL)
        self.assertEqual(offenders, [], f"relative hook commands in launch-session skill: {offenders}")

    def test_scaffold_uses_project_dir(self) -> None:
        """Positive check: every scaffold settings hook `command` that runs a
        project-resident `.claude/hooks/` script is `$CLAUDE_PROJECT_DIR`-anchored.
        (Line-based: scaffold emits the JSON with escaped quotes, which defeats a
        single-string regex.)"""
        lines = [
            ln for ln in SCAFFOLD.read_text(encoding="utf-8").splitlines()
            if '"command":' in ln and ".claude/hooks/" in ln
        ]
        self.assertTrue(lines, "no hook command lines found in scaffold.sh")
        for ln in lines:
            self.assertIn("CLAUDE_PROJECT_DIR", ln, f"not project-dir-anchored: {ln.strip()}")


if __name__ == "__main__":
    unittest.main()
