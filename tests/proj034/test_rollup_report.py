"""Tests for tools/rollup-report.py — PROJ-034/T-011.

T7–T16 cover the algorithmic surface; live verification (cloud scroll, real
session shapes) lives in the PR description.
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


def _load():
    spec = importlib.util.spec_from_file_location(
        "rollup_report", REPO_ROOT / "tools" / "rollup-report.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


rollup_report = _load()


NOW = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)


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
    bucket: dict | None = None,
    model: str = "claude-sonnet-4-6",
    last_message_ts: datetime | None = None,
) -> dict:
    bucket = bucket or _bucket(input_=1000, output=500, cache_creation=200, cache_read=8000)
    last_message_ts = last_message_ts or (NOW - timedelta(days=1))
    return {
        "session_id": session_id,
        "projects": projects,
        "work_items": work_items or [],
        "model_usage": {model: bucket},
        "total_tokens": bucket,
        "last_message_ts": last_message_ts.isoformat(),
        "workspace": "agent-tooling",
        "agent": "hephaestus",
    }


PROGRAMME_MAP_FIXTURE = """# Task Naming Reference

## Project Names → Programme

| ID | Shortform | Programme |
|----|-----------|-----------|
| PROJ-003 | `gitopsapi` | `gitops-product` |
| PROJ-015 | `collab-infra` | `platform-buildout` |
| PROJ-033 | `materialised-context` | `materialised-context` |
| PROJ-034 | `session-cost-observability` | `materialised-context` |

