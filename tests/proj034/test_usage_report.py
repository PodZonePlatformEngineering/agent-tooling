"""Tests for tools/usage-report.py — PROJ-034/T-008.

T1–T13 cover the algorithmic surface; live verification (live Qdrant scroll,
zombie deletes) is captured in the PR description, not here.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "usage_report", REPO_ROOT / "tools" / "usage-report.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


usage_report = _load_module()


# ---------------------------------------------------------------------------
# Fixture helpers

NOW = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)


def _bucket(input_=0, output=0, cache_creation=0, cache_read=0, iterations=0):
    return {
        "input_tokens": input_,
        "output_tokens": output,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "iterations": iterations,
    }


def _point(
    pid: str,
    *,
    workspace: str = "agent-tooling",
    agent: str = "hephaestus",
    model: str = "claude-sonnet-4-6",
    bucket: dict | None = None,
    last_message_ts: datetime | None = None,
    status: str = "ended",
    assistant_count: int = 5,
    data_source: str = "stop_hook",
) -> dict:
    bucket = bucket or _bucket(input_=1000, output=500, cache_creation=200, cache_read=8000, iterations=assistant_count)
    last_message_ts = last_message_ts or (NOW - timedelta(days=1))
    payload = {
        "session_id": pid,
        "workspace": workspace,
        "agent": agent,
        "data_source": data_source,
        "status": status,
        "last_message_ts": last_message_ts.isoformat(),
        "last_heartbeat_ts": last_message_ts.isoformat(),
        "model_usage": {model: bucket} if model else {},
        "total_tokens": bucket,
        "message_counts": {"assistant": assistant_count, "user": assistant_count},
    }
    return {"id": pid, "payload": payload}


class TestUsageReport(unittest.TestCase):

    def setUp(self):
        # Stub now() in filter_window via patching
        patcher = patch.object(
            usage_report, "datetime",
            wraps=usage_report.datetime,
        )
        self.mock_dt = patcher.start()
        self.mock_dt.now.return_value = NOW
        self.addCleanup(patcher.stop)

        # Stub scroll_all unconditionally — every test injects points
        scroll_patcher = patch.object(usage_report, "scroll_all", return_value=[])
        scroll_patcher.start()
        self.addCleanup(scroll_patcher.stop)

        # Stub delete_points so tests never hit the network
        del_patcher = patch.object(usage_report, "delete_points", return_value=True)
        self.mock_delete = del_patcher.start()
        self.addCleanup(del_patcher.stop)

    # T1 — empty
    def test_t1_empty_result_set(self):
        r = usage_report.run(days=7, points=[], stdout_only=True)
        self.assertEqual(r["agg"]["session_count"], 0)
        self.assertIn("Usage Summary", r["markdown"])
        self.assertIn("Sessions: 0", r["digest"])

    # T2 — single workspace, single model
    def test_t2_single_workspace_single_model(self):
        pts = [_point("a", bucket=_bucket(input_=1000, output=500, cache_creation=100, cache_read=4000, iterations=3))]
        r = usage_report.run(days=7, points=pts, stdout_only=True)
        self.assertEqual(r["agg"]["session_count"], 1)
        self.assertIn("agent-tooling", r["agg"]["per_workspace"])
        self.assertIn("claude-sonnet-4-6", r["agg"]["per_model"])

    # T3 — multi-workspace + multi-model totals match
    def test_t3_multi_workspace_multi_model(self):
        pts = [
            _point("a", workspace="agent-tooling", model="claude-sonnet-4-6",
                   bucket=_bucket(input_=100, output=50, cache_creation=10, cache_read=400)),
            _point("b", workspace="podzoneAgentTeam", model="claude-opus-4-7",
                   bucket=_bucket(input_=200, output=100, cache_creation=20, cache_read=800)),
        ]
        r = usage_report.run(days=7, points=pts, stdout_only=True)
        ws_total = sum(usage_report._sum_total(e["totals"]) for e in r["agg"]["per_workspace"].values())
        model_total = sum(usage_report._sum_total(e["totals"]) for e in r["agg"]["per_model"].values())
        self.assertEqual(ws_total, model_total)
        self.assertEqual(ws_total, 100 + 50 + 10 + 200 + 100 + 20)

    # T4 — cache-hit formula
    def test_t4_cache_hit_calculation(self):
        # bucket: input=100, creation=100, read=300 → 300/(300+100+100) = 60%
        bucket = _bucket(input_=100, output=50, cache_creation=100, cache_read=300)
        pct = usage_report._cache_hit_pct(bucket)
        self.assertAlmostEqual(pct, 60.0, places=1)

    # T5 — outlier by iteration count
    def test_t5_outlier_by_iterations(self):
        pts = [
            _point("a", assistant_count=10),
            _point("b", assistant_count=152),
        ]
        r = usage_report.run(days=7, points=pts, stdout_only=True)
        # high-iteration flagged
        self.assertGreaterEqual(r["agg"]["flags"]["high_iteration"], 1)
        # session b appears in outliers
        ids = [s["session_id"] for s in r["agg"]["outliers"]]
        self.assertIn("b", ids)

    # T6 — outlier by token volume (top 5 by total)
    def test_t6_outlier_by_tokens_top5(self):
        pts = []
        for i in range(8):
            pts.append(_point(
                f"s{i}",
                bucket=_bucket(input_=i * 1000, output=i * 500, cache_creation=0, cache_read=100),
            ))
        r = usage_report.run(days=7, points=pts, stdout_only=True)
        outliers = r["agg"]["outliers"]
        # Top by tokens should be s7 (highest i)
        self.assertEqual(outliers[0]["session_id"], "s7")
        self.assertLessEqual(len(outliers), 5)

    # T7 — sparkline scaling
    def test_t7_sparkline_scaling(self):
        self.assertEqual(usage_report._spark([0, 0, 0]), "   ")
        s = usage_report._spark([1, 0, 10])
        self.assertEqual(s[1], " ")  # empty day is space
        self.assertEqual(s[2], "█")  # tallest is █

    # T8 — --days 30 produces a 30-day window
    def test_t8_days_30_window(self):
        # one point 20 days ago, one 40 days ago
        recent = _point("recent", last_message_ts=NOW - timedelta(days=20))
        old = _point("old", last_message_ts=NOW - timedelta(days=40))
        r = usage_report.run(days=30, points=[recent, old], stdout_only=True)
        self.assertEqual(r["agg"]["session_count"], 1)
        ids = [p["payload"]["session_id"] for p in [recent]]
        self.assertIn("recent", ids)

    # T9 — --stdout-only writes nothing
    def test_t9_stdout_only_no_disk(self):
        r = usage_report.run(days=7, points=[_point("a")], stdout_only=True)
        self.assertIsNone(r["report_path"])

    # T10 — --dry-run skips cleanup AND file write
    def test_t10_dry_run_skips_writes(self):
        zombie = {"id": "z", "payload": {"last_message_ts": NOW.isoformat()}}
        r = usage_report.run(days=7, points=[zombie, _point("a")], dry_run=True)
        self.assertIsNone(r["report_path"])
        self.mock_delete.assert_not_called()
        # cleanup removed = 0 in dry-run
        self.assertEqual(r["cleanup"]["removed"], 0)

    # T11 — file overwrite, doesn't append
    def test_t11_file_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "summary.md"
            r1 = usage_report.run(days=7, points=[_point("a")], output=out)
            first_text = out.read_text()
            r2 = usage_report.run(days=7, points=[_point("b")], output=out)
            second_text = out.read_text()
            self.assertNotEqual(first_text, second_text)
            # No append — second_text should not contain first_text body chunks
            # (markdown headers identical; the session-id outlier line differs)
            self.assertEqual(second_text.count("# Usage Summary"), 1)

    # T12 — zombie cleanup deletes points without data_source
    def test_t12_zombie_cleanup_deletes(self):
        zombies = [
            {"id": "z1", "payload": {"last_message_ts": NOW.isoformat()}},
            {"id": "z2", "payload": {"last_message_ts": NOW.isoformat(), "data_source": None}},
        ]
        good = _point("g1")
        r = usage_report.run(days=7, points=zombies + [good], stdout_only=True)
        self.mock_delete.assert_called_once()
        called_ids = self.mock_delete.call_args[0][0]
        self.assertIn("z1", called_ids)
        self.assertIn("z2", called_ids)
        self.assertNotIn("g1", called_ids)
        self.assertEqual(r["cleanup"]["removed"], 2)

    # T13 — --no-cleanup leaves zombies alone
    def test_t13_no_cleanup_skips_deletes(self):
        zombie = {"id": "z", "payload": {"last_message_ts": NOW.isoformat()}}
        usage_report.run(days=7, points=[zombie], no_cleanup=True, stdout_only=True)
        self.mock_delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
