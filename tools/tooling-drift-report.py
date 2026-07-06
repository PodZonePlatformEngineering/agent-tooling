#!/usr/bin/env python3
"""
tooling-drift-report.py — fleet-wide agent-tooling version + drift visibility
(PROJ-039/T-057, Sharpening 4).

Apex (podzoneAgentTeam) is read-only to agents: the *catalog* Hermes maintains
there is derived and hand-applied by Hermes, never agent-written. This tool is
the derivation — it reads each fleet home repo's SHIPPED
``.claude/tooling-manifest.json`` (PROJ-039/T-055 manifest v2, written by
sync-agent-tooling.sh after a byte-identity PASS) straight off GitHub `main`
via `gh api ... -H "Accept: application/vnd.github.raw"` (no clone required),
and emits a per-agent row: ``{agent, home_repo, version, source_commit,
synced_at}`` plus a drift flag against a canonical version (e.g. "atlas on
v1.0.0, canonical v1.1.0").

Fleet list: parsed from ``migrated-agents.md`` (PROJ-032, read from the apex
clone — ``$PODZONEAGENTTEAM_REPO`` or ``--migrated-agents-path``), filtered to
``status: migrated`` rows, plus ``home-training-template`` (ships the tool set
without being a migrated *agent*). ``--repos`` overrides the fleet list
entirely (comma-separated ``owner/repo`` or bare ``repo`` — bare names resolve
against ``--org``).

Output: a human table to stdout by default; ``--json`` for skill/report
embedding (Hermes's ``/consolidate-tasks`` or ``/usage-report`` wiring — see
the apex wiring note in the PROJ-039/T-057 session response for the exact
call site; this tool does not touch apex).

A missing/unreachable/corrupt manifest is reported as a `flagged` row, never
fatal — one broken repo must not blank the whole fleet table.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

DEFAULT_ORG = "PodZonePlatformEngineering"
DEFAULT_MIGRATED_AGENTS_PATH = (
    "planning/projects/PROJ-032-agent-home-repos/migrated-agents.md"
)
# Ships tooling (update-tooling.py etc.) but is not a migrated *agent* row.
EXTRA_REPOS = ["home-training-template"]

# Matches a well-formed `| a | b | c | d | e | f | ... |` data row with 6 or
# more pipe-delimited cells (PROJ-039/T-057 added a 7th `tooling_version`
# column); only the first 6 cells are captured, extras are ignored.
_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|(?:[^|]*\|)*$"
)


def parse_migrated_agents(text: str) -> list[dict]:
    """Rows with ``status: migrated`` from the migrated-agents.md table.
    Returns ``[{"agent", "team", "home_repo"}, ...]``. Tolerant of the header/
    separator rows, extra trailing columns (e.g. `tooling_version`), and any
    surrounding prose (only well-formed `| a | b | ... |` data rows with at
    least 6 columns match)."""
    fleet = []
    for line in text.splitlines():
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        agent, team, home_repo, status, _migrated_on, _task = (g.strip() for g in m.groups())
        if agent.lower() == "agent" or set(agent) == {"-"}:
            continue  # header / separator row
        if status.lower() != "migrated":
            continue
        if not home_repo.startswith("home-"):
            continue  # e.g. hermes's "(apex / team-lead)"
        fleet.append({"agent": agent, "team": team, "home_repo": home_repo})
    return fleet


def resolve_fleet(*, migrated_agents_path: Optional[str], repos: Optional[str]) -> list[dict]:
    """Resolve the fleet list: ``--repos`` override, or migrated-agents.md +
    EXTRA_REPOS. Raises FileNotFoundError if neither is available."""
    if repos:
        out = []
        for r in repos.split(","):
            r = r.strip()
            if not r:
                continue
            out.append({"agent": r, "team": "", "home_repo": r})
        return out

    path = Path(migrated_agents_path) if migrated_agents_path else None
    if not path or not path.is_file():
        raise FileNotFoundError(
            f"migrated-agents.md not found at {path} — pass --migrated-agents-path "
            f"or --repos to override"
        )
    fleet = parse_migrated_agents(path.read_text(encoding="utf-8"))
    for extra in EXTRA_REPOS:
        if not any(f["home_repo"] == extra for f in fleet):
            fleet.append({"agent": "(template)", "team": "", "home_repo": extra})
    return fleet


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def fetch_manifest(org: str, repo: str, *, ref: str = "main") -> dict:
    """Raw ``.claude/tooling-manifest.json`` off GitHub `main` via `gh api`.
    Raises on any failure (unreachable / 404 / bad JSON) — caller flags it."""
    cp = _run([
        "gh", "api", f"repos/{org}/{repo}/contents/.claude/tooling-manifest.json",
        "-X", "GET", "-f", f"ref={ref}",
        "-H", "Accept: application/vnd.github.raw",
    ])
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout or "gh api failed").strip())
    return json.loads(cp.stdout)


def build_report(fleet: list[dict], *, org: str, canonical_version: str) -> list[dict]:
    rows = []
    for entry in fleet:
        home_repo = entry["home_repo"]
        row = {
            "agent": entry["agent"],
            "team": entry.get("team", ""),
            "home_repo": home_repo,
            "version": None,
            "source_commit": None,
            "synced_at": None,
            "role": None,
            "drift": None,
            "flagged": False,
            "reason": None,
        }
        try:
            manifest = fetch_manifest(org, home_repo)
        except Exception as exc:
            row["flagged"] = True
            row["reason"] = f"manifest-unreachable: {exc}"
            rows.append(row)
            continue

        version = manifest.get("version")
        if not version or not isinstance(manifest.get("files"), dict):
            row["flagged"] = True
            row["reason"] = "manifest-corrupt: missing version or files"
            rows.append(row)
            continue

        row["version"] = version
        row["source_commit"] = manifest.get("source_commit")
        row["synced_at"] = manifest.get("synced_at")
        row["role"] = manifest.get("role")
        row["drift"] = version != canonical_version
        rows.append(row)
    return rows


def render_table(rows: list[dict], *, canonical_version: str) -> str:
    lines = [f"Canonical version: {canonical_version}", ""]
    header = f"{'AGENT':<14}{'HOME REPO':<28}{'VERSION':<10}{'SOURCE_COMMIT':<14}{'SYNCED_AT':<22}{'DRIFT'}"
    lines.append(header)
    lines.append("-" * len(header))
    for row in rows:
        if row["flagged"]:
            lines.append(f"{row['agent']:<14}{row['home_repo']:<28}{'?':<10}{'?':<14}{'?':<22}FLAGGED: {row['reason']}")
            continue
        commit = (row["source_commit"] or "")[:8]
        drift = "DRIFT" if row["drift"] else "ok"
        if row["drift"]:
            drift = f"DRIFT ({row['version']} != {canonical_version})"
        lines.append(
            f"{row['agent']:<14}{row['home_repo']:<28}{row['version']:<10}{commit:<14}"
            f"{(row['synced_at'] or ''):<22}{drift}"
        )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", default=DEFAULT_ORG)
    ap.add_argument("--migrated-agents-path", default=None,
                    help=f"path to migrated-agents.md (default: "
                         f"$PODZONEAGENTTEAM_REPO/{DEFAULT_MIGRATED_AGENTS_PATH})")
    ap.add_argument("--repos", default=None,
                    help="comma-separated repo override — skips migrated-agents.md entirely")
    ap.add_argument("--canonical-version", default=None,
                    help="default: this checkout's VERSION file")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    migrated_agents_path = args.migrated_agents_path
    if not migrated_agents_path and not args.repos:
        import os
        apex = os.environ.get("PODZONEAGENTTEAM_REPO", "")
        if apex:
            migrated_agents_path = str(Path(apex) / DEFAULT_MIGRATED_AGENTS_PATH)

    canonical_version = args.canonical_version
    if not canonical_version:
        version_file = Path(__file__).resolve().parents[1] / "VERSION"
        canonical_version = version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else "unknown"

    try:
        fleet = resolve_fleet(migrated_agents_path=migrated_agents_path, repos=args.repos)
    except FileNotFoundError as exc:
        print(f"tooling-drift-report: {exc}", file=sys.stderr)
        return 2

    rows = build_report(fleet, org=args.org, canonical_version=canonical_version)

    if args.as_json:
        print(json.dumps({"canonical_version": canonical_version, "fleet": rows}, indent=2))
    else:
        print(render_table(rows, canonical_version=canonical_version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
