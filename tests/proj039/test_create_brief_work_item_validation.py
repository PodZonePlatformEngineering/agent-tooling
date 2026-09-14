"""create-brief.py's `--work-item` creation-time validation (PROJ-039/USS-418).

Three live 2026-09-13 `create-brief.py` calls were each given a malformed
`--work-item` ref (a brief slug instead of the real `PROJ-XXX/T-YYY` ref) and
nothing caught it until deep in a background dispatch log. This tool now reuses
register-planning-session.py's own resolver (moved to
`lib.planning_mirror.resolve_task_ids`, PROJ-039/USS-418) to validate every
`--work-item` value *before* the brief point is upserted:

  * a ref that resolves passes through unchanged, brief gets created
  * a ref that doesn't resolve halts immediately with a clear error, and
    `brief_substrate.create_brief` is never called
  * omitting `--work-item` entirely stays unvalidated (still optional)
  * no `PLANNING_DATABASE_URL` configured degrades soft (best-effort,
    matching register-planning-session.py's own posture) rather than
    blocking a brief that would otherwise be fine
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "create_brief", REPO_ROOT / "tools" / "create-brief.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


create_brief = _load_tool()


class ValidateWorkItemsTest(unittest.TestCase):
    def test_no_work_items_skips_validation(self):
        with patch.object(create_brief.planning_mirror, "connect") as connect:
            rc = create_brief._validate_work_items([])
        self.assertEqual(rc, 0)
        connect.assert_not_called()

    def test_valid_ref_passes_through(self):
        conn = MagicMock()
        with patch.object(create_brief.planning_mirror, "connect", return_value=conn), \
             patch.object(create_brief.planning_mirror, "resolve_task_ids",
                           return_value=["11111111-1111-1111-1111-111111111111"]) as resolve:
            rc = create_brief._validate_work_items(["PROJ-039/T-418"])
        self.assertEqual(rc, 0)
        resolve.assert_called_once_with(conn, ["PROJ-039/T-418"])
        conn.close.assert_called_once()

    def test_unresolvable_ref_halts(self):
        conn = MagicMock()
        with patch.object(create_brief.planning_mirror, "connect", return_value=conn), \
             patch.object(create_brief.planning_mirror, "resolve_task_ids",
                           side_effect=ValueError(
                               "no planning.task found for "
                               "'PROJ-011/acp504-academy-frontend-as-built-docs' — "
                               "create it first")):
            rc = create_brief._validate_work_items(
                ["PROJ-011/acp504-academy-frontend-as-built-docs"])
        self.assertEqual(rc, 1)
        conn.close.assert_called_once()

    def test_missing_database_url_degrades_soft(self):
        with patch.object(create_brief.planning_mirror, "connect",
                           side_effect=RuntimeError("PLANNING_DATABASE_URL not set")):
            rc = create_brief._validate_work_items(["PROJ-039/T-418"])
        self.assertEqual(rc, 0)

    def test_bad_ref_halts_before_brief_point_is_written(self):
        with patch.object(create_brief.planning_mirror, "connect", return_value=MagicMock()), \
             patch.object(create_brief.planning_mirror, "resolve_task_ids",
                           side_effect=ValueError("no planning.task found for 'bad-ref'")), \
             patch.object(create_brief.brief_substrate, "create_brief") as create:
            rc = create_brief.main([
                "--brief-id", "hephaestus/2026-09-15-test-brief",
                "--team", "hephaestus", "--author", "hephaestus", "--assignee", "hephaestus",
                "--body", "# Brief\n\nsome body text",
                "--work-item", "bad-ref",
            ])
        self.assertEqual(rc, 1)
        create.assert_not_called()

    def test_valid_ref_reaches_brief_point_write(self):
        with patch.object(create_brief.planning_mirror, "connect", return_value=MagicMock()), \
             patch.object(create_brief.planning_mirror, "resolve_task_ids",
                           return_value=["11111111-1111-1111-1111-111111111111"]), \
             patch.object(create_brief, "_gate_body", return_value=0), \
             patch.object(create_brief.brief_substrate, "create_brief",
                           return_value={"point_id": "deadbeef"}) as create:
            rc = create_brief.main([
                "--brief-id", "hephaestus/2026-09-15-test-brief",
                "--team", "hephaestus", "--author", "hephaestus", "--assignee", "hephaestus",
                "--body", "# Brief\n\nsome body text",
                "--work-item", "PROJ-039/T-418",
            ])
        self.assertEqual(rc, 0)
        create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
