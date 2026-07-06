"""Tests for tools/update-tooling.py — brief-gated home-repo self-update
(PROJ-039/T-056).

Real temp git repos throughout (mirrors test_session_guard.py's discipline):
a bare "remote" standing in for agent-tooling (tagged commits with a VERSION
file) and a "home repo" clone with a workspaces/identity/*.identity.yaml. Only
the refusal matrix + tag/role resolution are exercised here — the full
sync-agent-tooling.sh success path is proven live against the real hephaestus
home repo (session E2E proof), since replicating hooks/primitives/lib/skills
byte-identity in a throwaway fixture would just re-test sync-agent-tooling.sh
itself.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

spec = importlib.util.spec_from_file_location(
    "update_tooling", str(REPO_ROOT / "tools" / "update-tooling.py")
)
update_tooling = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update_tooling)  # type: ignore


def _run(*args, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=check)


def _git(repo, *args, check=True):
    return _run("git", "-C", str(repo), *args, check=check)


class _GitFixture(unittest.TestCase):
    """A tagged bare "remote" (VERSION-stamped commits) + a clean home-repo clone."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.remote = base / "remote.git"
        self.home_repo = base / "home"

        _run("git", "init", "--bare", "-b", "main", str(self.remote))

        work = base / "remote-work"
        _run("git", "clone", str(self.remote), str(work))
        for k, v in (("user.email", "t@t"), ("user.name", "T"), ("commit.gpgsign", "false")):
            _git(work, "config", k, v)
        (work / "VERSION").write_text("1.0.0\n")
        (work / "sync-agent-tooling.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        _git(work, "add", "-A")
        _git(work, "commit", "-m", "v1.0.0")
        _git(work, "tag", "v1.0.0")
        (work / "VERSION").write_text("1.1.0\n")
        _git(work, "add", "-A")
        _git(work, "commit", "-m", "v1.1.0")
        _git(work, "tag", "v1.1.0")
        _git(work, "push", "origin", "main", "--tags")

        _run("git", "clone", str(self.remote), str(self.home_repo))
        for k, v in (("user.email", "t@t"), ("user.name", "T"), ("commit.gpgsign", "false")):
            _git(self.home_repo, "config", k, v)
        ident_dir = self.home_repo / "workspaces" / "identity"
        ident_dir.mkdir(parents=True)
        (ident_dir / "t.identity.yaml").write_text("role_class: agenticflows/roles/coder/\n")
        _git(self.home_repo, "add", "-A")
        _git(self.home_repo, "commit", "-m", "identity")

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TestListSemverTags(_GitFixture):
    def test_ascending_semver_order(self) -> None:
        tags = update_tooling.list_semver_tags(str(self.remote))
        self.assertEqual(tags, ["v1.0.0", "v1.1.0"])

    def test_unreachable_remote_raises(self) -> None:
        with self.assertRaises(update_tooling.UpdateRefusal):
            update_tooling.list_semver_tags("/no/such/path.git")


class TestResolveTag(_GitFixture):
    def test_latest_is_highest_semver(self) -> None:
        self.assertEqual(update_tooling.resolve_tag("latest", str(self.remote)), "v1.1.0")

    def test_exact_tag_passthrough(self) -> None:
        self.assertEqual(update_tooling.resolve_tag("v1.0.0", str(self.remote)), "v1.0.0")

    def test_unknown_tag_refused(self) -> None:
        with self.assertRaises(update_tooling.UpdateRefusal):
            update_tooling.resolve_tag("v9.9.9", str(self.remote))


class TestDetectRole(_GitFixture):
    def test_detects_role_from_identity_yaml(self) -> None:
        self.assertEqual(update_tooling.detect_role(str(self.home_repo)), "coder")

    def test_no_identity_yaml_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _run("git", "init", "-b", "main", td)
            with self.assertRaises(update_tooling.UpdateRefusal):
                update_tooling.detect_role(td)


class TestWorkingTreeDirty(_GitFixture):
    def test_clean_tree_is_not_dirty(self) -> None:
        self.assertFalse(update_tooling.working_tree_dirty(str(self.home_repo)))

    def test_ds_store_alone_is_not_dirty(self) -> None:
        (self.home_repo / ".DS_Store").write_text("x")
        self.assertFalse(update_tooling.working_tree_dirty(str(self.home_repo)))

    def test_real_change_is_dirty(self) -> None:
        (self.home_repo / "scratch.txt").write_text("x")
        self.assertTrue(update_tooling.working_tree_dirty(str(self.home_repo)))


class TestRunUpdateRefusals(_GitFixture):
    def test_dirty_tree_refused_before_any_clone(self) -> None:
        (self.home_repo / "scratch.txt").write_text("x")
        with self.assertRaises(update_tooling.UpdateRefusal) as ctx:
            update_tooling.run_update(str(self.home_repo), "coder", "latest", remote=str(self.remote))
        self.assertIn("dirty-tree", str(ctx.exception))
        # refused before touching .workspace/ at all
        self.assertFalse((self.home_repo / ".workspace" / "agent-tooling").exists())

    def test_unknown_tag_refused(self) -> None:
        with self.assertRaises(update_tooling.UpdateRefusal) as ctx:
            update_tooling.run_update(str(self.home_repo), "coder", "v9.9.9", remote=str(self.remote))
        self.assertIn("unknown-tag", str(ctx.exception))

    def test_tag_version_mismatch_refused(self) -> None:
        # Tag the remote v2.0.0 but leave its VERSION file at 1.1.0 (mistag).
        work = Path(self._tmp.name) / "remote-work"
        _git(work, "tag", "v2.0.0")
        _git(work, "push", "origin", "--tags")
        with self.assertRaises(update_tooling.UpdateRefusal) as ctx:
            update_tooling.run_update(str(self.home_repo), "coder", "v2.0.0", remote=str(self.remote))
        self.assertIn("version-mismatch", str(ctx.exception))
        # the mistagged clone must not be left resident
        self.assertFalse((self.home_repo / ".workspace" / "agent-tooling").exists())

    def test_main_is_a_noop_when_env_unset(self) -> None:
        import os
        env = dict(os.environ)
        env.pop("TOOLING_UPDATE", None)
        cp = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "update-tooling.py")],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(cp.returncode, 0)
        self.assertEqual(cp.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
