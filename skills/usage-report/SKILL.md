---
description: Generate a usage summary from the sessions Qdrant collection for a recent time window. Writes a markdown report to the resolved team repo's team/{lead}/outgoing/usage-reports/ and prints a digest to stdout.
---

# /usage-report

Usage:

```
/usage-report [days]
```

- `days` (optional, default 7) — window in days, inclusive of today.

**Tool dependency (skip-if-absent, mirrors `/consolidate-tasks` Step 6b):** this skill
needs `agent-tooling/tools/usage-report.py` on disk. Resolve the path via the single
`.workspace/agent-tooling` on-demand-clone convention — never a hardcoded
`~/workspace/agent-tooling`. **Apex exception:** Hermes's primary clone is permanently at
`~/workspace/agent-tooling` (canonical source, not an on-demand checkout); prefer that
path if it exists, falling back to `.workspace/agent-tooling` otherwise. If neither path
has the tool, or Qdrant is unreachable, report the failure cleanly and stop — do not retry.

Behaviour:

1. Resolve the output directory via the `team_repo` decoder (same resolver used by
   `/consolidate-tasks` and `/launch-session`, PROJ-039/T-038): the report lands at
   `team/{lead}/outgoing/usage-reports/` in the **resolved team repo**, not a hardcoded
   `podzoneTeam`/`team/hermes` path. `{lead}` is the lowercased `agent:` value from the
   operator's identity YAML (e.g. `hermes`, `athena`, `kronos`).
   ```bash
   AT="${HOME}/workspace/agent-tooling"; [ -d "$AT" ] || AT=".workspace/agent-tooling"
   RESOLVED=$(python3 "${AT}/lib/team_repo.py" --home-repo "$HOME_REPO" --role-class "$ROLE_CLASS" --json)
   TEAM_REPO_PATH=$(echo "$RESOLVED" | python3 -c "import json,sys; print(json.load(sys.stdin)['local_path'])")
   OUT_DIR="${TEAM_REPO_PATH}/team/${LEAD_SLUG}/outgoing/usage-reports"
   ```
2. Run the tool against that output directory:
   ```bash
   python3 "${AT}/tools/usage-report.py" --days "${days:-7}" --out-dir "$OUT_DIR"
   ```
3. Capture the stdout digest verbatim in your response to the user.
4. The report file is at `{OUT_DIR}/{today}-usage-summary.md` — link it in the response.

Step 0 of the tool runs a zombie-cleanup pass against the cloud `sessions` collection (deletes pre-T-005 heartbeat-only points missing `data_source`). Use `--no-cleanup` only when you have a specific reason to skip it.

Phase 1: usage data only — no dollar values. See PROJ-034 spec for the data model: `planning/projects/PROJ-034-session-cost-observability/proposal.md` (apex `podzoneTeam`; a fissioned team lead without local access to that path may skip the citation and note the phase in the response instead).
