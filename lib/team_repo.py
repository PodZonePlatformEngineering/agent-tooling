"""
team_repo.py — resolve the *team repo* a team lead consolidates / launches against,
from the lead's identity (PROJ-039/T-038).

The load-bearing case (the one this module exists to get right): a **migrated
team-lead home repo** where the lead's ``home_repo`` and the team repo they lead
DIVERGE. Athena leads ``trainingTeam`` but her ``home_repo`` becomes
``home-training-athena``. Local-mode ``/consolidate-tasks`` (and ``/launch-session``)
historically assumed the current repo *was* the team repo — true for the legacy
fissioned layout (``home_repo == trainingTeam``), false once the lead is migrated.
This resolver makes the team repo explicit from identity rather than from CWD, so
the coordination skills resolve + consolidate the **right** repo when home≠team.

Resolution by ``home_repo``:
  * ``podzoneAgentTeam``                 → apex / full mode (Hermes); team repo = apex.
  * ``trainingTeam`` / ``roadmapTeam``   → legacy fissioned; CWD *is* the team repo.
  * ``home-<team>-<agent>``              → migrated team-lead; team is decoded from the
                                           home-repo name (``home-training-athena`` →
                                           ``training`` → ``trainingTeam``). home≠team.

Public API:
    resolve_team_repo(identity, *, workspace_root=None) -> dict
    team_from_home_repo(home_repo) -> str | None

CLI:
    python3 team_repo.py --home-repo home-training-athena [--role-class ...] [--json]
        Prints the resolved team repo (JSON, or shell-eval ``KEY=value`` lines).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional


GITHUB_ORG = "PodZonePlatformEngineering"

# Canonical team → team-repo mapping. The key is the short team token that appears
# in a ``home-<team>-<agent>`` repo name; the value is the team repo dir name.
TEAM_REPO_NAMES: dict[str, str] = {
    "podzone": "podzoneAgentTeam",
    "training": "trainingTeam",
    "roadmap": "roadmapTeam",
}

# The apex repo — full ("apex") consolidate mode runs here.
APEX_REPO = "podzoneAgentTeam"

_HOME_REPO_RE = re.compile(r"^home-(?P<team>[a-z0-9]+)-(?P<agent>[a-z0-9]+)$")


def team_from_home_repo(home_repo: str) -> Optional[str]:
    """Return the short team token encoded in a ``home-<team>-<agent>`` repo name.

    ``home-training-athena`` → ``training``. Returns None for non-home repo names.
    """
    if not home_repo:
        return None
    m = _HOME_REPO_RE.match(home_repo.strip())
    return m.group("team") if m else None


def _workspace_root(workspace_root: Optional[str]) -> Path:
    """Where sibling team repos live (``~/workspace`` by default, env-overridable)."""
    if workspace_root:
        return Path(workspace_root)
    env = os.environ.get("PODZONE_WORKSPACE_ROOT")
    if env:
        return Path(env)
    return Path.home() / "workspace"


def resolve_team_repo(
    identity: dict,
    *,
    workspace_root: Optional[str] = None,
) -> dict:
    """Resolve the team repo a (team-lead) identity consolidates / launches against.

    ``identity`` is the parsed identity YAML (needs at least ``home_repo``; ``team``
    and ``role_class`` are used when present). Returns::

        {
          "mode": "apex" | "local",       # apex = Hermes full mode; local = fissioned lead
          "is_team_lead": bool,           # role_class contains "team-lead"
          "team": "training",             # short team token
          "team_repo": "trainingTeam",    # team repo dir name
          "github_repo": "PodZonePlatformEngineering/trainingTeam",
          "local_path": "/abs/.../trainingTeam",
          "tasklist_path": "/abs/.../trainingTeam/planning/team-tasklist.md",
          "status_path":   "/abs/.../trainingTeam/planning/STATUS.md",
          "home_repo": "home-training-athena",
          "separate_from_home": True,     # the load-bearing home_repo != team_repo case
        }

    Never raises for a well-formed identity; raises ValueError only if ``home_repo``
    is absent (the one field the resolution cannot proceed without).
    """
    home_repo = (identity.get("home_repo") or "").strip()
    if not home_repo:
        raise ValueError("identity has no home_repo — cannot resolve team repo")

    role_class = str(identity.get("role_class") or "")
    is_team_lead = "team-lead" in role_class

    root = _workspace_root(workspace_root)

    # 1) Apex (Hermes): home_repo is the apex repo itself → full mode.
    if home_repo == APEX_REPO:
        team = "podzone"
        team_repo = APEX_REPO
        mode = "apex"
    # 2) Legacy fissioned team lead: home_repo IS a team repo (CWD == team repo).
    elif home_repo in TEAM_REPO_NAMES.values():
        team_repo = home_repo
        team = next(t for t, r in TEAM_REPO_NAMES.items() if r == home_repo)
        mode = "local"
    # 3) Migrated team-lead home repo: decode team from home-<team>-<agent>.
    #    Prefer the decoded token, but fall back to the identity `team` field when
    #    the name does not encode a *known* team (keeps the resolver robust to home
    #    repos whose token is opaque).
    else:
        decoded = team_from_home_repo(home_repo)
        team = decoded if decoded in TEAM_REPO_NAMES else (
            (identity.get("team") or "").strip().lower() or decoded or ""
        )
        team_repo = TEAM_REPO_NAMES.get(team)
        if not team_repo:
            raise ValueError(
                f"cannot resolve team repo for home_repo={home_repo!r} "
                f"(team token {team!r} not in {sorted(TEAM_REPO_NAMES)})"
            )
        mode = "apex" if team_repo == APEX_REPO else "local"

    local_path = root / team_repo
    return {
        "mode": mode,
        "is_team_lead": is_team_lead,
        "team": team,
        "team_repo": team_repo,
        "github_repo": f"{GITHUB_ORG}/{team_repo}",
        "local_path": str(local_path),
        "tasklist_path": str(local_path / "planning" / "team-tasklist.md"),
        "status_path": str(local_path / "planning" / "STATUS.md"),
        "home_repo": home_repo,
        "separate_from_home": team_repo != home_repo,
    }


# ---------------------------------------------------------------------------
# CLI — used by the coordination skills to resolve the team repo without
# re-implementing the home-<team>-<agent> decoding in bash.
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Resolve the team repo for a team lead.")
    p.add_argument("--home-repo", required=True)
    p.add_argument("--role-class", default="agenticflows/roles/team-lead/")
    p.add_argument("--team", default="")
    p.add_argument("--workspace-root", default="")
    p.add_argument("--json", action="store_true",
                   help="emit JSON (default: shell-eval KEY=value lines)")
    args = p.parse_args(argv)

    identity = {
        "home_repo": args.home_repo,
        "role_class": args.role_class,
        "team": args.team,
    }
    try:
        res = resolve_team_repo(
            identity,
            workspace_root=args.workspace_root or None,
        )
    except ValueError as exc:
        print(f"error: {exc}", flush=True)
        return 1

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        for k, v in res.items():
            print(f"TEAM_REPO_{k.upper()}={v}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