(noise lines that should not match)
| Some | other | table |
"""


class TestProgrammeMap(unittest.TestCase):
    def test_load_parses_table(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "task-naming.md"
            p.write_text(PROGRAMME_MAP_FIXTURE)
            prog, short = rollup_report.load_programme_map(p)
        self.assertEqual(prog["PROJ-034"], "materialised-context")
        self.assertEqual(prog["PROJ-015"], "platform-buildout")
        self.assertEqual(short["PROJ-003"], "gitopsapi")

    def test_load_missing_file_returns_empty(self):
        prog, short = rollup_report.load_programme_map(Path("/nonexistent/file.md"))
        self.assertEqual(prog, {})
        self.assertEqual(short, {})


class TestRollupAggregation(unittest.TestCase):

    def _map(self, td):
        p = Path(td) / "task-naming.md"
        p.write_text(PROGRAMME_MAP_FIXTURE)
        return p

    # T7 — per-project totals from 3-project / 5-session fixture
    def test_t7_per_project_totals(self):
        payloads = [
            _payload(session_id="s1", projects=["PROJ-034"], bucket=_bucket(input_=1000, output=500)),
            _payload(session_id="s2", projects=["PROJ-034"], bucket=_bucket(input_=2000, output=1000)),
            _payload(session_id="s3", projects=["PROJ-033"], bucket=_bucket(input_=500, output=250)),
            _payload(session_id="s4", projects=["PROJ-015"], bucket=_bucket(input_=300, output=100)),
            _payload(session_id="s5", projects=["PROJ-015"], bucket=_bucket(input_=300, output=100)),
        ]
        with tempfile.TemporaryDirectory() as td:
            result = rollup_report.run(
                days=7, programme_map=self._map(td), stdout_only=True, payloads=payloads
            )
        per_p = result["agg"]["per_project"]
        self.assertEqual(per_p["PROJ-034"]["sessions"], 2)
        self.assertEqual(per_p["PROJ-034"]["totals"]["input_tokens"], 3000)
        self.assertEqual(per_p["PROJ-033"]["sessions"], 1)
        self.assertEqual(per_p["PROJ-015"]["sessions"], 2)

    # T8 — shared attribution: multi-project session counts in each project
    def test_t8_shared_attribution(self):
        payloads = [
            _payload(
                session_id="multi",
                projects=["PROJ-034", "PROJ-033"],
                bucket=_bucket(input_=1000, output=500),
            ),
        ]
        with tempfile.TemporaryDirectory() as td:
            result = rollup_report.run(
                days=7, programme_map=self._map(td), stdout_only=True, payloads=payloads
            )
        per_p = result["agg"]["per_project"]
        self.assertEqual(per_p["PROJ-034"]["totals"]["input_tokens"], 1000)
        self.assertEqual(per_p["PROJ-033"]["totals"]["input_tokens"], 1000)
        self.assertEqual(per_p["PROJ-034"]["sessions"], 1)
        self.assertEqual(per_p["PROJ-033"]["sessions"], 1)

    # T9 — programme grouping: 3 projects → 2 programmes
    def test_t9_programme_grouping(self):
        payloads = [
            _payload(session_id="s1", projects=["PROJ-034"], bucket=_bucket(input_=1000)),
            _payload(session_id="s2", projects=["PROJ-033"], bucket=_bucket(input_=500)),
            _payload(session_id="s3", projects=["PROJ-015"], bucket=_bucket(input_=300)),
        ]
        with tempfile.TemporaryDirectory() as td:
            result = rollup_report.run(
                days=7, programme_map=self._map(td), stdout_only=True, payloads=payloads
            )
        prog = result["agg"]["per_programme"]
        # PROJ-034 + PROJ-033 → materialised-context; PROJ-015 → platform-buildout
        self.assertIn("materialised-context", prog)
        self.assertIn("platform-buildout", prog)
        self.assertEqual(prog["materialised-context"]["sessions"], 2)
        self.assertEqual(prog["materialised-context"]["totals"]["input_tokens"], 1500)
        self.assertEqual(prog["platform-buildout"]["sessions"], 1)
        self.assertEqual(prog["materialised-context"]["projects"], {"PROJ-034", "PROJ-033"})

    # T10 — unmapped project → `unmapped`
    def test_t10_unmapped_project(self):
        payloads = [
            _payload(session_id="s1", projects=["PROJ-099"], bucket=_bucket(input_=200)),
        ]
        with tempfile.TemporaryDirectory() as td:
            result = rollup_report.run(
                days=7, programme_map=self._map(td), stdout_only=True, payloads=payloads
            )
        prog = result["agg"]["per_programme"]
        self.assertIn("unmapped", prog)
        self.assertEqual(prog["unmapped"]["projects"], {"PROJ-099"})

    # T11 — missing programme map → all-unmapped + warning footer in markdown
    def test_t11_missing_programme_map(self):
        payloads = [
            _payload(session_id="s1", projects=["PROJ-034"], bucket=_bucket(input_=1000)),
        ]
        result = rollup_report.run(
            days=7,
            programme_map=Path("/no/such/file.md"),
            stdout_only=True,
            payloads=payloads,
        )
        # No map → PROJ-034 goes under `unmapped`
        prog = result["agg"]["per_programme"]
        self.assertEqual(set(prog.keys()), {"unmapped"})
        self.assertIn("Programme map not found", result["markdown"])

    # T12 — top work_items sorting (descending tokens, alpha tie-break)
    def test_t12_top_work_items_sorted(self):
        payloads = [
            _payload(
                session_id="big",
                projects=["PROJ-034"],
                work_items=["PROJ-034/T-008"],
                bucket=_bucket(input_=5000),
            ),
            _payload(
                session_id="med",
                projects=["PROJ-034"],
                work_items=["PROJ-034/T-005"],
                bucket=_bucket(input_=2000),
            ),
            _payload(
                session_id="tie-a",
                projects=["PROJ-034"],
                work_items=["PROJ-034/T-001"],
                bucket=_bucket(input_=100),
            ),
            _payload(
                session_id="tie-b",
                projects=["PROJ-034"],
                work_items=["PROJ-034/T-002"],
                bucket=_bucket(input_=100),
            ),
        ]
        with tempfile.TemporaryDirectory() as td:
            result = rollup_report.run(
                days=7, programme_map=self._map(td), stdout_only=True, payloads=payloads
            )
        md = result["markdown"]
        # Top 2 by tokens
        t8_pos = md.find("PROJ-034/T-008")
        t5_pos = md.find("PROJ-034/T-005")
        self.assertGreater(t5_pos, t8_pos)
        # Tie break: T-001 before T-002 (alphabetical)
        t1_pos = md.find("PROJ-034/T-001")
        t2_pos = md.find("PROJ-034/T-002")
        self.assertGreater(t2_pos, t1_pos)

    # T13 — sparkline values follow per-project daily totals
    def test_t13_sparkline_scaling(self):
        # Two sessions on different days for same project
        day_a = NOW - timedelta(days=3)
        day_b = NOW - timedelta(days=1)
        payloads = [
            _payload(
                session_id="a",
                projects=["PROJ-034"],
                bucket=_bucket(input_=1000),
                last_message_ts=day_a,
            ),
            _payload(
                session_id="b",
                projects=["PROJ-034"],
                bucket=_bucket(input_=4000),
                last_message_ts=day_b,
            ),
        ]
        with tempfile.TemporaryDirectory() as td:
            result = rollup_report.run(
                days=7, programme_map=self._map(td), stdout_only=True, payloads=payloads
            )
        # Sparkline block present + contains a block char (highest day = █)
        self.assertIn("Daily sparkline", result["markdown"])
        self.assertIn("█", result["markdown"])

    # T14 — --days 30 filters out points outside the window
    def test_t14_window_filtering(self):
        real_now = datetime.now(timezone.utc)
        in_window = real_now - timedelta(days=5)
        out_of_window = real_now - timedelta(days=40)
        payloads = [
            _payload(
                session_id="in",
                projects=["PROJ-034"],
                bucket=_bucket(input_=1000),
                last_message_ts=in_window,
            ),
            _payload(
                session_id="out",
                projects=["PROJ-034"],
                bucket=_bucket(input_=9999),
                last_message_ts=out_of_window,
            ),
        ]
        with tempfile.TemporaryDirectory() as td:
            result = rollup_report.run(
                days=30,
                programme_map=self._map(td),
                stdout_only=True,
                payloads=payloads,
            )
        # The 40-day-old session must be excluded
        self.assertEqual(result["agg"]["per_project"]["PROJ-034"]["sessions"], 1)
        self.assertEqual(
            result["agg"]["per_project"]["PROJ-034"]["totals"]["input_tokens"], 1000
        )

    # T15 — --stdout-only skips file write
    def test_t15_stdout_only_skips_write(self):
        payloads = [
            _payload(session_id="s1", projects=["PROJ-034"], bucket=_bucket(input_=100)),
        ]
        with tempfile.TemporaryDirectory() as td:
            result = rollup_report.run(
                days=7,
                programme_map=self._map(td),
                stdout_only=True,
                payloads=payloads,
            )
        self.assertIsNone(result["report_path"])

    # T16 — empty result set → valid empty report
    def test_t16_empty_payloads(self):
        with tempfile.TemporaryDirectory() as td:
            result = rollup_report.run(
                days=7, programme_map=self._map(td), stdout_only=True, payloads=[]
            )
        self.assertIn("Sessions in window: 0", result["markdown"])
        self.assertIn("_None._", result["markdown"])


if __name__ == "__main__":
    unittest.main()
