"""
session_metadata.py — resolve workspace + agent from a Claude Code session cwd.

Used by PROJ-034 scraper, backfill, Stop hook, /session-end skill, and budi.

Public API:
    resolve(cwd: str | None = None, jsonl_path: str | Path | None = None) -> dict
        Returns {"workspace": str, "agent": str | None, "cwd": str}

The resolver is intentionally best-effort on the agent field — workspaces with no
matching identity file return agent=None rather than raising.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Optional

try:
    import yaml  # PyYAML
except ImportError:
    yaml = None  # type: ignore[assignment]


HOME = Path.home()

# Directories scanned for identity YAML files. Order matters for tie-breaking:
# primary podzoneAgentTeam wins, then fissioned teams.
IDENTITY_DIRS = (
    HOME / "workspace" / "podzoneAgentTeam" / "workspaces" / "identity",
    HOME / "workspace" / "trainingTeam" / "workspaces" / "identity",
    HOME / "workspace" / "roadmapTeam" / "workspaces" / "identity",
)

# Session-dir convention: ~/sessions/{agent}-YYYY-MM-DD-{slug}/{repo}
SESSION_DIR_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9]*)-\d{4}-\d{2}-\d{2}-")


def _decode_project_dirname(name: str) -> str:
    """Decode ~/.claude/projects/-Users-... dir name back to an absolute path.

    The encoding is: replace every `/` in the path with `-`. So decoding is:
    insert `/` everywhere there is a `-`. Leading `-` -> leading `/`.
    """
    if not name.startswith("-"):
        return name
    return "/" + name[1:].replace("-", "/")


def _derive_cwd_from_jsonl(jsonl_path: Path) -> str:
    return _decode_project_dirname(jsonl_path.parent.name)


def _session_agent(cwd: Path) -> Optional[str]:
    """Match a `~/sessions/{agent}-YYYY-MM-DD-*` ancestor and return the agent."""
    sessions_root = HOME / "sessions"
    try:
        rel = cwd.relative_to(sessions_root)
    except ValueError:
        return None
    if not rel.parts:
        return None
    m = SESSION_DIR_RE.match(rel.parts[0])
    return m.group(1).lower() if m else None


def _identity_files(dirs: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for d in dirs:
        if not d.is_dir():
            continue
        out.extend(sorted(d.glob("*.identity.yaml")))
    return out


def _identity_matches(doc: dict, workspace: str) -> bool:
    if doc.get("home_repo") == workspace:
        return True
    for repo in doc.get("repos") or []:
        if isinstance(repo, dict) and repo.get("name") == workspace:
            return True
    return False


def _identity_workspace_field(doc: dict) -> Optional[str]:
    """The workspace name embedded in the identity (e.g. martin-hephaestus-agent-tooling)."""
    val = doc.get("workspace")
    return str(val) if isinstance(val, str) else None


def _identity_lookup(workspace: str, cwd: Path) -> Optional[str]:
    """Return the agent for workspace via identity-file scan, or None."""
    if yaml is None:
        return None
    matches: list[tuple[Path, dict]] = []
    for f in _identity_files(IDENTITY_DIRS):
        try:
            with f.open("r", encoding="utf-8") as fh:
                doc = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(doc, dict):
            continue
        if _identity_matches(doc, workspace):
            matches.append((f, doc))

    if not matches:
        return None

    # Tie-break: prefer identity whose `workspace:` field contains the repo basename
    # of cwd (i.e. closest fit to the user's actual workspace context).
    cwd_basename = cwd.name
    if len(matches) > 1:
        preferred = [
            (f, d)
            for f, d in matches
            if (_identity_workspace_field(d) or "").endswith(cwd_basename)
        ]
        if preferred:
            matches = preferred

    # Deterministic pick: alphabetical by filename (sorted already by _identity_files
    # within each dir; matches across dirs preserve dir order).
    matches.sort(key=lambda fd: fd[0].name)
    agent = matches[0][1].get("agent")
    return str(agent).lower() if agent else None


def resolve(
    cwd: Optional[str] = None,
    jsonl_path: Optional[str | Path] = None,
) -> dict:
    """Resolve workspace + agent from a cwd or a session JSONL path.

    Raises ValueError if neither input is provided.
    """
    if cwd is None and jsonl_path is None:
        raise ValueError("resolve() requires either cwd or jsonl_path")

    if cwd is None:
        cwd_str = _derive_cwd_from_jsonl(Path(jsonl_path))  # type: ignore[arg-type]
    else:
        cwd_str = cwd

    cwd_str = os.path.normpath(cwd_str)
    cwd_path = Path(cwd_str)
    workspace = cwd_path.name or cwd_str

    agent = _session_agent(cwd_path)
    if agent is None:
        agent = _identity_lookup(workspace, cwd_path)

    return {"workspace": workspace, "agent": agent, "cwd": cwd_str}
