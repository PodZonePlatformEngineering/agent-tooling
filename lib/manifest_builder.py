"""manifest_builder.py — build trajectory-manifest.yaml for a design (PROJ-038/T-002).

Auto-discovers artefacts touching a given project ID across four sources:

  * `jsonl`     — Claude Code session JSONLs under ~/.claude/projects/ whose
                  cwd basename matches a configured cwd_slug AND whose
                  extracted `projects` list contains the project ID.
  * `outbox`    — Markdown files under {team_root}/team/*/outgoing/ that
                  mention the project ID.
  * `incoming`  — Markdown briefs under {team_root}/team/*/incoming/ that
                  mention the project ID.
  * `qdrant`    — Points in the cloud Qdrant `sessions` collection whose
                  payload `projects` array contains the project ID.

Project-to-cwd-slug mapping (Stage-1 design decision per
team/hephaestus/incoming/2026-05-27-trajectory-manifest-builder.md):
reads `cwd_slugs:` field from `spec.md` frontmatter, falling back to
`READMEFIRST.md` frontmatter — Hermes lean (option 2) generalised to
projects that have no spec.md yet. CLI `--cwd-slug` overrides.

Output schema is the one accepted by `lib.decay.manifest.load_manifest` —
do not duplicate validation here; the CLI re-loads the emitted manifest
to satisfy AC-MB-001.

Determinism (AC-MB-005): sources are scanned in a fixed order; entries are
emitted in chronological order with stable tie-breaks (path / qdrant_id).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from . import jsonl_scrape, work_items_extract

VALID_ROLES = {"hermes", "hephaestus", "atlas", "thoth"}
VALID_SOURCES = ("jsonl", "outbox", "incoming", "qdrant")

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FILENAME_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_PROJECT_DIR_RE = re.compile(r"^(PROJ-\d{3})\b")


@dataclass
class BuilderResult:
    manifest: dict
    verbose_log: list[str] = field(default_factory=list)

    @property
    def artefacts(self) -> list[dict]:
        return self.manifest.get("artefacts", [])


# --------------------------------------------------------------------------- #
# Project metadata
# --------------------------------------------------------------------------- #

def resolve_project_id(project_dir: Path) -> str:
    """Derive PROJ-XXX from the project directory basename.

    Accepts e.g. `PROJ-003-gitopsapi-product` → `PROJ-003`. Raises ValueError
    if the basename does not start with a `PROJ-XXX` prefix; callers should
    pass `--project-id` explicitly in that case.
    """
    m = _PROJECT_DIR_RE.match(project_dir.name)
    if not m:
        raise ValueError(
            f"cannot derive project ID from {project_dir.name!r}; "
            f"pass --project-id explicitly"
        )
    return m.group(1)


def _read_frontmatter(md_path: Path) -> dict:
    if not md_path.is_file():
        return {}
    text = md_path.read_text(encoding="utf-8", errors="replace")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        loaded = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def resolve_cwd_slugs(project_dir: Path) -> list[str]:
    """Read cwd_slugs from spec.md frontmatter, falling back to READMEFIRST.md.

    Returns [] if neither file declares the field. Callers should warn the
    user and skip the jsonl source if the override flag is also absent.
    """
    for name in ("spec.md", "READMEFIRST.md"):
        fm = _read_frontmatter(project_dir / name)
        slugs = fm.get("cwd_slugs")
        if isinstance(slugs, list) and slugs:
            return [str(s) for s in slugs if isinstance(s, str)]
    return []


# --------------------------------------------------------------------------- #
# Helpers shared across sources
# --------------------------------------------------------------------------- #

def _to_iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _file_timestamp(path: Path) -> str:
    """Prefer a filename-embedded ISO date (midnight UTC) over mtime.

    Filename dates are stable across filesystems; mtimes drift on copy.
    Determinism (AC-MB-005) hinges on this.
    """
    m = _FILENAME_DATE_RE.search(path.name)
    if m:
        try:
            return datetime.fromisoformat(
                m.group(1) + "T00:00:00+00:00"
            ).isoformat()
        except ValueError:
            pass
    return _to_iso_utc(
        datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    )


def _role_for_agent(agent: Optional[str]) -> str:
    if agent and agent.lower() in VALID_ROLES:
        return agent.lower()
    return "other"


def _role_from_team_path(path: Path) -> str:
    """`{team_root}/team/{agent}/outgoing/x.md` → `{agent}` (or 'other')."""
    parts = path.parts
    try:
        team_idx = parts.index("team")
    except ValueError:
        return "other"
    if team_idx + 1 >= len(parts):
        return "other"
    return _role_for_agent(parts[team_idx + 1])


def _within_window(
    ts_iso: str,
    start_iso: Optional[str],
    end_iso: Optional[str],
) -> bool:
    if start_iso and ts_iso < start_iso:
        return False
    if end_iso and ts_iso > end_iso:
        return False
    return True


def _project_mention_re(project_id: str) -> re.Pattern[str]:
    n = int(project_id.split("-")[1])
    return re.compile(
        rf"\bproj-?0*{n}\b",
        re.IGNORECASE,
    )


# --------------------------------------------------------------------------- #
# Per-source scanners
# --------------------------------------------------------------------------- #

def scan_outbox_artefacts(
    project_id: str,
    team_root: Path,
    start_iso: Optional[str] = None,
    end_iso: Optional[str] = None,
    verbose_log: Optional[list[str]] = None,
) -> list[dict]:
    return _scan_team_markdown(
        project_id, team_root, "outgoing", "outbox",
        start_iso, end_iso, verbose_log,
    )


def scan_incoming_artefacts(
    project_id: str,
    team_root: Path,
    start_iso: Optional[str] = None,
    end_iso: Optional[str] = None,
    verbose_log: Optional[list[str]] = None,
) -> list[dict]:
    return _scan_team_markdown(
        project_id, team_root, "incoming", "brief",
        start_iso, end_iso, verbose_log,
    )


def _scan_team_markdown(
    project_id: str,
    team_root: Path,
    subdir: str,
    artefact_type: str,
    start_iso: Optional[str],
    end_iso: Optional[str],
    verbose_log: Optional[list[str]],
) -> list[dict]:
    out: list[dict] = []
    mention_re = _project_mention_re(project_id)
    team_dir = team_root / "team"
    if not team_dir.is_dir():
        if verbose_log is not None:
            verbose_log.append(
                f"[{artefact_type}] team dir not found: {team_dir} — skipping"
            )
        return out
    for agent_dir in sorted(team_dir.iterdir()):
        agent_subdir = agent_dir / subdir
        if not agent_subdir.is_dir():
            continue
        for md in sorted(agent_subdir.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                if verbose_log is not None:
                    verbose_log.append(f"[{artefact_type}] read failed: {md}")
                continue
            if not mention_re.search(text):
                if verbose_log is not None:
                    verbose_log.append(
                        f"[{artefact_type}] reject (no {project_id} mention): {md}"
                    )
                continue
            ts = _file_timestamp(md)
            if not _within_window(ts, start_iso, end_iso):
                if verbose_log is not None:
                    verbose_log.append(
                        f"[{artefact_type}] reject (outside window): {md}"
                    )
                continue
            try:
                rel = md.relative_to(team_root)
                path_str = str(rel)
            except ValueError:
                path_str = str(md)
            entry = {
                "path": path_str,
                "type": artefact_type,
                "timestamp": ts,
                "role": _role_from_team_path(md),
            }
            out.append(entry)
            if verbose_log is not None:
                verbose_log.append(f"[{artefact_type}] include: {path_str}")
    return out


def scan_jsonl_artefacts(
    project_id: str,
    cwd_slugs: list[str],
    start_iso: Optional[str] = None,
    end_iso: Optional[str] = None,
    projects_root: Optional[Path] = None,
    verbose_log: Optional[list[str]] = None,
) -> list[dict]:
    """Scan ~/.claude/projects/<cwd-slug>/*.jsonl via lib.jsonl_scrape.

    A JSONL is included if its scraped `projects` list contains `project_id`.
    """
    if not cwd_slugs:
        if verbose_log is not None:
            verbose_log.append(
                "[jsonl] no cwd_slugs declared (add `cwd_slugs:` to "
                "spec.md/READMEFIRST.md or pass --cwd-slug); skipping"
            )
        return []

    root = projects_root or (Path.home() / ".claude" / "projects")
    out: list[dict] = []
    slug_set = set(cwd_slugs)
    for proj_dir in sorted(root.iterdir() if root.is_dir() else []):
        if not proj_dir.is_dir():
            continue
        decoded = _decode_project_dirname(proj_dir.name)
        if Path(decoded).name not in slug_set:
            continue
        for jsonl in sorted(proj_dir.glob("*.jsonl")):
            try:
                payload = jsonl_scrape.scrape(jsonl)
            except (FileNotFoundError, OSError) as exc:
                if verbose_log is not None:
                    verbose_log.append(f"[jsonl] scrape failed: {jsonl} ({exc})")
                continue
            if project_id not in (payload.get("projects") or []):
                if verbose_log is not None:
                    verbose_log.append(
                        f"[jsonl] reject (no {project_id} in extracted "
                        f"projects): {jsonl.name}"
                    )
                continue
            first = payload.get("first_message_ts")
            if not first:
                if verbose_log is not None:
                    verbose_log.append(
                        f"[jsonl] reject (no first_message_ts): {jsonl.name}"
                    )
                continue
            ts = _normalise_ts(first)
            if not _within_window(ts, start_iso, end_iso):
                if verbose_log is not None:
                    verbose_log.append(
                        f"[jsonl] reject (outside window): {jsonl.name}"
                    )
                continue
            entry = {
                "path": str(jsonl.resolve()),
                "type": "transcript-ref",
                "timestamp": ts,
                "role": _role_for_agent(payload.get("agent")),
            }
            out.append(entry)
            if verbose_log is not None:
                verbose_log.append(f"[jsonl] include: {jsonl.name}")
    return out


def scan_qdrant_artefacts(
    project_id: str,
    start_iso: Optional[str] = None,
    end_iso: Optional[str] = None,
    api_key: Optional[str] = None,
    verbose_log: Optional[list[str]] = None,
) -> list[dict]:
    """Scroll the cloud `sessions` collection for points referencing project_id.

    Local JSONLs scanned via `scan_jsonl_artefacts` are skipped here by
    matching on `jsonl_path` / `session_id` — Qdrant entries are emitted only
    when the underlying transcript is no longer on disk (per F-1-002 — older
    sessions may exist only in the cloud).
    """
    try:
        from . import sessions_reader
    except ImportError as exc:
        if verbose_log is not None:
            verbose_log.append(f"[qdrant] reader unavailable: {exc}")
        return []

    # `projects` is a payload array, not necessarily a server-side index;
    # scroll the whole collection and filter client-side. The `sessions`
    # collection is bounded (low hundreds of points) so this is cheap.
    try:
        points = list(sessions_reader.scroll_all_sessions(api_key=api_key))
    except (sessions_reader.SessionsReaderError, ValueError) as exc:
        if verbose_log is not None:
            verbose_log.append(f"[qdrant] scroll failed: {exc}")
        return []

    out: list[dict] = []
    for p in points:
        if project_id not in (p.get("projects") or []):
            continue
        session_id = p.get("session_id")
        first = p.get("first_message_ts")
        if not session_id or not first:
            if verbose_log is not None:
                verbose_log.append(
                    f"[qdrant] reject (missing session_id/first_ts): {p!r}"
                )
            continue
        ts = _normalise_ts(first)
        if not _within_window(ts, start_iso, end_iso):
            if verbose_log is not None:
                verbose_log.append(
                    f"[qdrant] reject (outside window): {session_id}"
                )
            continue
        entry = {
            "qdrant_collection": "sessions",
            "qdrant_id": session_id,
            "type": "session",
            "timestamp": ts,
            "role": _role_for_agent(p.get("agent")),
        }
        out.append(entry)
        if verbose_log is not None:
            verbose_log.append(f"[qdrant] include: {session_id}")
    return out


# --------------------------------------------------------------------------- #
# Builder entry point
# --------------------------------------------------------------------------- #

def build(
    project_dir: Path,
    team_root: Path,
    *,
    project_id: Optional[str] = None,
    cwd_slugs: Optional[list[str]] = None,
    include: Iterable[str] = VALID_SOURCES,
    start_iso: Optional[str] = None,
    end_iso: Optional[str] = None,
    qdrant_api_key: Optional[str] = None,
    projects_root: Optional[Path] = None,
    verbose: bool = False,
) -> BuilderResult:
    log: list[str] = []
    pid = project_id or resolve_project_id(project_dir)
    slugs = cwd_slugs if cwd_slugs is not None else resolve_cwd_slugs(project_dir)

    include_set = set(include)
    unknown = include_set - set(VALID_SOURCES)
    if unknown:
        raise ValueError(
            f"unknown --include source(s): {sorted(unknown)}; "
            f"valid: {list(VALID_SOURCES)}"
        )

    artefacts: list[dict] = []
    if "outbox" in include_set:
        artefacts.extend(
            scan_outbox_artefacts(pid, team_root, start_iso, end_iso, log)
        )
    if "incoming" in include_set:
        artefacts.extend(
            scan_incoming_artefacts(pid, team_root, start_iso, end_iso, log)
        )
    if "jsonl" in include_set:
        artefacts.extend(
            scan_jsonl_artefacts(
                pid, slugs, start_iso, end_iso, projects_root, log,
            )
        )
    if "qdrant" in include_set:
        qdrant_entries = scan_qdrant_artefacts(
            pid, start_iso, end_iso, qdrant_api_key, log,
        )
        # Dedupe: drop Qdrant points whose session_id matches a local JSONL
        # stem (the same session, surfaced via two sources).
        local_session_ids = {
            Path(a["path"]).stem for a in artefacts
            if a.get("type") == "transcript-ref" and a.get("path")
        }
        for q in qdrant_entries:
            if q.get("qdrant_id") in local_session_ids:
                log.append(
                    f"[qdrant] dedupe (covered by local JSONL): "
                    f"{q['qdrant_id']}"
                )
                continue
            artefacts.append(q)

    # Deterministic order: chronological; tie-break on stable identity.
    artefacts.sort(key=lambda a: (a["timestamp"], a.get("path") or a.get("qdrant_id") or ""))

    manifest = {"artefacts": artefacts}
    if not verbose:
        log = []
    return BuilderResult(manifest=manifest, verbose_log=log)


# --------------------------------------------------------------------------- #
# Path helpers (kept local — session_metadata's decoder is private)
# --------------------------------------------------------------------------- #

def _decode_project_dirname(name: str) -> str:
    if not name.startswith("-"):
        return name
    return "/" + name[1:].replace("-", "/")


def _normalise_ts(ts: str) -> str:
    """Accept ISO 8601 with trailing Z; return ISO with timezone."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return str(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def dump_yaml(manifest: dict) -> str:
    """Serialise a manifest deterministically (block style, key order preserved)."""
    return yaml.safe_dump(
        manifest,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
