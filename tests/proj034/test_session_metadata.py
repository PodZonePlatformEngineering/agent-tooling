"""Tests for lib/session_metadata.resolve()."""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

# Add repo root to path so `import lib...` works regardless of cwd.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import session_metadata  # noqa: E402


def _write_identity(dir_: Path, name: str, agent: str, *, home_repo: str | None = None,
                    repos: list[str] | None = None, workspace: str | None = None) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    lines = [f"agent: {agent}"]
    if home_repo:
        lines.append(f"home_repo: {home_repo}")
    if workspace:
        lines.append(f"workspace: {workspace}")
    if repos:
        lines.append("repos:")
        for r in repos:
            lines.append(f"  - name: {r}")
    (dir_ / f"{name}.identity.yaml").write_text("\n".join(lines) + "\n")


class TestResolve(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "sessions").mkdir()
        (self.home / "workspace").mkdir()

        # Patch HOME + identity dirs so tests are hermetic.
        self._patches = [
            mock.patch.object(session_metadata, "HOME", self.home),
            mock.patch.object(
                session_metadata,
                "IDENTITY_DIRS",
                (
                    self.home / "workspace/podzoneAgentTeam/workspaces/identity",
                    self.home / "workspace/trainingTeam/workspaces/identity",
                    self.home / "workspace/roadmapTeam/workspaces/identity",
                ),
            ),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    # T1: cwd matches ~/sessions/{agent}-YYYY-MM-DD-*
    def test_session_dir_agent_match(self) -> None:
        cwd = self.home / "sessions/hephaestus-2026-05-20-proj034-foundation/agent-tooling"
        out = session_metadata.resolve(cwd=str(cwd))
        self.assertEqual(out["agent"], "hephaestus")
        self.assertEqual(out["workspace"], "agent-tooling")

    # T2: identity file lookup by home_repo
    def test_identity_file_lookup(self) -> None:
        ident = self.home / "workspace/podzoneAgentTeam/workspaces/identity"
        _write_identity(ident, "martin-hephaestus-agent-tooling", "Hephaestus",
                        home_repo="agent-tooling",
                        workspace="martin-hephaestus-agent-tooling")
        cwd = self.home / "workspace/agent-tooling"
        out = session_metadata.resolve(cwd=str(cwd))
        self.assertEqual(out["agent"], "hephaestus")
        self.assertEqual(out["workspace"], "agent-tooling")

    # T3: no matching identity → agent=None
    def test_no_identity_match(self) -> None:
        cwd = self.home / "workspace/some-unmapped-repo"
        out = session_metadata.resolve(cwd=str(cwd))
        self.assertIsNone(out["agent"])
        self.assertEqual(out["workspace"], "some-unmapped-repo")

    # T4: jsonl_path only — cwd decoded from parent dir name
    def test_jsonl_path_decoding(self) -> None:
        jsonl = Path("/tmp/-Users-martincolley-workspace-podzoneAgentTeam/abc.jsonl")
        out = session_metadata.resolve(jsonl_path=jsonl)
        self.assertEqual(out["cwd"], "/Users/martincolley/workspace/podzoneAgentTeam")
        self.assertEqual(out["workspace"], "podzoneAgentTeam")

    # T5: trailing slash on cwd → normalised
    def test_trailing_slash_normalised(self) -> None:
        cwd = str(self.home / "workspace/agent-tooling") + "/"
        out = session_metadata.resolve(cwd=cwd)
        self.assertFalse(out["cwd"].endswith("/"))
        self.assertEqual(out["workspace"], "agent-tooling")

    # T6: multiple identity files match → deterministic pick
    def test_multiple_matches_deterministic(self) -> None:
        ident = self.home / "workspace/podzoneAgentTeam/workspaces/identity"
        _write_identity(ident, "alpha-claim-tooling", "Alpha",
                        home_repo="agent-tooling",
                        workspace="alpha-claim-tooling")
        _write_identity(ident, "beta-claim-tooling", "Beta",
                        home_repo="agent-tooling",
                        workspace="beta-claim-tooling")
        cwd = self.home / "workspace/agent-tooling"
        out = session_metadata.resolve(cwd=str(cwd))
        # alphabetical order picks alpha-*
        self.assertEqual(out["agent"], "alpha")

    def test_requires_input(self) -> None:
        with self.assertRaises(ValueError):
            session_metadata.resolve()

    def test_user_root_jsonl_dir(self) -> None:
        # ~/.claude/projects/-Users-martincolley/ → cwd /Users/martincolley
        jsonl = Path("/tmp/-Users-martincolley/abc.jsonl")
        out = session_metadata.resolve(jsonl_path=jsonl)
        self.assertEqual(out["cwd"], "/Users/martincolley")
        self.assertEqual(out["workspace"], "martincolley")
        self.assertIsNone(out["agent"])


if __name__ == "__main__":
    unittest.main()
