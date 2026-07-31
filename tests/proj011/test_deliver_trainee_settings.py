"""Tests for tools/deliver-trainee-settings.py — the one-time delivery pass that
carries the structural settings.json fixes to the ALREADY-LIVE trainee repos
(PROJ-011/T-125, CC-519).

The mechanism (wire-update-tooling.py) is covered by
tests/proj039/test_wire_update_tooling.py. What is tested here is the delivery
wrapper: it must be genuinely read-only in `--dry-run` (the repos belong to
trainees and an operator will run this against real remotes), it must be
idempotent, and it must leave everything other than the hook command strings
alone.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "deliver-trainee-settings.py"

# The pre-T-121 shape carried by every live trainee repo (verbatim from
# home-training-martin's committed settings.json).
LIVE_SETTINGS = {
    "env": {"TRAINEE_RUNTIME": "1"},
    "hooks": {
        "SessionStart": [
            {"matcher": "startup|resume", "hooks": [
                {"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/trainee-preflight.py'},
                {"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/trainee-finalise.py --guard'},
                {"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/trainee-session-branch.py'},
                {"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/tools/update-tooling.py', "timeout": 300},
                {"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/trainee-materialise.py'},
            ]}
        ],
        "Stop": [
            {"matcher": "", "hooks": [
                {"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/trainee-telemetry.py'},
            ]}
        ],
    },
}


def _fake_trainee_repo(root: Path) -> Path:
    repo = root / "home-training-fake"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.json").write_text(json.dumps(LIVE_SETTINGS, indent=2) + "\n")
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True, capture_output=True)
    return repo


def _run(repo: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), "--local-clone", str(repo), *flags],
                          capture_output=True, text=True)


class TestDeliverTraineeSettings(unittest.TestCase):

    def test_dry_run_reports_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = _fake_trainee_repo(Path(td))
            before = (repo / ".claude" / "settings.json").read_text()
            cp = _run(repo, "--dry-run")
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertIn("would-patch", cp.stdout)
            self.assertEqual((repo / ".claude" / "settings.json").read_text(), before,
                             "--dry-run must leave the repo byte-untouched")
            branches = subprocess.run(["git", "-C", str(repo), "branch", "--list"],
                                      capture_output=True, text=True).stdout
            self.assertNotIn("t125", branches, "--dry-run must not create a branch")

    def test_dry_run_shows_the_guard_in_the_diff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = _fake_trainee_repo(Path(td))
            cp = _run(repo, "--dry-run")
            self.assertIn("Python 3 is not installed", cp.stdout)

    def test_already_guarded_repo_is_reported_ok(self) -> None:
        """Idempotence: the second pass over a repo has nothing to do."""
        with tempfile.TemporaryDirectory() as td:
            repo = _fake_trainee_repo(Path(td))
            # Apply the patcher directly (the delivery tool's own write path needs
            # a remote), then re-run the delivery check.
            subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "wire-update-tooling.py"),
                            "--settings", str(repo / ".claude" / "settings.json"),
                            "--role", "trainee"], check=True, capture_output=True)
            cp = _run(repo, "--dry-run")
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertIn("ok", cp.stdout.split("\n")[0])
            self.assertNotIn("would-patch", cp.stdout)

    def test_patch_changes_only_the_hook_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = _fake_trainee_repo(Path(td))
            settings = repo / ".claude" / "settings.json"
            subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "wire-update-tooling.py"),
                            "--settings", str(settings), "--role", "trainee"],
                           check=True, capture_output=True)
            after = json.loads(settings.read_text())

            def norm(d: dict) -> dict:
                d = json.loads(json.dumps(d))
                for e in d["hooks"]["SessionStart"][0]["hooks"]:
                    if "trainee-preflight" in e["command"]:
                        e["command"] = "PREFLIGHT"
                return d

            self.assertEqual(norm(after), norm(LIVE_SETTINGS),
                             "only the preflight command string may change")
            self.assertEqual(after["env"], {"TRAINEE_RUNTIME": "1"})

    def test_live_repo_list_is_the_six_onboarded_trainees(self) -> None:
        """The list is explicit on purpose — a wildcard org sweep would rewrite
        the template and any future repo without a decision."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("deliver", str(TOOL))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore
        self.assertEqual(len(mod.LIVE_TRAINEE_REPOS), 6)
        self.assertNotIn("home-training-template", mod.LIVE_TRAINEE_REPOS)
        for name in mod.LIVE_TRAINEE_REPOS:
            self.assertTrue(name.startswith("home-training-"))


if __name__ == "__main__":
    unittest.main()
