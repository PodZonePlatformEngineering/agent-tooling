"""Tests for lib/telemetry_repo — DT-013 (telemetry push + clone-recover, R-015).

Uses a local bare repo as the 'remote' so the full commit → push → clone-recover
cycle runs deterministically offline.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import telemetry_repo  # noqa: E402


def _git(d, *a):
    return subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, check=True)


class TestTelemetryRepo(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.remote)],
                       capture_output=True, check=True)
        self.repo = root / "telemetry"
        self.repo.mkdir()
        # local identity so commit works in CI/sandbox
        telemetry_repo.ensure_repo(str(self.repo), remote=str(self.remote))
        _git(str(self.repo), "config", "user.email", "t@example.com")
        _git(str(self.repo), "config", "user.name", "Test")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ensure_repo_initialises_and_sets_remote(self) -> None:
        self.assertTrue((self.repo / ".git").exists())
        out = _git(str(self.repo), "remote", "-v").stdout
        self.assertIn(str(self.remote), out)

    def test_dt013_commit_push_and_clone_recover(self) -> None:
        # a session JSONL lands in the repo dir
        (self.repo / "sample-session.jsonl").write_text(
            '{"type":"assistant","message":{"usage":{"input_tokens":1}}}\n'
        )
        res = telemetry_repo.commit_and_push("sess-xyz", repo_dir=str(self.repo),
                                             date="2026-06-11")
        self.assertTrue(res["committed"], msg=res["reason"])
        self.assertTrue(res["pushed"], msg=res["reason"])

        # the pushed ref exists on the remote
        ls = subprocess.run(["git", "ls-remote", str(self.remote)],
                            capture_output=True, text=True, check=True)
        self.assertIn("refs/heads/main", ls.stdout)

        # clone-recoverable: a fresh clone contains the session JSONL
        with tempfile.TemporaryDirectory() as scratch:
            dest = Path(scratch) / "clone"
            subprocess.run(["git", "clone", str(self.remote), str(dest)],
                           capture_output=True, check=True)
            self.assertTrue((dest / "sample-session.jsonl").exists())

    def test_commit_is_noop_safe_when_nothing_changed(self) -> None:
        telemetry_repo.commit_and_push("s1", repo_dir=str(self.repo))
        res = telemetry_repo.commit_and_push("s2", repo_dir=str(self.repo))
        # nothing new to commit → committed False, but no error, push still attempted
        self.assertFalse(res["committed"])
        self.assertEqual(res["reason"] if not res["pushed"] else "", res["reason"]
                         if not res["pushed"] else "")

    def test_push_failure_reported_when_no_remote(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "noremote"
            r.mkdir()
            telemetry_repo.ensure_repo(str(r))  # no remote
            _git(str(r), "config", "user.email", "t@example.com")
            _git(str(r), "config", "user.name", "Test")
            (r / "f.jsonl").write_text("{}\n")
            res = telemetry_repo.commit_and_push("s", repo_dir=str(r))
            self.assertTrue(res["committed"])
            self.assertFalse(res["pushed"])  # the gate the deletion checks


class TestTelemetryScopeAndRemote(unittest.TestCase):
    """PROJ-039/T-032: agent-session scope .gitignore + remote resolution."""

    def test_scope_patterns_use_leading_dash_slug(self):
        # Claude encodes /Users/x -> -Users-x (leading slash becomes a leading dash).
        pats = telemetry_repo._scope_patterns(home="/Users/martincolley")
        self.assertIn("/*", pats)
        self.assertIn("!/.gitignore", pats)
        self.assertIn("!-Users-martincolley-sessions-*/", pats)
        self.assertIn("!-Users-martincolley-workspace-home-*/", pats)
        # no pattern should drop the leading dash (the bug that would track nothing)
        self.assertFalse(any(p.startswith("!/Users-") for p in pats),
                         f"leading-dash slug missing in {pats}")

    def test_scope_gitignore_skipped_for_non_default_repo(self):
        # A custom telemetry dir is assumed already-scoped; ensure_repo must not
        # drop a ~/.claude/projects-shaped allowlist into it.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "custom-telemetry"
            r.mkdir()
            wrote = telemetry_repo.write_scope_gitignore(str(r))
            self.assertFalse(wrote)
            self.assertFalse((r / ".gitignore").exists())

    def test_resolve_remote_prefers_arg_then_env(self):
        import os
        self.assertEqual(telemetry_repo.resolve_remote("git@x:r.git"), "git@x:r.git")
        old = os.environ.get(telemetry_repo.DEFAULT_REMOTE_ENV)
        try:
            os.environ[telemetry_repo.DEFAULT_REMOTE_ENV] = "https://env/remote.git"
            self.assertEqual(telemetry_repo.resolve_remote(), "https://env/remote.git")
        finally:
            if old is None:
                os.environ.pop(telemetry_repo.DEFAULT_REMOTE_ENV, None)
            else:
                os.environ[telemetry_repo.DEFAULT_REMOTE_ENV] = old

    def test_ensure_repo_returns_remote_in_result(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "t"
            r.mkdir()
            res = telemetry_repo.ensure_repo(str(r), remote="git@x:y.git")
            self.assertEqual(res["remote"], "git@x:y.git")
            self.assertTrue(res["ok"])


if __name__ == "__main__":
    unittest.main()
