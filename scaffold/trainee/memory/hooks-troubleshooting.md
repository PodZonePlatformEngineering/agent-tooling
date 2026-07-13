---
name: hooks-troubleshooting
description: What the trainee hooks do in a session and how to recover when one misbehaves
metadata:
  type: reference
---

# Hooks — troubleshooting summary (trainee)

A short, self-contained reference for when a session behaves unexpectedly. This is the
**troubleshooting** view only — enough to diagnose and recover. The full hook
documentation lives in the canonical `agent-tooling` source; you never edit hooks here.

Every hook is routed by the committed `training-config.yaml` (the repo root) and every
hook fails **soft**: offline, unconfigured, or Qdrant-down sessions still work from
`AGENTS.md` + `trainee-brief.md` alone.

## What runs, when

| When | Hook | What it does |
|---|---|---|
| Session start | `trainee-preflight.py` | Reports missing setup (once, quietly) — never blocks |
| Session start | `trainee-finalise.py --guard` | Recovers a previous session's truncated close (commits + re-raises its PR) |
| Session start | `trainee-session-branch.py` | Checks you out onto `session/{date}-{sid8}` off a clean `main` — **you run no git** |
| Session start | `update-tooling.py` | Applies a pending tooling update, if the team staged one |
| Session start | `trainee-materialise.py` | Surfaces the training team's **operational brief** from `training_briefs`, if one is pending |
| Session start / each prompt / compaction / turn end | `trainee-telemetry.py` | Writes one progress point to `training_session_telemetry` — the only place telemetry goes |
| During work | `trainee-read-guard.py` | Reports out-of-repo reads (context containment) |
| Session end | `trainee-finalise.py` | Copies the session log into `logs/`, writes the close telemetry point, commits your working tree to the session branch, pushes, and opens a PR to `main` |

## Common situations

**"not configured yet — see the setup guide" on start.** Expected on an unconfigured
workstation. The hooks are failing **soft** — nothing is broken. Work through
`docs/workstation-setup.md`, then run `python3 .claude/hooks/trainee-preflight.py`.

**"Operational brief channel unavailable (offline…)" on start.** Also normal — the
session proceeds from the in-repo briefing. Telemetry and team updates resume when the
workstation is back online.

**HALT at start about a "dirty / leftover" clone.** A previous session did not finish
cleanly and the recovery guard could not fix it alone. Commit or discard any stray
changes and get back onto a clean `main` (`git status` to see what is uncommitted).
The branch hook then re-checks you out.

**A warning about reading a file outside the repo.** Context containment: session
context comes only from **this** repo. If you need an external document, copy it into
`{Trainee}/sourceDocs/` first, then read it from there.

**No PR appeared at session end.** If `gh` is not installed/authenticated the finalise
still commits and pushes your session branch — open the PR manually, or install `gh`
(see the setup guide) so it is automatic next time. The next session's `--guard` also
retries a close that was cut off.

**An operational brief appeared at start.** The training team staged a repo/tooling
update. Alex explains it, applies it only with your approval, then acknowledges it
back to the team (`trainee-materialise.py --ack <revision>`).

**Where to look.** Committed session logs are in `logs/` (`session-{sid8}.jsonl`,
`libraries-{sid8}.log`) — they ride your session PR, so your trainer can see exactly
what the runtime did.
