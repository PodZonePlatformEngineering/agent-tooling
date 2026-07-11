"""
agent_identity.py — single-source home-repo identity resolution (PROJ-039/T-099,
CC-409).

Replaces the legacy 4-step `session-context.py::resolve_agent` chain for home
repos. That chain was never shipped by SUBSTRATE_BASE, so every migrated home
repo decayed to ("unknown", "unknown") — masking the T-078 F15 team-lead skip
and misdirecting the 2026-07-11 `home-podzone-hermes` halt to Qdrant/API-key
propagation. Here the home repo's own identity YAML
(`{repo}/workspaces/identity/*.identity.yaml`) is the ONE source of truth.

Fail loud, never "unknown": missing / ambiguous (≠1 match) / placeholder YAML,
an unrecognised role_class, or a repo-basename mismatch all raise
:class:`IdentityUnresolved` whose ``.reason`` is the sentinel-ready
``identity-unresolved: …`` string naming the YAML path. ``scope`` is the one
informational exception — a missing/placeholder scope degrades to "unknown"
(Hermes's live YAML carries ``scope: FILL_IN``; scope drives no routing).

Stdlib-only, Python 3.9 (home-repo runtime closure — no `yaml` dependency).
The flat parse reads only column-0 scalar keys, strips inline comments
(whitespace + ``#`` onward) and surrounding quotes; the lived failure was
``agent: Hermes  # FILL IN…`` regex-parsing to the whole rest-of-line.
"""

from __future__ import annotations

import re
from pathlib import Path

# The 9 role classes — MUST stay consistent with `VALID_ROLES` in
# sync-agent-tooling.sh and scaffold.sh. `team-lead-apex` (a role_class variant,
# not a role) maps to `team-lead` via substring match.
VALID_ROLES = (
    "team-lead",
    "coder",
    "archivist",
    "trainer",
    "cluster-operator",
    "curriculum-developer",
    "historian",
    "strategist",
    "trainee",
)

_PLACEHOLDER = re.compile(r"FILL[ _-]?IN", re.IGNORECASE)
_TOP_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")


class IdentityUnresolved(Exception):
    """Identity could not be resolved from the home repo's identity YAML.

    ``.reason`` is the sentinel-ready string (``identity-unresolved: …``) that
    session-materialise writes into ``.materialise-status.json``.
    """

    def __init__(self, detail: str):
        self.reason = f"identity-unresolved: {detail}"
        super().__init__(self.reason)


def _clean_value(raw: str) -> str:
    """A flat-YAML scalar: quoted → unquoted content; else inline comment
    (whitespace + '#' onward) stripped."""
    v = raw.strip()
    if not v:
        return ""
    if v[0] in "\"'":
        end = v.find(v[0], 1)
        if end != -1:
            return v[1:end]
    if v.startswith("#"):
        return ""
    m = re.search(r"\s#", v)
    if m:
        v = v[: m.start()]
    return v.strip()


def parse_flat_yaml(text: str) -> dict:
    """Column-0 scalar ``key: value`` pairs only — indented lines (lists, block
    scalars like ``notes: >`` continuations) are ignored, so a rich identity
    YAML (repos/mcp/notes) parses to just its top-level fields."""
    fields: dict = {}
    for line in text.splitlines():
        if not line or line[0] in " \t#-":
            continue
        m = _TOP_KEY.match(line)
        if not m:
            continue
        key, raw = m.groups()
        value = _clean_value(raw)
        if value:
            fields[key] = value
    return fields


def _role_from_class(role_class: str, yaml_path: Path) -> str:
    """role_class path → role, by token match against VALID_ROLES (longest
    token first so a more-specific role never loses to a substring sibling).
    `…/team-lead-apex/` contains "team-lead" → team-lead."""
    for token in sorted(VALID_ROLES, key=len, reverse=True):
        if token in role_class:
            return token
    raise IdentityUnresolved(
        f"role_class '{role_class}' in {yaml_path} matches none of "
        f"{', '.join(VALID_ROLES)}"
    )


def resolve(repo_root) -> "tuple[str, str, str]":
    """Resolve ``(agent, role, scope)`` from ``repo_root``'s identity YAML.

    Raises :class:`IdentityUnresolved` on: no YAML, >1 YAML, missing/placeholder
    ``agent`` or ``role_class``, unrecognised role_class, or a ``home-{team}-
    {agent}`` repo basename whose agent segment ≠ the YAML agent (a copy-pasted
    identity). Repos not matching the ``home-*`` pattern skip the cross-check.
    """
    root = Path(repo_root)
    ident_dir = root / "workspaces" / "identity"
    matches = sorted(ident_dir.glob("*.identity.yaml")) if ident_dir.is_dir() else []
    if not matches:
        raise IdentityUnresolved(
            f"no identity YAML at {ident_dir}/*.identity.yaml"
        )
    if len(matches) > 1:
        raise IdentityUnresolved(
            f"ambiguous identity — {len(matches)} YAMLs under {ident_dir}: "
            f"{', '.join(m.name for m in matches)}"
        )
    yaml_path = matches[0]

    fields = parse_flat_yaml(yaml_path.read_text(encoding="utf-8"))

    agent = fields.get("agent", "")
    if not agent or _PLACEHOLDER.search(agent):
        raise IdentityUnresolved(
            f"agent missing/placeholder in {yaml_path} (agent={agent!r})"
        )
    role_class = fields.get("role_class", "")
    if not role_class or _PLACEHOLDER.search(role_class):
        raise IdentityUnresolved(
            f"role_class missing/placeholder in {yaml_path} "
            f"(role_class={role_class!r})"
        )
    role = _role_from_class(role_class, yaml_path)

    scope = fields.get("scope", "")
    if not scope or _PLACEHOLDER.search(scope):
        scope = "unknown"  # informational only — never fail-loud on scope

    # Repo-basename cross-check (operator-confirmed reliable post-rollout):
    # home-{team}-{agent} encodes the agent as the final hyphen segment.
    basename = root.resolve().name
    parts = basename.split("-")
    if parts[0] == "home" and len(parts) >= 3:
        derived = parts[-1]
        if agent.lower() != derived.lower():
            raise IdentityUnresolved(
                f"identity YAML agent '{agent}' != repo-derived agent "
                f"'{derived}' (basename '{basename}') — {yaml_path} looks "
                f"copy-pasted from another scaffold"
            )

    return agent, role, scope
