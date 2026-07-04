---
name: hooks-troubleshooting
description: What the substrate hooks do in a trainee session and how to recover when one misbehaves
metadata:
  type: reference
---

# Hooks — troubleshooting summary (trainee)

A short, self-contained reference for when a session behaves unexpectedly. This is the
**troubleshooting** view only — enough to diagnose and recover. The full hook
documentation lives in the canonical `agent-tooling` source; you never edit hooks here.

## What runs, when

| When | Hook | What it does |
|---|---|---|
| Session start | `session-start.sh` | Records session telemetry; runs the unfinalised-session guard |
| Session start | `trainee-session-branch.py` | Checks you out onto `session/{date}-{sid8}` off a clean `main` — **you run no git** |
| First prompt | `first-prompt-brief.py` | Reads the `Brief:` line and materialises `.workspace/` from the `briefs` collection |
| During work | `pre-tool-use.sh` / `post-tool-use.sh` | Records tool telemetry; reports out-of-repo reads (context containment) |
| Session end | `session-end-finalise.py` | Writes the session summary, copies the session log into `logs/`, commits your working tree to the session branch, pushes, and opens a PR to `main` |

## Common situations

**"not configured yet — see the setup guide" on start.** Expected on an unconfigured
workstation. The hooks are failing **soft** — nothing is broken. Work through
`docs/workstation-setup.md`, then run `python3 .claude/hooks/trainee-preflight.py`.

**Session HALTs at the first prompt ("brief missing / not approved").** Your brief is
not an approved `briefs` point yet. Ask your trainer to author + `--approve` it. Do not
begin work on a HALT.

**HALT at start about a "dirty / leftover" clone.** A previous session did not finish
cleanly. Commit or discard any stray changes and get back onto a clean `main`
(`git status` to see what is uncommitted). The branch hook then re-checks you out.

**A warning about reading a file outside the repo.** Context containment (R-9): session
context comes only from **this** repo (plus the `.workspace/` task repos the brief
materialises). If you need an external document, copy it into `{Trainee}/sourceDocs/`
first, then read it from there.

**No PR appeared at session end.** If `gh` is not installed/authenticated the finalise
still commits and pushes your session branch and prints the branch — open the PR
manually, or install `gh` (see the setup guide) so it is automatic next time.

**Where to look.** Committed session logs are in `logs/` (`libraries-{sid8}.log`,
`primitives-{sid8}.log`) — they ride your session PR, so your trainer can see exactly
what the runtime did.
