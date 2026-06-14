# PROJ-039 — unified-session-substrate hook wiring

How the three substrate hooks are wired into a home-team-agent instance, and the
coexistence + auth model. Authored during T-010 (Phase B, single-instance trial);
intended as the reference for the Phase-C fleet rollout.

## The three hooks

| Event | Hook | Writes | Discipline |
|---|---|---|---|
| `SessionStart` | `session-materialise.py` | `<cwd>/.workspace/` from the `session` point | Halts (no fabrication) on empty/unreachable — R-006 / SD-3-002 |
| `Stop` | `substrate-stop.sh` → `append-session-stop.py` | `session_stop[]` on the point | `set_payload` only — never a full upsert (SD-3-001) |
| `SessionEnd` | `session-end-finalise.py` | `response` + `rollup`, telemetry push, push-then-delete, session-finalise, brief-result PR | Order is load-bearing (§ 2.4); raw-delete gated on the telemetry push (C-006) |

Wire them from `hooks/settings-substrate-snippet.json`.

## Coexistence with the legacy CST chain (C-003)

Claude Code **merges** hooks across settings sources (user + project + local) — every
matching hook fires. So the substrate hooks **coexist** with the legacy CST chain
rather than replacing it:

- Apex `~/.claude/settings.json` keeps firing the pure-CST chain (`session-start.sh`,
  `stop.sh`, `ingest-transcript.sh`, pre/post-tool-use). **Unchanged.**
- A **project-level** `.claude/settings.json` in the home repo adds the three substrate
  hooks. Both chains fire; the new ones write `session_substrate`, the legacy ones write
  CST. Nothing is disabled during the transition.

`substrate-stop.sh` is the substrate-only Stop wrapper for exactly this split (the
in-repo `stop.sh` does both halves and is for instances that wire a single Stop hook).

## Auth — the env-block mechanism (PROJ-033/T-016)

The hooks read `PODZONE_QDRANT_APIKEY` from the process env. The reliable propagation
path is the **`env` block in `settings.json`** — Claude Code injects it into the session
environment and hook subprocesses inherit it. This is what made the write path reliable
(T-016); shell-export inheritance into VS Code-launched hooks is *not* dependable (C-004).
**Never embed the key in a project/snippet settings file** — it rides in from the apex
env block. (Hygiene note: storing it there is plaintext-at-rest; revisit at cutover.)

## Findings from the T-010 trial (decisions for Phase C)

1. **Session-id pre-allocation is required.** Post-start writes key the point by
   `uuid5(session_id)` using the runtime id. The Team Lead authors the brief point
   *before* the id exists, so the launch must pin it: create the point with a
   pre-generated UUID and launch with `claude --session-id <uuid>`. The VS Code sidebar
   extension auto-generates an id that won't match — for a sidebar-launched fleet,
   `session-materialise` would need a **rekey** step (resolve by agent+work_item, then
   re-home the point onto `uuid5(runtime_id)`). Pick one before fleet rollout.
2. **SessionEnd is unreliable under the VS Code extension (2026-05-19).** All of
   finalisation hangs off it. Mitigation in place: `session-end-finalise.py` is
   idempotent and re-runnable as a **manual finalise** (`echo '{"session_id":…,
   "transcript_path":…}' | python3 session-end-finalise.py`). A Stop-driven
   finalise-once fallback is the alternative if manual is too fragile for the fleet.
3. **Brief-result PR needs a GitHub remote.** On a local/scratch remote the doc commits
   and pushes but `gh pr create` degrades gracefully (logged reason). Fleet instances
   point at the real `home-team-agent` GitHub remote.
