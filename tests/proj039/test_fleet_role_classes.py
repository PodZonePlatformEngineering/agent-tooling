"""Regression test for PROJ-039/T-101 (CC-420): every role_class a real fleet
identity YAML carries must be accepted by BOTH consumers of the role-detection
seam — update-tooling.py detect_role() and sync-agent-tooling.sh's role
validation. Lived failure (T-066 Phase 2 apex shakedown, F20): Hermes's
identity carries `role_class: agenticflows/roles/team-lead-apex/`;
detect_role() surfaced `team-lead-apex`, sync refused it (`unknown
role-class`), the ok:false sentinel HALTed the apex repo's SessionStart — the
apex could not receive a normal TOOLING_UPDATE without the
TOOLING_UPDATE_ROLE=team-lead override.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib.agent_identity import VALID_ROLES  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "update_tooling", str(REPO_ROOT / "tools" / "update-tooling.py")
)
update_tooling = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update_tooling)  # type: ignore

# Every role_class token a real fleet identity YAML carries: the 9 role
# classes plus the `team-lead-apex` variant (Hermes's live identity).
FLEET_ROLE_CLASSES = list(VALID_ROLES) + ["team-lead-apex"]


def _stub_home_repo(base: Path, role_class_token: str) -> Path:
    repo = base / "home"
    ident = repo / "workspaces" / "identity"
    ident.mkdir(parents=True)
    (ident / "t.identity.yaml").write_text(
        f"role_class: agenticflows/roles/{role_class_token}/\n", encoding="utf-8"
    )
    return repo


def _run_sync(repo: Path, *role_args: str) -> str:
    cp = subprocess.run(
        ["bash", str(REPO_ROOT / "sync-agent-tooling.sh"), *role_args,
         "--home-repo", str(repo), "--agent-tooling", str(REPO_ROOT), "--yes"],
        capture_output=True, text=True,
    )
    return (cp.stdout or "") + (cp.stderr or "")


class TestDetectRoleAcceptsFleetRoleClasses(unittest.TestCase):
    def test_every_fleet_role_class_detected_verbatim(self) -> None:
        for token in FLEET_ROLE_CLASSES:
            with tempfile.TemporaryDirectory() as td:
                repo = _stub_home_repo(Path(td), token)
                self.assertEqual(
                    update_tooling.detect_role(str(repo)), token,
                    f"detect_role must surface {token!r} verbatim")


class TestSyncAcceptsFleetRoleClasses(unittest.TestCase):
    """Role validation sits BEFORE the .claude/hooks presence check, so an
    accepted role fails later (`.claude/hooks/ not found`) while a refused one
    prints `unknown role-class` — assertions key on the refusal string only,
    and the sync never writes anywhere on this path."""

    def test_sync_refuses_no_fleet_role_class_via_autodetect(self) -> None:
        for token in FLEET_ROLE_CLASSES:
            with tempfile.TemporaryDirectory() as td:
                repo = _stub_home_repo(Path(td), token)
                out = _run_sync(repo)
                self.assertIn(f"Auto-detected role '{token}'", out)
                self.assertNotIn(
                    "unknown role-class", out,
                    f"sync refused fleet role_class {token!r}:\n{out}")

    def test_team_lead_apex_aliases_to_team_lead_file_set(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = _stub_home_repo(Path(td), "team-lead-apex")
            out = _run_sync(repo, "--role", "team-lead-apex")
            self.assertNotIn("unknown role-class", out)
            self.assertIn(
                "Role-class variant 'team-lead-apex' → applying the "
                "'team-lead' file set", out)


if __name__ == "__main__":
    unittest.main()
