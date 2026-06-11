"""Tests for hooks/stop-heartbeat.py (T-005 full payload behaviour)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _load_hook():
    spec = importlib.util.spec_from_file_location(
        "stop_heartbeat", REPO_ROOT / "hooks" / "stop-heartbeat.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


hook = _load_hook()


SID = "44444444-4444-4444-8444-444444444444"


def _make_jsonl(tmp: Path, session_id: str, cwd: str) -> Path:
    encoded = cwd.replace("/", "-")
    proj_dir = tmp / encoded
    proj_dir.mkdir(parents=True, exist_ok=True)
    jsonl = proj_dir / f"{session_id}.jsonl"
    with jsonl.open("w") as fh:
        fh.write(
            json.dumps(
                {
                    "type": "user",
                    "cwd": cwd,
                    "timestamp": "2026-05-20T10:00:00.000Z",
                    "message": {"role": "user", "content": "hi"},
                }
            )
            + "\n"
        )
        fh.write(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-05-20T10:00:01.000Z",
                    "message": {
                        "model": "claude-opus-4-7",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                        },
                    },
                }
            )
            + "\n"
        )
    return jsonl


class TestStopHeartbeat(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.cwd = "/Users/fake/workspace/foo"
        self.jsonl = _make_jsonl(self.tmp, SID, self.cwd)
        # Patch ~/.claude/projects/ to point at our tmp
        self.home_patch = patch.object(
            hook.Path, "home", staticmethod(lambda: self.tmp.parent)
        )
        self.home_patch.start()
        # The hook builds jsonl path from Path.home() / ".claude" / "projects" — so
        # mirror our tmp structure there:
        target = self.tmp.parent / ".claude" / "projects" / self.cwd.replace("/", "-")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.symlink_to(self.tmp / self.cwd.replace("/", "-"))
        self.env_patch = patch.dict(
            os.environ, {"PODZONE_QDRANT_APIKEY": "test-key"}, clear=False
        )
        self.env_patch.start()

    def tearDown(self) -> None:
        self.home_patch.stop()
        self.env_patch.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.tmp.parent / ".claude", ignore_errors=True)

    # T12: standard Stop with extant JSONL → full payload, stop_hook, in_progress
    def test_t12_full_payload_upsert(self) -> None:
        with patch("lib.qdrant_http.upsert_points") as up:
            hook.run(SID, self.cwd)
            self.assertTrue(up.called)
            sent = up.call_args.args[0][0]["payload"]
            self.assertEqual(sent["data_source"], "stop_hook")
            self.assertEqual(sent["status"], "in_progress")
            self.assertIn("model_usage", sent)
            self.assertIn("claude-opus-4-7", sent["model_usage"])

    # T13: missing JSONL → falls back to heartbeat-only write (via qdrant_http)
    def test_t13_missing_jsonl_fallback(self) -> None:
        # Use a session_id with no corresponding file
        bogus_sid = "55555555-5555-4555-8555-555555555555"
        with patch.object(hook, "_full_payload_upsert", return_value=False) as fp, \
             patch("lib.qdrant_http.upsert_points") as up:
            hook.run(bogus_sid, self.cwd)
            fp.assert_not_called()  # JSONL missing → never tried
            self.assertTrue(up.called)
            sent = up.call_args.args[0][0]["payload"]
            self.assertEqual(sent["data_source"], "stop_hook")
            self.assertIn("last_heartbeat_ts", sent)
            self.assertNotIn("model_usage", sent)

    # T14: malformed stdin → exit 0 cleanly (tested via main())
    def test_t14_malformed_stdin(self) -> None:
        with patch.object(sys, "stdin", _StringIO("not json")):
            with self.assertRaises(SystemExit) as cm:
                hook.main()
            self.assertEqual(cm.exception.code, 0)

    # T15: scrape times out → exits 0, no upsert
    def test_t15_scrape_timeout(self) -> None:
        def slow_scrape(_path):
            raise TimeoutError("simulated")

        with patch("lib.qdrant_http.upsert_points"), \
                patch("lib.jsonl_scrape.scrape", side_effect=slow_scrape):
            # heartbeat-only fallback should kick in; we just verify no raise
            try:
                hook.run(SID, self.cwd)
            except Exception as exc:
                self.fail(f"hook should swallow scrape errors; raised: {exc}")


class _StringIO:
    """Minimal stdin replacement for read()."""
    def __init__(self, data: str) -> None:
        self._data = data

    def read(self) -> str:
        return self._data


if __name__ == "__main__":
    unittest.main()
