"""Tests for tools/backfill-prompt-logs.py."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _load():
    spec = importlib.util.spec_from_file_location(
        "backfill_prompt_logs", REPO_ROOT / "tools" / "backfill-prompt-logs.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


bpl = _load()

SID_X = "77777777-7777-4777-8777-777777777777"


def _build_jsonl(path: Path, cwd: str, user_texts: list, extras: list = None) -> None:
    """user_texts: list of either str or list-of-content-blocks. Empty string = empty turn."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for text in user_texts:
            fh.write(
                json.dumps(
                    {
                        "type": "user",
                        "cwd": cwd,
                        "timestamp": "2026-05-20T10:00:00.000Z",
                        "message": {"role": "user", "content": text},
                    }
                )
                + "\n"
            )
        # Non-user entries should be ignored
        fh.write(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-05-20T10:00:01.000Z",
                    "message": {"model": "claude-opus-4-7", "content": "reply"},
                }
            )
            + "\n"
        )
        for e in extras or []:
            fh.write(json.dumps(e) + "\n")


class TestExtractTurns(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.cwd = "/Users/fake/workspace/foo"
        self.jsonl = self.tmp / f"{SID_X}.jsonl"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # T19: parses user turns; non-user entries ignored
    def test_t19_user_only(self) -> None:
        _build_jsonl(
            self.jsonl,
            self.cwd,
            ["first turn", "second turn"],
        )
        turns = bpl._extract_user_turns(self.jsonl)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["text"], "first turn")
        self.assertEqual(turns[0]["turn_number"], 0)
        self.assertEqual(turns[1]["turn_number"], 1)

    # T23: empty content turn is skipped
    def test_t23_empty_skipped(self) -> None:
        _build_jsonl(self.jsonl, self.cwd, ["non-empty", ""])
        turns = bpl._extract_user_turns(self.jsonl)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["text"], "non-empty")


class TestProcessFile(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.cwd = "/Users/fake/workspace/foo"
        encoded = self.cwd.replace("/", "-")
        self.jsonl = self.tmp / "projects" / encoded / f"{SID_X}.jsonl"
        _build_jsonl(self.jsonl, self.cwd, ["hello", "world"])
        # Patch home so resolve() uses tmp
        self.home_patch = patch.object(
            bpl.Path, "home", staticmethod(lambda: self.tmp)
        )
        self.home_patch.start()
        self.env_patch = patch.dict(
            os.environ, {"PODZONE_QDRANT_APIKEY": "test-key"}, clear=False
        )
        self.env_patch.start()

    def tearDown(self) -> None:
        self.home_patch.stop()
        self.env_patch.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # T20: payload shape matches existing hook
    def test_t20_payload_shape(self) -> None:
        captured = []

        def fake_upsert(pid, vec, payload):
            captured.append((pid, payload))
            return True

        with patch.object(bpl, "_embed", return_value=[0.0] * 768), \
                patch.object(bpl, "_upsert", side_effect=fake_upsert):
            bpl.process_file(self.jsonl)
        self.assertEqual(len(captured), 2)
        _, payload = captured[0]
        for k in ("session_id", "agent", "turn_number", "timestamp", "prompt_text", "cwd", "workspace"):
            self.assertIn(k, payload)
        self.assertEqual(payload["session_id"], SID_X)
        self.assertEqual(payload["turn_number"], 0)

    # T21: point ID is deterministic and matches existing hook format
    def test_t21_point_id_deterministic(self) -> None:
        captured = []

        def fake_upsert(pid, vec, payload):
            captured.append(pid)
            return True

        with patch.object(bpl, "_embed", return_value=[0.0] * 768), \
                patch.object(bpl, "_upsert", side_effect=fake_upsert):
            bpl.process_file(self.jsonl)
            bpl.process_file(self.jsonl)
        # Two runs → same IDs
        self.assertEqual(len(captured), 4)
        self.assertEqual(captured[0], captured[2])
        self.assertEqual(captured[1], captured[3])
        # And matches the existing hook's format
        expected = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{SID_X}:0"))
        self.assertEqual(captured[0], expected)

    # T22: --dry-run skips embedding and upsert
    def test_t22_dry_run(self) -> None:
        with patch.object(bpl, "_embed") as em, \
                patch.object(bpl, "_upsert") as up:
            result = bpl.process_file(self.jsonl, dry_run=True)
        em.assert_not_called()
        up.assert_not_called()
        self.assertEqual(result["upserted"], 2)

    # T24: Ollama returns None → skip turn, continue, increment counter
    def test_t24_embed_failure_skipped(self) -> None:
        with patch.object(bpl, "_embed", return_value=None), \
                patch.object(bpl, "_upsert") as up:
            result = bpl.process_file(self.jsonl)
        up.assert_not_called()
        self.assertEqual(result["upserted"], 0)
        self.assertEqual(result["skipped_embed_fail"], 2)


class TestWalker(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.projects = self.tmp / "projects"
        sid_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        sid_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        _build_jsonl(
            self.projects / "-Users-fake-workspace-foo" / f"{sid_a}.jsonl",
            "/Users/fake/workspace/foo",
            ["one"],
        )
        _build_jsonl(
            self.projects / "-Users-fake-workspace-bar" / f"{sid_b}.jsonl",
            "/Users/fake/workspace/bar",
            ["two"],
        )
        self.env_patch = patch.dict(
            os.environ, {"PODZONE_QDRANT_APIKEY": "test-key"}, clear=False
        )
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # T25: --workspace filter respects session_metadata.resolve()
    def test_t25_workspace_filter(self) -> None:
        with patch.object(bpl, "_embed", return_value=[0.0] * 768), \
                patch.object(bpl, "_upsert", return_value=True):
            report = bpl.backfill(
                projects_dir=self.projects, workspace_filter="foo"
            )
        self.assertEqual(report["totals"]["upserted"], 1)
        self.assertEqual(report["totals"]["skipped_workspace"], 1)
        self.assertIn("foo", report["per_workspace"])
        self.assertNotIn("bar", report["per_workspace"])


if __name__ == "__main__":
    unittest.main()
