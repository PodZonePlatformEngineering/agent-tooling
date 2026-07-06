"""Tests for tools/tooling-drift-report.py — fleet tooling-drift visibility
(PROJ-039/T-057). Live gh api calls are mocked (subprocess.run patched); the
parser/drift-computation logic is exercised directly against fixture text and
fake manifests, per the brief's "manifest-parse, drift computation, repo-list
parsing" test list. The live fleet run itself is proven in the session
response, not here (network + gh auth dependent).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

spec = importlib.util.spec_from_file_location(
    "tooling_drift_report", str(REPO_ROOT / "tools" / "tooling-drift-report.py")
)
drift_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drift_report)  # type: ignore


FIXTURE_MIGRATED_AGENTS = """\
# Migrated agents registry

| agent | team | home_repo | status | migrated_on | task |
|---|---|---|---|---|---|
| hephaestus | podzone | home-podzone-hephaestus | migrated | 2026-06-16 | PROJ-039/T-011 C2a |
| atlas | podzone | home-podzone-atlas | migrated | 2026-06-26 | PROJ-039/T-011 C2b |
| hermes | podzone | (apex / team-lead) | legacy | — | — |
| kronos | roadmap | home-roadmap-kronos | migrated | 2026-06-29 | PROJ-039/T-037 C2c |

Some trailing prose that must not be mistaken for a table row.
"""

# Current live shape (PROJ-039/T-057): a 7th `tooling_version` column, derived
# Hermes-side and never agent-written. _ROW_RE must tolerate this without
# dropping to the hardcoded template row (the T-062 bug — see brief CC-372).
FIXTURE_MIGRATED_AGENTS_7COL = """\
# Migrated agents registry

| agent | team | home_repo | status | migrated_on | task | tooling_version |
|---|---|---|---|---|---|---|
| hephaestus | podzone | home-podzone-hephaestus | migrated | 2026-06-16 | PROJ-039/T-011 C2a | 1.1.1 ✓ |
| atlas | podzone | home-podzone-atlas | migrated | 2026-06-26 | PROJ-039/T-011 C2b | 1.1.1 ✓ |
| hermes | podzone | (apex / team-lead) | legacy | — | — | n/a (apex) |
| kronos | roadmap | home-roadmap-kronos | migrated | 2026-06-29 | PROJ-039/T-037 C2c | 1.1.1 ✓ |

