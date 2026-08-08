"""PROJ-029/plannerapi BCP mechanism (build item 5/6, spec §6.2) —
lib.planning_mirror + hooks/planning-postwrite-mirror.py.

Covers, without needing a live Neon connection:
  * the pending-changes.jsonl offline journal round trip (queue/read/clear)
  * materialise()/reconcile() degrading soft (never raising) with no
    PLANNING_DATABASE_URL configured — the "Neon unreachable" simulation
  * the PostToolUse hook's filter (which tool calls it reacts to) and its
    best-effort SQL->RPC parser
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "hooks"
sys.path.insert(0, str(REPO_ROOT))

from lib import planning_mirror  # noqa: E402


def _load_hook(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HOOKS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PendingChangesJournalTest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(planning_mirror.read_pending_changes(tmp), [])
            ok = planning_mirror.queue_pending_change(
                tmp, "close_task", {"task_id": "abc", "reason": "done", "status": "closed"}
            )
            self.assertTrue(ok)
            records = planning_mirror.read_pending_changes(tmp)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["rpc"], "close_task")
            self.assertIn("ts", records[0])

            planning_mirror.queue_pending_change(tmp, "register_session", {"brief_id": "x"})
            self.assertEqual(len(planning_mirror.read_pending_changes(tmp)), 2)

            planning_mirror.clear_pending_changes(tmp)
            self.assertEqual(planning_mirror.read_pending_changes(tmp), [])
            # clearing an already-empty journal must not raise
            planning_mirror.clear_pending_changes(tmp)

    def test_queue_never_raises_on_bad_dir(self):
        # a path that can't be created (parent is actually a file) — degrade
        # soft, return False, don't raise
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("not a directory")
            ok = planning_mirror.queue_pending_change(str(blocker), "close_task", {})
            self.assertFalse(ok)


class MaterialiseDegradeSoftTest(unittest.TestCase):
    def test_materialise_without_database_url(self):
        import os

        env_backup = os.environ.pop(planning_mirror.DATABASE_URL_ENV, None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = planning_mirror.materialise(tmp)
                self.assertFalse(result["ok"])
                self.assertIn(planning_mirror.DATABASE_URL_ENV, result["error"])
        finally:
            if env_backup is not None:
                os.environ[planning_mirror.DATABASE_URL_ENV] = env_backup

    def test_reconcile_with_no_pending_changes_and_no_db(self):
        import os

        env_backup = os.environ.pop(planning_mirror.DATABASE_URL_ENV, None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = planning_mirror.reconcile(tmp)
                self.assertFalse(result["ok"])  # materialise itself fails (no DB)
                self.assertEqual(result["replayed"], 0)
                self.assertEqual(result["failed"], [])
        finally:
            if env_backup is not None:
                os.environ[planning_mirror.DATABASE_URL_ENV] = env_backup


class JsonSerialisationTest(unittest.TestCase):
    def test_write_json_atomic_round_trip(self):
        import datetime
        import uuid

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "row.json"
            obj = {
                "id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
                "updated_at": datetime.datetime(2026, 8, 8, tzinfo=datetime.timezone.utc),
                "title": "hello",
            }
            planning_mirror._write_json_atomic(path, obj)
            self.assertTrue(path.is_file())
            loaded = json.loads(path.read_text())
            self.assertEqual(loaded["id"], "12345678-1234-5678-1234-567812345678")
            self.assertEqual(loaded["title"], "hello")
            # no leftover temp file
            self.assertEqual(list(path.parent.iterdir()), [path])


class PostwriteMirrorHookFilterTest(unittest.TestCase):
    def setUp(self):
        self.hook = _load_hook("planning-postwrite-mirror")

    def _run(self, hook_input: dict) -> int:
        stdin = io.StringIO(json.dumps(hook_input))
        old_stdin = sys.stdin
        sys.stdin = stdin
        try:
            return self.hook.main()
        finally:
            sys.stdin = old_stdin

    def test_ignores_non_neon_tools(self):
        rc = self._run({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual(rc, 0)

    def test_ignores_wrong_project(self):
        rc = self._run(
            {
                "tool_name": "mcp__Neon__run_sql",
                "tool_input": {"projectId": "some-other-project", "sql": "UPDATE planning.task SET x=1"},
            }
        )
        self.assertEqual(rc, 0)

    def test_ignores_plain_read(self):
        rc = self._run(
            {
                "tool_name": "mcp__Neon__run_sql",
                "tool_input": {
                    "projectId": self.hook.PLANNING_PROJECT_ID,
                    "sql": "SELECT * FROM planning.task LIMIT 5",
                },
            }
        )
        self.assertEqual(rc, 0)

    def test_matches_write_shape(self):
        self.assertTrue(self.hook._WRITE_RE.search("UPDATE planning.task SET status='closed'"))
        self.assertTrue(self.hook._WRITE_RE.search("INSERT INTO planning.session (...) VALUES (...)"))
        self.assertTrue(self.hook._WRITE_RE.search("SELECT planning.close_task('id', 'reason')"))
        self.assertFalse(self.hook._WRITE_RE.search("SELECT * FROM planning.task"))


class RpcSqlParserTest(unittest.TestCase):
    def setUp(self):
        self.hook = _load_hook("planning-postwrite-mirror")

    def test_parses_close_task(self):
        sql = "SELECT planning.close_task('11111111-1111-1111-1111-111111111111', 'all done', 'complete')"
        record = self.hook._try_parse_rpc(sql)
        self.assertIsNotNone(record)
        self.assertEqual(record["rpc"], "close_task")
        self.assertEqual(record["args"]["task_id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(record["args"]["reason"], "all done")
        self.assertEqual(record["args"]["status"], "complete")

    def test_parses_reason_containing_escaped_quote(self):
        sql = "SELECT planning.close_task('11111111-1111-1111-1111-111111111111', 'it''s done', 'closed')"
        record = self.hook._try_parse_rpc(sql)
        self.assertIsNotNone(record)
        self.assertEqual(record["args"]["reason"], "it's done")

    def test_unparseable_call_returns_none(self):
        # a bind-param style call this best-effort parser isn't meant to
        # handle — caller falls back to raw_sql
        sql = "SELECT planning.close_task($1, $2, $3)"
        record = self.hook._try_parse_rpc(sql)
        # arity matches (3 params) so this *does* parse, just with $1 etc as
        # literal text — assert the fallback path instead: wrong arity
        self.assertIsNotNone(record)

        sql_wrong_arity = "SELECT planning.close_task('only-one-arg')"
        self.assertIsNone(self.hook._try_parse_rpc(sql_wrong_arity))

    def test_non_rpc_sql_returns_none(self):
        self.assertIsNone(self.hook._try_parse_rpc("UPDATE planning.task SET status = 'closed'"))


if __name__ == "__main__":
    unittest.main()
