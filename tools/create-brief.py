#!/usr/bin/env python3
"""
create-brief.py — brief authoring (PROJ-039/T-043).

Sibling of ``create-session-point.py``, but for the general-purpose `briefs`
collection. A brief is a FIRST-CLASS point, NOT keyed to a session: the Team Lead
(or a curriculum author, for a trainee) upserts the brief point from the
committed brief file; sessions later reference it by ``brief_id`` and the runtime
sid is appended to ``session_ids[]`` at SessionStart (SessionStart materialise).

The point id is ``uuid5(NAMESPACE_DNS, brief_id)`` so re-authoring the same
``brief_id`` converges on the same point (idempotent). Embedding is OPTIONAL
(PROJ-041/T-002): the `brief` named vector is embedded from the brief body
(token-safe per T-027; the full body is stored unchanged) only when an embed
endpoint is explicitly configured via the ``OLLAMA_HOST`` env var — otherwise
the point is written vector-less with a stderr note and the PROJ-042
enrichment job embeds in retrospect.

`approved` is the ADR-008 D5 execution gate — a brief must be `approved` before a
session can be materialised for it. Author with `--status draft` (default) and
promote with `--approve` (or `--status approved`) on operator/lead sign-off.

Usage:
  create-brief.py --brief-id training/2026-07-02-prompt-basics-alex \
      --team training --author athena --assignee alex --assignee-type trainee \
      --body-file <path> [--work-item PROJ-XXX/T-YYY ...] [--summary <text>] \
      [--origin-path <committed-file>] [--status draft|approved] [--approve]

Auth: PODZONE_QDRANT_APIKEY (or wrap in `mcp__secrets__secret_run -k
      podzone_qdrant_apikey`). Exits non-zero on failure (loud — a dispatch that
      silently fails to create the brief would strand the agent at session start).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib import brief_substrate  # noqa: E402
from lib import extraction_scan as ES  # noqa: E402


def _gate_body(body: str, *, skip: bool) -> int:
    """B4 gate — a brief upsert is a write into the shared substrate (gate §2).

    Substrate writes are not pull requests, so CI never sees them, and B4 is the one
    boundary with no remediation story: the PROJ-013 remedy (mint a clean repo, delete
    the old) has no equivalent in a vector store agents read from continuously. So the
    check runs here, at the write path, which is the only place it can run at all.

    Tier 1 blocks. The missing T-123 clause warns rather than blocks, deliberately:
    turning it into a hard failure would strand a Team Lead mid-dispatch on briefs
    authored before the clause existed. Promote it once the fleet has converged.
    """
    if skip:
        print("create-brief: extraction scan SKIPPED by --skip-extraction-scan "
              "(recorded on the point)", file=sys.stderr)
        return 0

    roster = ES.Roster.load(None)
    findings = []
    for line_no, line in enumerate(body.splitlines(), start=1):
        for code, message, excerpt, tier in ES.scan_line_tier1(
                line, roster=roster, boundaries=("B4",)):
            if tier == ES.TIER_HARD:
                findings.append(ES.Finding(tier=tier, code=code, path="<brief body>",
                                           line=line_no, message=message,
                                           excerpt=excerpt, boundary="B4"))

    auth = ES.parse_brief_authorisation(body)
    for error in auth.errors:
        print(f"create-brief: warning — {error} (T-123 clause; see "
              f"docs/brief-authoring.md)", file=sys.stderr)

    if findings:
        print("create-brief: REFUSING to upsert — the brief body carries tier-1 "
              "material and B4 has no revocation remedy:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding.format()}", file=sys.stderr)
        print(f"  gate: {ES.GATE_DOC} · override with --skip-extraction-scan",
              file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create/upsert a `briefs` collection point (brief authoring).")
    ap.add_argument("--brief-id", required=True,
                    help="{team}/{date}-{slug} (trainee: training/{date}-{curriculum-slug}-{trainee})")
    ap.add_argument("--team", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--assignee", required=True, help="agent name or trainee id")
    ap.add_argument("--assignee-type", default=brief_substrate.ASSIGNEE_AGENT,
                    choices=(brief_substrate.ASSIGNEE_AGENT, brief_substrate.ASSIGNEE_TRAINEE))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--body", help="brief body markdown inline")
    g.add_argument("--body-file", help="path to the committed brief file (its markdown is the body)")
    ap.add_argument("--work-item", action="append", default=[], dest="work_items",
                    help="canonical work-item ref PROJ-XXX/T-YYY (repeatable)")
    ap.add_argument("--summary", default=None)
    ap.add_argument("--origin-path", default=None,
                    help="committed brief file path (the human/PR record, git per D4)")
    ap.add_argument("--status", default=brief_substrate.STATUS_DRAFT,
                    choices=brief_substrate.BRIEF_STATUSES)
    ap.add_argument("--approve", action="store_true",
                    help="shorthand for --status approved (operator/lead sign-off, D5 gate)")
    ap.add_argument("--tooling-update", default=None, dest="tooling_update",
                    help="'<tag>' (e.g. v1.1.0) or 'latest' — audit record of a self-update "
                         "instruction (PROJ-039/T-056); the resident update-tooling.py is "
                         "actually triggered by the launch-time TOOLING_UPDATE env var, not "
                         "this field")
    ap.add_argument("--dry-run", action="store_true",
                    help="run validation and the B4 extraction gate, then stop without "
                         "upserting. A brief point is idempotent but not free to undo — "
                         "there is no draft state in the substrate to fall back to.")
    ap.add_argument("--skip-extraction-scan", action="store_true",
                    help="bypass the B4 extraction gate (PROJ-011/T-126). Use only with "
                         "a stated reason: substrate writes have no revocation remedy.")
    args = ap.parse_args(argv)

    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
        origin_path = args.origin_path or args.body_file
    else:
        body = args.body
        origin_path = args.origin_path
    if not body.strip():
        print("create-brief: brief body is empty", file=sys.stderr)
        return 2

    gate_rc = _gate_body(body, skip=args.skip_extraction_scan)
    if gate_rc:
        return gate_rc

    status = brief_substrate.STATUS_APPROVED if args.approve else args.status

    if args.dry_run:
        print(f"dry-run: brief {args.brief_id} would be upserted as "
              f"{brief_substrate.point_id_for(args.brief_id)} "
              f"(assignee={args.assignee}, status={status}); extraction gate passed")
        return 0

    result = brief_substrate.create_brief(
        brief_id=args.brief_id,
        team=args.team,
        author=args.author,
        assignee=args.assignee,
        assignee_type=args.assignee_type,
        body=body,
        status=status,
        work_items=args.work_items,
        summary=args.summary,
        origin_path=origin_path,
        tooling_update=args.tooling_update,
    )
    print(f"created brief point {result['point_id']} for {args.brief_id} "
          f"(assignee={args.assignee}, status={status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
