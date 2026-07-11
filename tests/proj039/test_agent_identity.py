"""Tests for lib/agent_identity.py — PROJ-039/T-099 (CC-409).

Single-source home-repo identity resolution from the identity YAML. The lived
failure this guards: SUBSTRATE_BASE never shipped `session-context.py`, so
`_resolve_agent_and_role` decayed to ("unknown", "unknown") in every migrated
home repo — masking the F15 team-lead skip and misdirecting the 2026-07-11
`home-podzone-hermes` halt to Qdrant/API-key propagation. Fail loud, never
"unknown".
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from lib import agent_identity  # noqa: E402
from lib.agent_identity import IdentityUnresolved  # noqa: E402


def write_identity(repo: Path, text: str, name: str = "martin-x.identity.yaml") -> Path:
    ident = repo / "workspaces" / "identity"
    ident.mkdir(parents=True, exist_ok=True)
    p = ident / name
    p.write_text(text, encoding="utf-8")
    return p


def yaml_for(agent: str, role_class: str, scope: str = "podzone") -> str:
    return (
        f"operator: Martin\n"
        f"operator_mode: system-owner\n"
        f"agent: {agent}\n"
        f"scope: {scope}\n"
        f"role_class: {role_class}\n"
        f"repos:\n"
        f"  - name: agent-tooling\n"
        f"    purpose: sync source\n"
        f'task_filter: "Claude-Code"\n'
    )


class TestHappyPathAllRoles(unittest.TestCase):
    """Resolution happy path per role class — all 9 VALID_ROLES."""

    def test_every_valid_role_resolves(self) -> None:
        for role in agent_identity.VALID_ROLES:
            with tempfile.TemporaryDirectory() as td:
                repo = Path(td) / "somerepo"
                write_identity(repo, yaml_for("Hephaestus", f"agenticflows/roles/{role}/"))
                agent, resolved, scope = agent_identity.resolve(repo)
                self.assertEqual((agent, resolved, scope),
                                 ("Hephaestus", role, "podzone"),
                                 f"role class {role!r} misresolved")

    def test_team_lead_apex_maps_to_team_lead(self) -> None:
        """`team-lead-apex` is a role_class variant, not a role (Hermes's live
        YAML) — it must resolve to the `team-lead` role."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "home-podzone-hermes"
            write_identity(repo, yaml_for("Hermes", "agenticflows/roles/team-lead-apex/"))
            _agent, role, _scope = agent_identity.resolve(repo)
            self.assertEqual(role, "team-lead")

    def test_unrecognised_role_class_halts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "somerepo"
            write_identity(repo, yaml_for("Hephaestus", "agenticflows/roles/wizard/"))
            with self.assertRaises(IdentityUnresolved) as cm:
                agent_identity.resolve(repo)
            self.assertIn("identity-unresolved", cm.exception.reason)
            self.assertIn("wizard", cm.exception.reason)


class TestFlatYamlHardening(unittest.TestCase):
    """Stdlib flat parse: inline comments and surrounding quotes must be
    stripped. Lived failure: `agent: Hermes  # FILL IN…` parsed to the whole
    rest-of-line."""

    def test_inline_comment_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "somerepo"
            write_identity(repo, (
                "agent: Hermes  # FILL IN — capitalised agent name confirmed\n"
                "scope: programme # trailing\n"
                "role_class: agenticflows/roles/team-lead-apex/   # variant\n"
            ))
            agent, role, scope = agent_identity.resolve(repo)
            self.assertEqual((agent, role, scope), ("Hermes", "team-lead", "programme"))

    def test_quoted_values_unquoted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "somerepo"
            write_identity(repo, (
                'agent: "Athena"\n'
                "scope: 'training'\n"
                "role_class: \"agenticflows/roles/trainer/\"\n"
            ))
            agent, role, scope = agent_identity.resolve(repo)
            self.assertEqual((agent, role, scope), ("Athena", "trainer", "training"))

    def test_indented_and_list_lines_ignored(self) -> None:
        """Rich identity YAML (repos list, comments) parses to top-level scalars
        only — an indented `name:` inside the repos list must not leak."""
        fields = agent_identity.parse_flat_yaml(yaml_for(
            "Hephaestus", "agenticflows/roles/coder/"))
        self.assertEqual(fields.get("agent"), "Hephaestus")
        self.assertNotIn("name", fields)
        self.assertNotIn("purpose", fields)

    def test_value_containing_hash_without_space_survives(self) -> None:
        # Only ` #` (whitespace + hash) starts an inline comment.
        self.assertEqual(agent_identity.parse_flat_yaml("scope: a#b\n"),
                         {"scope": "a#b"})


