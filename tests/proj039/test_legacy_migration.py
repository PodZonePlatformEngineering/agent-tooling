"""Tests for lib/legacy_migration — the DT-014 / AC-007 additive migration.

Asserts the load-bearing properties of the migration:
  * **additive** — writes go only to `session_substrate`; the source collections
    are scrolled (read) but never written or deleted (C-003 reversibility);
  * **active selection** — only ACTIVE_STATUSES task/work_item points + in_progress
    sessions are taken; all events are taken (the audit trail);
  * **dedup** — a task present in both `work_items` and `tasks` (same proj/task id)
    migrates once, work_items winning;
  * **normalisation** — `in-progress`→`in_progress`, `owner`→`agent`, so migrated
    task points are found by session_substrate.active_work_items;
  * **provenance + audit trail** — the full source payload is preserved under a
    `_migration` block (AC-007);
  * **vectorless** — points carry an empty named-vector map;
  * **idempotent ids** — deterministic, so a re-run upserts in place.

The qdrant_http read/write layer is stubbed; the live run + reconciliation is
captured in the session outbox.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import legacy_migration, qdrant_http, session_substrate  # noqa: E402

# A small but representative legacy corpus.
LEGACY = {
    "work_items": [
        {"id": "wi-1", "payload": {"proj_id": "PROJ-012", "task_id": "T-001",
                                   "title": "Active WI", "status": "ready", "owner": "hephaestus"}},
        {"id": "wi-2", "payload": {"proj_id": "PROJ-012", "task_id": "T-002",
                                   "title": "Blocked WI", "status": "blocked", "owner": "atlas"}},
        {"id": "wi-3", "payload": {"proj_id": "PROJ-099", "task_id": "T-009",
                                   "title": "Done WI", "status": "complete", "owner": "hermes"}},
    ],
    "tasks": [
        # duplicate of wi-1 (same proj/task) — should be deduped (work_items wins)
        {"id": "tk-1", "payload": {"proj_id": "PROJ-012", "task_id": "T-001",
                                   "summary": "dup", "status": "ready", "agent": "hephaestus"}},
        # tasks-only active, with hyphenated status to normalise
        {"id": "tk-2", "payload": {"proj_id": "PROJ-014", "task_id": "T-008",
                                   "summary": "Tasks only", "status": "in-progress", "agent": "hermes"}},
        # paused = not active → excluded
        {"id": "tk-3", "payload": {"proj_id": "PROJ-014", "task_id": "T-009",
                                   "summary": "Paused", "status": "paused", "agent": "hermes"}},
    ],
    "sessions": [
        {"id": "s-1", "payload": {"session_id": "aaaa", "agent": "hephaestus", "status": "in_progress"}},
        {"id": "s-2", "payload": {"session_id": "bbbb", "agent": "atlas", "status": "ended"}},
    ],
    "task_events": [
        {"id": "ev-1", "payload": {"event_type": "subagent_complete", "session_id": "aaaa",
                                   "detail": "x", "timestamp": "2026-06-01T00:00:00Z"}},
    ],
    "prompt_logs": [
        {"id": "pl-1", "payload": {"session_id": "aaaa", "prompt_text": "hi", "turn_number": 1}},
    ],
}


def _fake_scroll(*, collection, body=None, qdrant_url=None, api_key=None, **kw):
    # one page, no offset
    if body and body.get("offset"):
        return {"result": {"points": [], "next_page_offset": None}}
    return {"result": {"points": LEGACY.get(collection, []), "next_page_offset": None}}


class MigrationPlanTests(unittest.TestCase):
    def setUp(self):
        with patch.object(qdrant_http, "scroll", _fake_scroll):
            self.m = legacy_migration.collect_migration(api_key="x")

    def _by_type(self, t):
        return [p for p in self.m.points if p.point_type == t]

    def test_active_task_selection_and_dedup(self):
        tasks = self._by_type("task")
        keys = sorted(p.dedup_key for p in tasks)
        # PROJ-012/T-001 (wi, dup dropped), PROJ-012/T-002 (blocked), PROJ-014/T-008 (in-progress)
        self.assertEqual(keys, ["PROJ-012/T-001", "PROJ-012/T-002", "PROJ-014/T-008"])
        self.assertEqual(self.m.dedup_dropped, 1)
        # complete + paused excluded
        self.assertNotIn("PROJ-099/T-009", keys)
        self.assertNotIn("PROJ-014/T-009", keys)

    def test_dedup_keeps_work_items_source(self):
        wi_t001 = next(p for p in self._by_type("task") if p.dedup_key == "PROJ-012/T-001")
        self.assertEqual(wi_t001.source_collection, "work_items")
        self.assertEqual(wi_t001.payload["title"], "Active WI")  # not the tasks 'dup'

    def test_status_and_agent_normalised(self):
        t008 = next(p for p in self._by_type("task") if p.dedup_key == "PROJ-014/T-008")
        self.assertEqual(t008.payload["status"], "in_progress")   # was in-progress
        self.assertEqual(t008.payload["agent"], "hermes")
        # owner→agent for a work_items point
        t002 = next(p for p in self._by_type("task") if p.dedup_key == "PROJ-012/T-002")
        self.assertEqual(t002.payload["agent"], "atlas")

    def test_migrated_task_is_findable_by_active_query(self):
        """status+agent normalised → the active_work_items filter would match."""
        t008 = next(p for p in self._by_type("task") if p.dedup_key == "PROJ-014/T-008")
        self.assertEqual(t008.payload["point_type"], "task")
        self.assertIn(t008.payload["status"], session_substrate.ACTIVE_STATUSES)

    def test_sessions_only_in_progress(self):
        sess = self._by_type("session")
        self.assertEqual(len(sess), 1)
        self.assertEqual(sess[0].dedup_key, "aaaa")
        # session keeps the substrate-native deterministic id
        self.assertEqual(sess[0].point_id, session_substrate.point_id_for("aaaa"))

    def test_all_events_migrated(self):
        events = self._by_type("event")
        self.assertEqual(len(events), 2)  # 1 task_event + 1 prompt_log
        self.assertEqual({e.source_collection for e in events}, {"task_events", "prompt_logs"})
        for e in events:
            self.assertEqual(e.payload["point_type"], "event")

    def test_audit_trail_preserved(self):
        for p in self.m.points:
            self.assertIn("_migration", p.payload)
            self.assertIn("source_collection", p.payload["_migration"])
            self.assertIn("source_point_id", p.payload["_migration"])
            self.assertIn("migrated_at", p.payload["_migration"])

    def test_points_are_vectorless(self):
        for p in self.m.points:
            self.assertEqual(p.to_qdrant()["vector"], {})

    def test_ids_are_deterministic(self):
        with patch.object(qdrant_http, "scroll", _fake_scroll):
            m2 = legacy_migration.collect_migration(api_key="x")
        self.assertEqual(sorted(p.point_id for p in self.m.points),
                         sorted(p.point_id for p in m2.points))


class MigrationWriteTests(unittest.TestCase):
    def setUp(self):
        with patch.object(qdrant_http, "scroll", _fake_scroll):
            self.m = legacy_migration.collect_migration(api_key="x")

    def test_dry_run_writes_nothing(self):
        calls = []
        with patch.object(qdrant_http, "upsert_points",
                          lambda *a, **k: calls.append(a)):
            res = legacy_migration.write_migration(self.m, dry_run=True)
        self.assertEqual(calls, [])
        self.assertEqual(res["written"], 0)
        self.assertEqual(res["total"], len(self.m.points))

    def test_apply_writes_only_to_substrate(self):
        written_collections = []
        written_points = []

        def _fake_upsert(points, *, collection, qdrant_url=None, api_key=None, **kw):
            written_collections.append(collection)
            written_points.extend(points)
            return {"result": {"status": "acknowledged"}}

        with patch.object(qdrant_http, "upsert_points", _fake_upsert):
            res = legacy_migration.write_migration(self.m, dry_run=False)

        self.assertEqual(res["written"], len(self.m.points))
        # additive: every write targeted session_substrate, nothing else
        self.assertEqual(set(written_collections), {session_substrate.COLLECTION})
        self.assertEqual(len(written_points), len(self.m.points))

    def test_no_source_collection_is_ever_written(self):
        """C-003 reversibility: scroll the sources (read) but never write them."""
        written_collections = []
        with patch.object(qdrant_http, "scroll", _fake_scroll), \
             patch.object(qdrant_http, "upsert_points",
                          lambda points, *, collection, **k: written_collections.append(collection)), \
             patch.object(qdrant_http, "set_payload",
                          lambda *a, **k: written_collections.append("SET_PAYLOAD")), \
             patch.object(qdrant_http, "delete_points",
                          lambda *a, **k: written_collections.append("DELETE")):
            m = legacy_migration.collect_migration(api_key="x")
            legacy_migration.write_migration(m, dry_run=False)
        for src in legacy_migration.TASK_SOURCES + legacy_migration.EVENT_SOURCES + (legacy_migration.SESSION_SOURCE,):
            self.assertNotIn(src, written_collections)
        self.assertNotIn("DELETE", written_collections)


if __name__ == "__main__":
    unittest.main()
