---
name: session-end
description: Manually finalise this session — the /exit equivalent for environments without one (IDE sidebar sessions)
---

Thin wrapper over the resident SessionEnd finalise hook (PROJ-039/T-100). Some
session surfaces (the IDE sidebar) have no `/exit` built-in, so the harness
SessionEnd fires only at window close — this skill triggers the **same finalise,
deliberately, now**. It duplicates NO finalise logic: everything (response +
rollup upsert, result authoring, lifecycle-mode-aware landing [trunk: direct
commit on main; branch: result PR], telemetry push, end-guard, lock release) is
`.claude/hooks/session-end-finalise.py`, invoked exactly as the harness invokes it.

**This ends the session's guarded lifecycle.** After it runs, further work in this
conversation trips the F14 resumed-after-finalise HALT and needs the re-arm ritual.
Confirm with the operator before firing unless they invoked this skill themselves.

## Step 1 — Resolve the live sid and transcript

1. `sid = $PODZONE_SESSION_ID` if set (the hooks export it at SessionStart).
2. Otherwise derive from the transcript directory for this project:
   `~/.claude/projects/{cwd with / → -}/` — take the **most recently modified**
   `*.jsonl` (F27: mtime is authoritative; never trust a picker-echoed id) and use
   its basename as the sid.
3. `transcript_path = ~/.claude/projects/{encoded cwd}/{sid}.jsonl` — verify it
   exists and its mtime is recent (this conversation is still appending to it).
   Mismatch or ambiguity → STOP and show the candidates; never finalise a guessed sid.

## Step 2 — Fire the finalise

From the home-repo root (`$CLAUDE_PROJECT_DIR` / the launch cwd):

```bash
printf '{"session_id":"%s","transcript_path":"%s","cwd":"%s"}' \
  "$SID" "$TRANSCRIPT" "$PWD" \
  | python3 .claude/hooks/session-end-finalise.py
```

The hook resolves the authoritative home repo itself (T-054) — pass the honest
cwd, never a `.workspace/*` path.

## Step 3 — Verify and report

- Ledger: `logs/finalise-state.log` entry for the sid reads `complete: true`.
- Landing per `lifecycle_mode` (`lib/lifecycle_mode.py`): **trunk** → result commit
  visible on `origin/main`, clean tree; **branch** → result PR raised on the home
  repo. (Brief-less team-lead sessions: until T-105 lands, result authoring may
  skip with `session point not found` — F25; report it, don't mask it.)
- Locks: no `~/.claude/session-locks/{repo}.lock` held by this sid.

Report the outcome to the operator in one short block: sid, result location (commit
or PR), telemetry push status, anything skipped.

## Double-fire is handled

The harness SessionEnd still fires at window close. The finalise's T-100 guard
no-ops when the sid is complete AND the transcript hasn't grown since; anything
new re-runs the idempotent finalise and banks it. Either way, no repair needed.
