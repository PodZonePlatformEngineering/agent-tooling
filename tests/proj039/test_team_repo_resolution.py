"""T-038 — team-repo resolution for a (migrated) team lead (PROJ-039/CC-337).

The load-bearing behaviour: local-mode `/consolidate-tasks` and `/launch-session`
run from a *team-lead home repo* must resolve + operate on the SEPARATE team repo,
not the home repo. Athena leads `trainingTeam` but her `home_repo` is
`home-training-athena`. Before T-038 the coordination skills keyed on the current
repo being the team repo (true for the legacy fissioned layout `home_repo ==
trainingTeam`, false once migrated). `lib.team_repo` makes the team repo explicit
from identity and is the testable core of the fix.

Regression target: the `home_repo != team_repo` path resolves to `trainingTeam`
(tasklist / STATUS / GitHub repo all under trainingTeam), with `separate_from_home`
flagged so the skills know to operate on a repo other than CWD.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import team_repo  # noqa: E402

WS = "/tmp/ws"  # deterministic workspace root for path assertions


class TestTeamFromHomeRepo(unittest.TestCase):
    def test_decodes_team_token(self) -> None:
        self.assertEqual(team_repo.team_from_home_repo("home-training-athena"), "training")
        self.assertEqual(team_repo.team_from_home_repo("home-roadmap-clio"), "roadmap")
        self.assertEqual(team_repo.team_from_home_repo("home-podzone-hephaestus"), "podzone")

    def test_non_home_names_return_none(self) -> None:
        self.assertIsNone(team_repo.team_from_home_repo("trainingTeam"))
        self.assertIsNone(team_repo.team_from_home_repo("podzoneTeam"))
        self.assertIsNone(team_repo.team_from_home_repo(""))


class TestMigratedTeamLead(unittest.TestCase):
    """home_repo != team_repo — the case T-038 exists to get right."""

    ATHENA = {
        "home_repo": "home-training-athena",
        "role_class": "agenticflows/roles/team-lead/",
        "team": "training",
    }

    def test_resolves_to_trainingteam_not_home(self) -> None:
        res = team_repo.resolve_team_repo(self.ATHENA, workspace_root=WS)
        self.assertEqual(res["team"], "training")
        self.assertEqual(res["team_repo"], "trainingTeam")
        self.assertEqual(res["github_repo"], "PodZonePlatformEngineering/trainingTeam")
        self.assertEqual(res["local_path"], f"{WS}/trainingTeam")
        self.assertEqual(res["mode"], "local")
        self.assertTrue(res["is_team_lead"])

    def test_separate_from_home_flagged(self) -> None:
        res = team_repo.resolve_team_repo(self.ATHENA, workspace_root=WS)
        self.assertTrue(res["separate_from_home"],
                        "home_repo != team_repo must be flagged so skills target the team repo")
        self.assertNotEqual(res["local_path"], f"{WS}/home-training-athena")

    def test_tasklist_and_status_under_team_repo(self) -> None:
        res = team_repo.resolve_team_repo(self.ATHENA, workspace_root=WS)
        self.assertEqual(res["tasklist_path"], f"{WS}/trainingTeam/planning/team-tasklist.md")
        self.assertEqual(res["status_path"], f"{WS}/trainingTeam/planning/STATUS.md")

    def test_team_token_falls_back_to_identity_when_name_opaque(self) -> None:
        # If the home repo name does not encode the team, fall back to the team field.
        ident = {"home_repo": "home-x-y", "team": "training",
                 "role_class": "agenticflows/roles/team-lead/"}
        res = team_repo.resolve_team_repo(ident, workspace_root=WS)
        self.assertEqual(res["team_repo"], "trainingTeam")


class TestLegacyAndApex(unittest.TestCase):
    def test_legacy_fissioned_home_is_team_repo(self) -> None:
        # Before migration: home_repo IS the team repo (CWD == team repo).
        ident = {"home_repo": "trainingTeam", "role_class": "agenticflows/roles/team-lead/"}
        res = team_repo.resolve_team_repo(ident, workspace_root=WS)
        self.assertEqual(res["team_repo"], "trainingTeam")
        self.assertFalse(res["separate_from_home"])
        self.assertEqual(res["mode"], "local")

    def test_apex_hermes_full_mode(self) -> None:
        ident = {"home_repo": "podzoneTeam", "role_class": "agenticflows/roles/team-lead/"}
        res = team_repo.resolve_team_repo(ident, workspace_root=WS)
        self.assertEqual(res["team_repo"], "podzoneTeam")
        self.assertEqual(res["mode"], "apex")
        self.assertFalse(res["separate_from_home"])


class TestErrors(unittest.TestCase):
    def test_missing_home_repo_raises(self) -> None:
        with self.assertRaises(ValueError):
            team_repo.resolve_team_repo({"role_class": "team-lead"}, workspace_root=WS)

    def test_unknown_team_raises(self) -> None:
        with self.assertRaises(ValueError):
            team_repo.resolve_team_repo(
                {"home_repo": "home-marketing-bob"}, workspace_root=WS)


if __name__ == "__main__":
    unittest.main()
