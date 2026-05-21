"""Tests for tools/upsert-current-session.py."""

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


def _load():
    spec = importlib.util.spec_from_file_location(
        "upsert_current_session", REPO_ROOT / "tools" / "upsert-current-session.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


ucs = _load()

SID = "66666666-6666-4666-8666-666666666666"


def _write_jsonl(target: Path, cwd: str) -> Path:
    encoded = cwd.replace("/", "-")
    proj_dir = target / ".claude" / "projects" / encoded
    proj_dir.mkdir(parents=True, exist_ok=True)
    jsonl = proj_dir / f"{SID}.jsonl"
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
    return jsonl


class TestUpsertCurrentSession(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.cwd = "/Users/fake/workspace/foo"
        _write_jsonl(self.tmp, self.cwd)
        self.home_patch = patch.object(
            ucs.Path, "home", staticmethod(lambda: self.tmp)
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

    # T16: explicit --session-id + --cwd resolves expected JSONL
    def test_t16_explicit_args(self) -> None:
        with patch("lib.sessions_upsert.requests") as rq:
            mock_resp = type("R", (), {"raise_for_status": lambda self: None, "status_code": 200})()
            rq.put.return_value = mock_resp
            rc = ucs.run(SID, self.cwd, "session_end_skill", "ended")
            self.assertEqual(rc, 0)
            self.assertTrue(rq.put.called)
            sent = rq.put.call_args.kwargs["json"]["points"][0]["payload"]
            self.assertEqual(sent["data_source"], "session_end_skill")
            self.assertEqual(sent["status"], "ended")

    # T17: auto-detect via env + cwd
    def test_t17_autodetect(self) -> None:
        with patch.dict(os.environ, {"CLAUDE_SESSION_ID": SID}, clear=False), \
                patch("os.getcwd", return_value=self.cwd), \
                patch("lib.sessions_upsert.requests") as rq:
            mock_resp = type("R", (), {"raise_for_status": lambda self: None, "status_code": 200})()
            rq.put.return_value = mock_resp
            rc = ucs.run(None, None, "session_end_skill", "ended")
            self.assertEqual(rc, 0)
            self.assertTrue(rq.put.called)

    # T18: missing JSONL → exit 0 with stderr log (best-effort)
    def test_t18_missing_jsonl(self) -> None:
        with patch("lib.sessions_upsert.requests") as rq:
            mock_resp = type("R", (), {"raise_for_status": lambda self: None, "status_code": 200})()
            rq.put.return_value = mock_resp
            rc = ucs.run(SID, "/nonexistent/path", "session_end_skill", "ended")
            self.assertEqual(rc, 0)
            rq.put.assert_not_called()

    def test_no_session_id_exits_zero(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            rc = ucs.run(None, self.cwd, "session_end_skill", "ended")
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
