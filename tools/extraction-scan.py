#!/usr/bin/env python3
"""extraction-scan.py — enforce the mechanical half of the extraction gate.

PROJ-011/T-126 (CC-521). Control of record: ``podzoneTeam/planning/projects/
PROJ-011-academy/session-to-curriculum-extraction-gate.md``. Design rationale and
the tier definitions live in ``lib/extraction_scan.py``; operating instructions in
``docs/extraction-scan.md``.

Modes
-----
  --diff <base-ref>        scan what a branch adds against a base (the CI mode)
  --paths <path> [...]     scan named files or directories
  --substrate <collection> sweep a Qdrant collection's point payloads (B4)

Exit codes
----------
  0  no blocking findings (warnings may have been printed)
  1  blocking findings — tier 1 and/or tier 2 per --fail-on
  2  usage or configuration error

The declaration stays mandatory even when this passes: the scanner checks what is
detectable, the declaring agent checks what is not, and a declaration detects
omission rather than falsification (gate §9).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import extraction_scan as ES  # noqa: E402


def _git(args: list, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        raise ES.ExtractionScanError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def changed_files(base: str, cwd: Path) -> list:
    out = _git(["diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"], cwd)
    return [line.strip() for line in out.splitlines() if line.strip()]


def added_lines_for(path: str, base: str, cwd: Path) -> list:
    """Added lines with their new-file line numbers, parsed from a unified diff.

    Tiers 1 and 3 run over these rather than the whole file: the gate fires on the
    extract, and re-reporting untouched lines in a modified document is the noise
    that gets a scanner switched off.
    """
    out = _git(["diff", "--unified=0", f"{base}...HEAD", "--", path], cwd)
    added: list = []
    line_no = 0
    for line in out.splitlines():
        if line.startswith("@@"):
            try:
                new_span = line.split("+", 1)[1].split(" ", 1)[0]
                line_no = int(new_span.split(",", 1)[0])
            except (IndexError, ValueError):
                line_no = 0
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append((line_no, line[1:]))
            line_no += 1
    return added


def iter_paths(paths: list) -> list:
    out = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(sorted(str(f) for f in p.rglob("*")
                              if f.is_file() and ES.is_text_file(str(f))))
        elif p.is_file():
            out.append(str(p))
    return out


def scan_substrate(collection: str, *, config: ES.DestinationConfig, roster: ES.Roster,
                   limit: int, text_fields: list) -> list:
    """B4 sweep — scan the text payloads of points already in a shared collection.

    Detection after the fact, deliberately and explicitly: an upsert is not a PR, so
    there is no pre-merge moment to hold. See docs/extraction-scan.md § "B4" for why
    this is a sweep rather than a gate, and what is gated inline instead.
    """
    from lib import qdrant_http  # imported lazily: the file modes need no network

    findings: list = []
    offset = None
    scanned = 0
    while scanned < limit:
        body = {"limit": min(64, limit - scanned), "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        raw = qdrant_http.scroll(collection=collection, body=body)
        batch = raw.get("result", raw) if isinstance(raw, dict) else {}
        points = batch.get("points", []) if isinstance(batch, dict) else []
        if not points:
            break
        for point in points:
            payload = point.get("payload") or {}
            pid = point.get("id")
            for field_name in text_fields:
                value = payload.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    continue
                pseudo_path = f"{collection}#{pid}:{field_name}"
                for finding in ES.scan_text(pseudo_path, value, config=config,
                                            roster=roster):
                    # Path-derived boundaries do not apply to a point id; B4 is the
                    # boundary by construction, so re-tag and keep tiers 1 and 3.
                    if finding.tier == ES.TIER_STRUCTURAL:
                        continue
                    finding.boundary = "B4"
                    findings.append(finding)
                for code, message, excerpt, tier in ES.scan_line_tier3(
                        value, roster=roster, boundaries=("B4",)):
                    findings.append(ES.Finding(tier=tier, code=code, path=pseudo_path,
                                               line=0, message=message, excerpt=excerpt,
                                               boundary="B4"))
            scanned += 1
        offset = batch.get("next_page_offset") if isinstance(batch, dict) else None
        if offset is None:
            break
    return findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extraction-scan.py",
        description="Enforce the mechanical half of the extraction gate (PROJ-011/T-126).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--diff", metavar="BASE_REF",
                      help="scan files this branch adds/changes against BASE_REF")
    mode.add_argument("--paths", nargs="+", metavar="PATH",
                      help="scan the named files or directories in full")
    mode.add_argument("--substrate", metavar="COLLECTION",
                      help="sweep a Qdrant collection's payload text (B4)")

    parser.add_argument("--repo", default=".", help="repository root (default: cwd)")
    parser.add_argument("--config", help="destination config JSON (default: data/extraction-destinations.json)")
    parser.add_argument("--roster", help="participant roster JSON (default: $EXTRACTION_ROSTER)")
    parser.add_argument("--brief", help="brief file — cross-check declared vs authorised boundaries")
    parser.add_argument("--check-brief-clause", action="store_true",
                        help="with --brief, also require the T-123 clause to be present and well-formed")
    parser.add_argument("--fail-on", choices=("tier1", "tier2", "both", "any", "none"),
                        default="both", help="which tiers block (default: both = tier 1 + tier 2)")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--substrate-limit", type=int, default=2000,
                        help="max points to sweep (default: 2000)")
    parser.add_argument("--substrate-fields", default="text,body,content,summary,message",
                        help="comma-separated payload fields to scan")
    parser.add_argument("--quiet-warnings", action="store_true",
                        help="suppress tier-3 output entirely")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).resolve()

    try:
        config = ES.DestinationConfig.load(Path(args.config) if args.config else None)
        roster = ES.Roster.load(args.roster)
    except ES.ExtractionScanError as exc:
        print(f"extraction-scan: {exc}", file=sys.stderr)
        return 2

    brief_auth = None
    findings: list = []

    if args.brief:
        brief_path = Path(args.brief)
        if not brief_path.exists():
            print(f"extraction-scan: brief not found: {brief_path}", file=sys.stderr)
            return 2
        brief_auth = ES.parse_brief_authorisation(brief_path.read_text(encoding="utf-8"))
        if args.check_brief_clause:
            for error in brief_auth.errors:
                findings.append(ES.Finding(
                    tier=ES.TIER_STRUCTURAL, code="BRIEF_CLAUSE_MISSING",
                    path=str(brief_path), line=0, message=error))

    try:
        if args.substrate:
            findings.extend(scan_substrate(
                args.substrate, config=config, roster=roster,
                limit=args.substrate_limit,
                text_fields=[f.strip() for f in args.substrate_fields.split(",") if f.strip()]))
        elif args.diff:
            for rel in changed_files(args.diff, repo):
                if not ES.is_text_file(rel):
                    continue
                full = repo / rel
                text = ES.read_text(full) if full.exists() else ""
                if text is None:
                    continue
                findings.extend(ES.scan_text(
                    rel, text, config=config, roster=roster,
                    added_lines=added_lines_for(rel, args.diff, repo),
                    brief=brief_auth))
        else:
            for rel in iter_paths(args.paths):
                text = ES.read_text(Path(rel))
                if text is None:
                    continue
                display = os.path.relpath(rel, repo) if Path(rel).is_absolute() else rel
                findings.extend(ES.scan_text(display, text, config=config, roster=roster,
                                             brief=brief_auth))
    except ES.ExtractionScanError as exc:
        print(f"extraction-scan: {exc}", file=sys.stderr)
        return 2

    buckets = ES.partition(findings)
    blocking = {
        "tier1": [ES.TIER_HARD],
        "tier2": [ES.TIER_STRUCTURAL],
        "both": [ES.TIER_HARD, ES.TIER_STRUCTURAL],
        "any": [ES.TIER_HARD, ES.TIER_STRUCTURAL, ES.TIER_WARN],
        "none": [],
    }[args.fail_on]
    blocked = [f for f in findings if f.tier in blocking]

    if args.format == "json":
        print(json.dumps({
            "findings": [f.__dict__ for f in findings],
            "blocking": len(blocked),
            "roster_configured": roster.configured,
        }, indent=2))
        return 1 if blocked else 0

    for tier, label in ((ES.TIER_HARD, "TIER 1 — hard fail"),
                        (ES.TIER_STRUCTURAL, "TIER 2 — declaration"),
                        (ES.TIER_WARN, "TIER 3 — warning")):
        items = buckets[tier]
        if not items or (tier == ES.TIER_WARN and args.quiet_warnings):
            continue
        print(f"\n{label} ({len(items)})")
        for finding in items:
            print(f"  {finding.format()}")

    print("")
    if not roster.configured:
        print("note: no participant roster configured — the tier-3 name check did not run "
              "(--roster / $EXTRACTION_ROSTER).")
    if blocked:
        print(f"extraction-scan: FAIL — {len(blocked)} blocking finding(s). "
              f"Gate: {ES.GATE_DOC}")
        return 1
    print(f"extraction-scan: pass ({len(findings)} finding(s), none blocking). "
          "A clean scan is not a clean extract — the §7 declaration remains mandatory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