Some trailing prose that must not be mistaken for a table row.
"""


class TestParseMigratedAgents(unittest.TestCase):
    def test_only_migrated_home_rows(self) -> None:
        fleet = drift_report.parse_migrated_agents(FIXTURE_MIGRATED_AGENTS)
        agents = [f["agent"] for f in fleet]
        self.assertEqual(agents, ["hephaestus", "atlas", "kronos"])

    def test_legacy_and_apex_rows_excluded(self) -> None:
        fleet = drift_report.parse_migrated_agents(FIXTURE_MIGRATED_AGENTS)
        self.assertNotIn("hermes", [f["agent"] for f in fleet])

    def test_home_repo_and_team_carried(self) -> None:
        fleet = drift_report.parse_migrated_agents(FIXTURE_MIGRATED_AGENTS)
        atlas = next(f for f in fleet if f["agent"] == "atlas")
        self.assertEqual(atlas["home_repo"], "home-podzone-atlas")
        self.assertEqual(atlas["team"], "podzone")

    def test_seven_column_tooling_version_shape(self) -> None:
        """T-062/CC-372: post-T-057 registries add a 7th column; the parser
        must still return the full fleet, not just the hardcoded template row."""
        fleet = drift_report.parse_migrated_agents(FIXTURE_MIGRATED_AGENTS_7COL)
        agents = [f["agent"] for f in fleet]
        self.assertEqual(agents, ["hephaestus", "atlas", "kronos"])

    def test_seven_column_home_repo_and_team_carried(self) -> None:
        fleet = drift_report.parse_migrated_agents(FIXTURE_MIGRATED_AGENTS_7COL)
        atlas = next(f for f in fleet if f["agent"] == "atlas")
        self.assertEqual(atlas["home_repo"], "home-podzone-atlas")
        self.assertEqual(atlas["team"], "podzone")


class TestResolveFleet(unittest.TestCase):
    def test_repos_override_short_circuits_file(self) -> None:
        fleet = drift_report.resolve_fleet(migrated_agents_path=None, repos="repo-a, repo-b")
        self.assertEqual([f["home_repo"] for f in fleet], ["repo-a", "repo-b"])

    def test_file_path_plus_extra_repos(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "migrated-agents.md"
            p.write_text(FIXTURE_MIGRATED_AGENTS, encoding="utf-8")
            fleet = drift_report.resolve_fleet(migrated_agents_path=str(p), repos=None)
        home_repos = [f["home_repo"] for f in fleet]
        self.assertIn("home-training-template", home_repos)
        self.assertEqual(home_repos.count("home-training-template"), 1)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            drift_report.resolve_fleet(migrated_agents_path="/no/such/file.md", repos=None)


class TestBuildReport(unittest.TestCase):
    def _fleet(self):
        return [
            {"agent": "hephaestus", "team": "podzone", "home_repo": "home-podzone-hephaestus"},
            {"agent": "atlas", "team": "podzone", "home_repo": "home-podzone-atlas"},
            {"agent": "thoth", "team": "podzone", "home_repo": "home-podzone-thoth"},
        ]

    def test_current_vs_drifted_vs_missing(self) -> None:
        def fake_fetch(org, repo, ref="main"):
            if repo == "home-podzone-hephaestus":
                return {"version": "1.1.0", "source_commit": "a" * 40, "synced_at": "t", "role": "coder", "files": {}}
            if repo == "home-podzone-atlas":
                return {"version": "1.0.0", "source_commit": "b" * 40, "synced_at": "t", "role": "coder", "files": {}}
            raise RuntimeError("404")

        with patch.object(drift_report, "fetch_manifest", fake_fetch):
            rows = drift_report.build_report(self._fleet(), org="org", canonical_version="1.1.0")

        by_agent = {r["agent"]: r for r in rows}
        self.assertFalse(by_agent["hephaestus"]["flagged"])
        self.assertFalse(by_agent["hephaestus"]["drift"])
        self.assertFalse(by_agent["atlas"]["flagged"])
        self.assertTrue(by_agent["atlas"]["drift"])
        self.assertTrue(by_agent["thoth"]["flagged"])
        self.assertIn("404", by_agent["thoth"]["reason"])

    def test_corrupt_manifest_flagged_not_fatal(self) -> None:
        def fake_fetch(org, repo, ref="main"):
            return {"version": None, "files": None}  # corrupt shape

        with patch.object(drift_report, "fetch_manifest", fake_fetch):
            rows = drift_report.build_report(self._fleet()[:1], org="org", canonical_version="1.1.0")
        self.assertTrue(rows[0]["flagged"])
        self.assertIn("manifest-corrupt", rows[0]["reason"])

    def test_one_flagged_repo_does_not_blank_the_rest(self) -> None:
        def fake_fetch(org, repo, ref="main"):
            if repo == "home-podzone-atlas":
                raise RuntimeError("unreachable")
            return {"version": "1.1.0", "source_commit": "c" * 40, "synced_at": "t", "role": "coder", "files": {}}

        with patch.object(drift_report, "fetch_manifest", fake_fetch):
            rows = drift_report.build_report(self._fleet(), org="org", canonical_version="1.1.0")
        self.assertEqual(len(rows), 3)
        self.assertTrue(any(r["flagged"] for r in rows))
        self.assertTrue(any(not r["flagged"] for r in rows))


class TestRenderAndJSON(unittest.TestCase):
    def test_json_round_trips(self) -> None:
        def fake_fetch(org, repo, ref="main"):
            return {"version": "1.1.0", "source_commit": "d" * 40, "synced_at": "t", "role": "coder", "files": {}}

        with patch.object(drift_report, "fetch_manifest", fake_fetch):
            rows = drift_report.build_report(
                [{"agent": "hephaestus", "team": "podzone", "home_repo": "home-podzone-hephaestus"}],
                org="org", canonical_version="1.1.0",
            )
        blob = json.dumps({"canonical_version": "1.1.0", "fleet": rows})
        parsed = json.loads(blob)
        self.assertEqual(parsed["fleet"][0]["agent"], "hephaestus")

    def test_table_mentions_drift_reason(self) -> None:
        rows = [{
            "agent": "atlas", "team": "podzone", "home_repo": "home-podzone-atlas",
            "version": "1.0.0", "source_commit": "e" * 40, "synced_at": "t", "role": "coder",
            "drift": True, "flagged": False, "reason": None,
        }]
        table = drift_report.render_table(rows, canonical_version="1.1.0")
        self.assertIn("DRIFT", table)
        self.assertIn("1.0.0", table)
        self.assertIn("1.1.0", table)


if __name__ == "__main__":
    unittest.main()
