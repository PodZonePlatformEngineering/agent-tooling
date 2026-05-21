"""Tests for tools/efficiency-report.py — PROJ-034/T-012.

T17–T24 cover the algorithmic surface; live verification (cloud scroll, real
session shapes) lives in the PR description.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _load():
    spec = importlib.util.spec_from_file_location(
        "efficiency_report", REPO_ROOT / "tools" / "efficiency-report.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


efficiency_report = _load()


def _bucket(input_=0, output=0, cache_creation=0, cache_read=0):
    return {
        "input_tokens": input_,
        "output_tokens": output,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
    }


def _payload(
    *,
    session_id: str,
    projects: list[str],
    work_items: list[str] | None = None,
    agent: str | None = "hephaestus",
    workspace: str = "agent-tooling",
    model_usage: dict | None = None,
    last_days_ago: int = 1,
) -> dict:
    last_message_ts = datetime.now(timezone.utc) - timedelta(days=last_days_ago)
    mu = model_usage or {"claude-sonnet-4-6": _bucket(input_=1000, output=500, cache_creation=200, cache_read=8000)}
    total = _bucket()
    for b in mu.values():
        for k in total:
            total[k] += b.get(k, 0)
    return {
        "session_id": session_id,
        "projects": projects,
        "work_items": work_items or [],
        "agent": agent,
        "workspace": workspace,
        "model_usage": mu,
        "total_tokens": total,
        "last_message_ts": last_message_ts.isoformat(),
    }


def _grand_total(ct: dict) -> int:
    """Sum tokens across every cell."""
    return sum(
        efficiency_report._sum_total(cell)
        for row in ct["cells"].values()
        for cell in row.values()
    )


class TestCrossTab(unittest.TestCase):

    # T17 — model × project: totals match per-cell + per-row + per-column
    def test_t17_project_totals_consistent(self):
        payloads = [
            _payload(
                session_id="s1",
                projects=["PROJ-034"],
                model_usage={"claude-sonnet-4-6": _bucket(input_=1000, output=500)},
            ),
            _payload(
                session_id="s2",
                projects=["PROJ-033"],
                model_usage={"claude-opus-4-7": _bucket(input_=2000, output=1000)},
            ),
            _payload(
                session_id="s3",
                projects=["PROJ-034"],
                model_usage={"claude-opus-4-7": _bucket(input_=500, output=200)},
            ),
        ]
        result = efficiency_report.run(days=30, stdout_only=True, payloads=payloads)
        ct = result["cross_tabs"]["project"]
        # Row totals sum to grand
        row_sum = sum(efficiency_report._sum_total(b) for b in ct["row_totals"].values())
        col_sum = sum(efficiency_report._sum_total(b) for b in ct["col_totals"].values())
        grand = _grand_total(ct)
        self.assertEqual(row_sum, grand)
        self.assertEqual(col_sum, grand)
        # Specific cell: opus-4-7 × PROJ-034 = 700 (input+output)
        self.assertEqual(
            efficiency_report._sum_total(ct["cells"]["claude-opus-4-7"]["PROJ-034"]),
            700,
        )

    # T18 — model × work_item: top-10 selection ranks by token volume
    def test_t18_work_item_top10(self):
        payloads = []
        # 12 work items with descending tokens
        for i in range(12):
            tokens = (12 - i) * 1000
            payloads.append(
                _payload(
                    session_id=f"s{i}",
                    projects=["PROJ-034"],
                    work_items=[f"PROJ-034/T-{i:03d}"],
                    model_usage={
                        "claude-sonnet-4-6": _bucket(input_=tokens, output=0)
                    },
                )
            )
        result = efficiency_report.run(days=30, stdout_only=True, payloads=payloads)
        ct = result["cross_tabs"]["work_item"]
        self.assertEqual(len(ct["columns"]), 10)
        # First column = T-000 (highest tokens), last in top-10 = T-009
        self.assertEqual(ct["columns"][0], "PROJ-034/T-000")
        self.assertEqual(ct["columns"][-1], "PROJ-034/T-009")
        self.assertNotIn("PROJ-034/T-010", ct["columns"])
        self.assertNotIn("PROJ-034/T-011", ct["columns"])

    # T19 — model × agent: null-agent in multi-agent workspace excluded
    def test_t19_null_agent_excluded(self):
        payloads = [
            _payload(session_id="a", projects=["PROJ-034"], agent="hephaestus", workspace="ws1"),
            _payload(session_id="b", projects=["PROJ-034"], agent="hermes", workspace="ws1"),
            _payload(session_id="c", projects=["PROJ-034"], agent=None, workspace="ws1"),
            _payload(session_id="d", projects=["PROJ-034"], agent=None, workspace="ws-solo"),
        ]
        result = efficiency_report.run(days=30, stdout_only=True, payloads=payloads)
        ct = result["cross_tabs"]["agent"]
        # session "c" should be excluded (ws1 has 2 agents); session "d" included as "unknown"
        self.assertEqual(ct["excluded"], 1)
        self.assertIn("hephaestus", ct["columns"])
        self.assertIn("hermes", ct["columns"])
        self.assertIn("unknown", ct["columns"])

    # T20 — cache hit % per cell
    def test_t20_cache_hit_pct(self):
        # input 1000, cache_read 9000 → 9000/10000 = 90%
        payloads = [
            _payload(
                session_id="s",
                projects=["PROJ-034"],
                model_usage={
                    "claude-sonnet-4-6": _bucket(input_=1000, cache_read=9000)
                },
            ),
        ]
        result = efficiency_report.run(days=30, stdout_only=True, payloads=payloads)
        self.assertIn("90%", result["markdown"])

    # T21 — observations section detects concentration
    def test_t21_concentration_observation(self):
        # opus puts 100% of its tokens in PROJ-034
        payloads = [
            _payload(
                session_id="o1",
                projects=["PROJ-034"],
                model_usage={
                    "claude-opus-4-7": _bucket(input_=5000, output=0)
                },
            ),
            _payload(
                session_id="o2",
                projects=["PROJ-033"],
                model_usage={
                    "claude-sonnet-4-6": _bucket(input_=500, output=0)
                },
            ),
        ]
        result = efficiency_report.run(days=30, stdout_only=True, payloads=payloads)
        md = result["markdown"]
        self.assertIn("concentration:", md)
        self.assertIn("opus-4-7 → PROJ-034", md)

    # T22 — zero-token cells render "n/a" in cache hit, not "0%"
    def test_t22_zero_token_cells_na(self):
        # opus only touches PROJ-034; sonnet only touches PROJ-033
        payloads = [
            _payload(
                session_id="s1",
                projects=["PROJ-034"],
                model_usage={"claude-opus-4-7": _bucket(input_=1000, cache_read=5000)},
            ),
            _payload(
                session_id="s2",
                projects=["PROJ-033"],
                model_usage={"claude-sonnet-4-6": _bucket(input_=1000, cache_read=5000)},
            ),
        ]
        result = efficiency_report.run(
            days=30, stdout_only=True, payloads=payloads, dimensions=("project",)
        )
        md = result["markdown"]
        # The cache hit table must show n/a for the zero-token cells
        self.assertIn("n/a", md)

    # T23 — --dimensions filter
    def test_t23_dimensions_filter(self):
        payloads = [_payload(session_id="s1", projects=["PROJ-034"])]
        result = efficiency_report.run(
            days=30,
            dimensions=("project",),
            stdout_only=True,
            payloads=payloads,
        )
        # Only project cross-tab present
        self.assertIn("project", result["cross_tabs"])
        self.assertNotIn("agent", result["cross_tabs"])
        self.assertNotIn("work_item", result["cross_tabs"])
        self.assertNotIn("model × agent", result["markdown"])
        self.assertNotIn("model × top-", result["markdown"])

    # T24 — empty result set → valid empty report
    def test_t24_empty_result_set(self):
        result = efficiency_report.run(days=30, stdout_only=True, payloads=[])
        self.assertIn("Sessions in window: 0", result["markdown"])
        # No cells but markdown still valid
        self.assertIn("Model Efficiency", result["markdown"])


if __name__ == "__main__":
    unittest.main()
