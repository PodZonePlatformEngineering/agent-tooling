"""register-planning-session.py's work-item ref validator (PLA-287).

Covers `_resolve_task_ids()` against a fake DB-API cursor (no live Neon
connection needed):
  * legacy `PROJ-XXX/T-YYY` resolution (unchanged behaviour)
  * short `{PREFIX}-{NNN}` resolution (new, PLA-287)
  * the genuine-error case — a ref matching neither shape raises a clear
    message before any query is even issued
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "register_planning_session", REPO_ROOT / "tools" / "register-planning-session.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


register_planning_session = _load_tool()


def _fake_conn(row):
    """A conn whose cursor's fetchone() always returns `row`."""
    cur = MagicMock()
    cur.fetchone.return_value = row
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


class ResolveTaskIdsTest(unittest.TestCase):
    def test_legacy_shape_resolution(self):
        conn, cur = _fake_conn(("11111111-1111-1111-1111-111111111111",))
        ids = register_planning_session._resolve_task_ids(conn, ["PROJ-006/T-008"])
        self.assertEqual(ids, ["11111111-1111-1111-1111-111111111111"])
        sql, params = cur.execute.call_args[0]
        self.assertIn("JOIN planning.project", sql)
        self.assertEqual(params, ("PROJ-006", "T-008"))

    def test_short_ref_resolution(self):
        conn, cur = _fake_conn(("22222222-2222-2222-2222-222222222222",))
        ids = register_planning_session._resolve_task_ids(conn, ["PLA-287"])
        self.assertEqual(ids, ["22222222-2222-2222-2222-222222222222"])
        sql, params = cur.execute.call_args[0]
        self.assertNotIn("JOIN planning.project", sql)
        self.assertEqual(params, ("PLA-287",))

    def test_unresolvable_ref_shape_raises(self):
        conn, cur = _fake_conn(None)
        with self.assertRaisesRegex(ValueError, "must be either legacy"):
            register_planning_session._resolve_task_ids(conn, ["not-a-valid-ref!"])
        cur.execute.assert_not_called()

    def test_no_matching_task_raises(self):
        conn, cur = _fake_conn(None)
        with self.assertRaisesRegex(ValueError, "no planning.task found"):
            register_planning_session._resolve_task_ids(conn, ["PLA-999"])


if __name__ == "__main__":
    unittest.main()