class TestFailLoud(unittest.TestCase):
    """Missing / ambiguous / placeholder YAML → IdentityUnresolved with a
    sentinel-ready `identity-unresolved: …` reason naming the YAML path."""

    def test_missing_yaml_halts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "somerepo"
            repo.mkdir()
            with self.assertRaises(IdentityUnresolved) as cm:
                agent_identity.resolve(repo)
            self.assertTrue(cm.exception.reason.startswith("identity-unresolved:"))
            self.assertIn("workspaces", cm.exception.reason)

    def test_ambiguous_two_yamls_halts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "somerepo"
            write_identity(repo, yaml_for("A", "agenticflows/roles/coder/"),
                           name="a.identity.yaml")
            write_identity(repo, yaml_for("B", "agenticflows/roles/coder/"),
                           name="b.identity.yaml")
            with self.assertRaises(IdentityUnresolved) as cm:
                agent_identity.resolve(repo)
            self.assertIn("ambiguous", cm.exception.reason)
            self.assertIn("a.identity.yaml", cm.exception.reason)
            self.assertIn("b.identity.yaml", cm.exception.reason)

    def test_placeholder_agent_halts(self) -> None:
        for placeholder in ("FILL_IN", "FILL IN", "fill-in"):
            with tempfile.TemporaryDirectory() as td:
                repo = Path(td) / "somerepo"
                write_identity(repo, yaml_for(placeholder, "agenticflows/roles/coder/"))
                with self.assertRaises(IdentityUnresolved) as cm:
                    agent_identity.resolve(repo)
                self.assertIn("agent missing/placeholder", cm.exception.reason)
                self.assertIn(".identity.yaml", cm.exception.reason)

    def test_missing_role_class_halts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "somerepo"
            write_identity(repo, "agent: Hephaestus\nscope: podzone\n")
            with self.assertRaises(IdentityUnresolved) as cm:
                agent_identity.resolve(repo)
            self.assertIn("role_class missing/placeholder", cm.exception.reason)

    def test_placeholder_scope_degrades_to_unknown_not_halt(self) -> None:
        """scope is informational only (Hermes's pre-fix YAML carried
        `scope: FILL_IN`) — never fail-loud on scope."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "somerepo"
            write_identity(repo, yaml_for("Hephaestus", "agenticflows/roles/coder/",
                                          scope="FILL_IN"))
            _agent, _role, scope = agent_identity.resolve(repo)
            self.assertEqual(scope, "unknown")


class TestRepoBasenameCrossCheck(unittest.TestCase):
    """home-{team}-{agent} basename encodes the agent — a YAML agent that
    disagrees is a copy-pasted identity and must halt. Non-home-* repos skip."""

    def test_matching_basename_passes_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "home-podzone-hephaestus"
            write_identity(repo, yaml_for("Hephaestus", "agenticflows/roles/coder/"))
            agent, _role, _scope = agent_identity.resolve(repo)
            self.assertEqual(agent, "Hephaestus")

    def test_mismatch_halts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "home-podzone-hephaestus"
            write_identity(repo, yaml_for("Hermes", "agenticflows/roles/coder/"))
            with self.assertRaises(IdentityUnresolved) as cm:
                agent_identity.resolve(repo)
            self.assertIn("copy-pasted", cm.exception.reason)
            self.assertIn("Hermes", cm.exception.reason)
            self.assertIn("hephaestus", cm.exception.reason)

    def test_non_home_repo_skips_cross_check(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "podzoneTeam"
            write_identity(repo, yaml_for("Hermes", "agenticflows/roles/team-lead/"))
            agent, _role, _scope = agent_identity.resolve(repo)
            self.assertEqual(agent, "Hermes")


class TestValidRolesSharedConstant(unittest.TestCase):
    """VALID_ROLES must stay consistent with sync-agent-tooling.sh and
    scaffold.sh (the brief's ONE-shared-constant requirement) — token drift in
    any of the three reintroduces the unknown-role misroute."""

    def _shell_roles(self, script: str) -> list[str]:
        text = (REPO_ROOT / script).read_text(encoding="utf-8")
        m = re.search(r'^VALID_ROLES="([^"]+)"', text, re.MULTILINE)
        self.assertIsNotNone(m, f"VALID_ROLES not found in {script}")
        return m.group(1).split()

    def test_matches_sync_and_scaffold(self) -> None:
        for script in ("sync-agent-tooling.sh", "scaffold.sh"):
            self.assertEqual(list(agent_identity.VALID_ROLES),
                             self._shell_roles(script),
                             f"lib/agent_identity.VALID_ROLES drifted from {script}")

    def test_no_role_is_substring_shadowed(self) -> None:
        """The longest-first token match must give every role a class path that
        resolves to itself (e.g. `trainer` must not be shadowed by `trainee`)."""
        for role in agent_identity.VALID_ROLES:
            resolved = agent_identity._role_from_class(
                f"agenticflows/roles/{role}/", Path("x.identity.yaml"))
            self.assertEqual(resolved, role)


if __name__ == "__main__":
    unittest.main()
