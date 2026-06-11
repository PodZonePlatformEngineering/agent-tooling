"""Tests for tools/backfill-sessions.py."""

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


def _load_backfill_module():
    spec = importlib.util.spec_from_file_location(
        "backfill_sessions", REPO_ROOT / "tools" / "backfill-sessions.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


backfill_sessions = _load_backfill_module()


def _write_jsonl(path: Path, cwd: str, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        fh.write(json.dumps({"type": "user", "cwd": cwd, "timestamp": "2026-05-20T10:00:00.000Z", "message": {"role": "user", "content": "hi"}}) + "\n")
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _make_projects_tree(tmp: Path, layout: dict[str, list[str]]) -> Path:
    """layout: {project_dir_name: [session_uuid, ...]} → builds ~/.claude/projects/-... mirror."""
    projects = tmp / "projects"
    for proj_dir, session_ids in layout.items():
        for sid in session_ids:
            jsonl = projects / proj_dir / f"{sid}.jsonl"
            # decode dir back to cwd
            cwd = "/" + proj_dir[1:].replace("-", "/") if proj_dir.startswith("-") else proj_dir
            entry = {
                "type": "assistant",
                "timestamp": "2026-05-20T10:00:01.000Z",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
            _write_jsonl(jsonl, cwd, [entry])
    return projects


SID_A = "11111111-1111-4111-8111-111111111111"
SID_B = "22222222-2222-4222-8222-222222222222"
SID_C = "33333333-3333-4333-8333-333333333333"


class TestWalker(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.projects = _make_projects_tree(
            self.tmp,
            {
                "-Users-fake-workspace-foo": [SID_A, SID_B],
                "-Users-fake-workspace-bar": [SID_C],
            },
        )
        self.env_patch = patch.dict(
            os.environ, {"PODZONE_QDRANT_APIKEY": "test-key"}, clear=False
        )
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # T7: walks tree and upserts each JSONL
    def test_t7_walk_and_upsert(self) -> None:
        with patch("lib.qdrant_http.upsert_points") as up, \
                patch("lib.sessions_upsert._get_existing", return_value=None):
            report = backfill_sessions.backfill(projects_dir=self.projects)
        self.assertEqual(report["scanned"], 3)
        self.assertEqual(report["upserted"], 3)
        self.assertEqual(up.call_count, 3)

    # T8: --dry-run does not upsert
    def test_t8_dry_run(self) -> None:
        with patch("lib.qdrant_http.upsert_points") as up:
            report = backfill_sessions.backfill(projects_dir=self.projects, dry_run=True)
        self.assertEqual(report["scanned"], 3)
        self.assertEqual(report["upserted"], 3)
        up.assert_not_called()

    # T9: --workspace filter
    def test_t9_workspace_filter(self) -> None:
        with patch("lib.qdrant_http.upsert_points") as up, \
                patch("lib.sessions_upsert._get_existing", return_value=None):
            report = backfill_sessions.backfill(
                projects_dir=self.projects, workspace_filter="foo"
            )
        self.assertEqual(report["upserted"], 2)
        self.assertEqual(report["skipped_workspace"], 1)
        self.assertIn("foo", report["per_workspace"])
        self.assertNotIn("bar", report["per_workspace"])

    # T10: write-precedence skip
    def test_t10_skip_when_session_end_skill_newer(self) -> None:
        def fake_get_existing(sid):
            if sid == SID_A:
                return {
                    "data_source": "session_end_skill",
                    "jsonl_mtime": "2099-01-01T00:00:00+00:00",
                }
            return None

        with patch("lib.qdrant_http.upsert_points") as up, \
                patch("lib.sessions_upsert._get_existing", side_effect=fake_get_existing):
            report = backfill_sessions.backfill(projects_dir=self.projects)

        self.assertEqual(report["upserted"], 2)
        self.assertEqual(report["skipped_finalised"], 1)

    # T11: aggregate counts in report match upserted
    def test_t11_aggregate_counts(self) -> None:
        with patch("lib.qdrant_http.upsert_points") as up, \
                patch("lib.sessions_upsert._get_existing", return_value=None):
            report = backfill_sessions.backfill(projects_dir=self.projects)
        total_sessions_per_ws = sum(
            e["sessions"] for e in report["per_workspace"].values()
        )
        self.assertEqual(total_sessions_per_ws, report["upserted"])

    def test_only_new_skips_existing(self) -> None:
        with patch("lib.qdrant_http.upsert_points") as up, \
                patch("lib.sessions_upsert._get_existing") as ge:
            ge.side_effect = lambda sid: ({"data_source": "stop_hook"} if sid == SID_A else None)
            report = backfill_sessions.backfill(
                projects_dir=self.projects, only_new=True
            )
        self.assertEqual(report["upserted"], 2)
        self.assertEqual(report["skipped_only_new"], 1)


if __name__ == "__main__":
    unittest.main()
