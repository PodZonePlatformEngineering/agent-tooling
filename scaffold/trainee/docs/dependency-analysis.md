# Trainee repo — first-run dependency analysis

**Read this before the first training session.** A freshly generated trainee repo
(*Use this template* → your own `podzone-training-<handle>` clone) works **offline
out of the box** — the agent briefing (`AGENTS.md` + `trainee-brief.md`) is in the
repo and no hook needs to succeed for a session to run. What does **not** work until
the workstation is configured: progress telemetry, the training team's
operational-brief channel, and the automatic session PR. On an unconfigured
workstation the hooks **fail soft** — one "not configured yet — see the setup guide"
pointer, never a blocked session.

This document surfaces the full dependency set so "some prep is required" is explicit,
not discovered by failure. The trainer-facing, step-by-step version is
[`workstation-setup.md`](./workstation-setup.md) — work through that with your trainer.

## Preflight

Run the bundled preflight at any time to see exactly what is still missing:

```bash
python3 .claude/hooks/trainee-preflight.py
```

It reports each dependency below as **OK** / **MISSING** and prints the single next
action. It never changes anything and never fails the shell.

## Dependency set

### 1. Workstation CLIs

| Dependency | Why | Check | Where to get it |
|---|---|---|---|
| `git` | Session branch + commit are hook-driven; you run no git | `git --version` | Xcode CLT / package manager |
| `gh` (authenticated) | Opens the session PR to `main`. Without it the finalise still commits + pushes and tells you to open the PR by hand | `gh auth status` | `brew install gh` → `gh auth login` |
| `python3` | Every hook is Python | `python3 --version` | Xcode CLT / package manager |

### 2. The credential — in `training-config.yaml`

The single credential is the trainee's **granular Database API Key**, and it lives in
the **committed** `training-config.yaml` at the repo root — not in an env var, not in
a gitignored file. That is safe by scope: the key is console-minted read-write to
exactly the two training collections named beside it (`training_briefs`,
`training_session_telemetry`) and can touch nothing else.

| Key | Collected from | Used by |
|---|---|---|
| `qdrant_api_key` | Your trainer (minted in the Qdrant Cloud console at take-on) | The operational-brief materialise, telemetry writes, and the session-close telemetry point |

Trainee repos are deliberately **slim**: no `PODZONE_QDRANT_APIKEY` fleet key, no
`PODZONE_TELEMETRY_REMOTE` (the session log is committed into `logs/` instead of
pushed to a fleet telemetry repo), no `PODZONETEAM_REPO` (no apex clone in trainee
context). Telemetry leaves the machine only as `training_session_telemetry` points.

### 3. Collaboration / access

| Dependency | Why |
|---|---|
| Training-lead invited as a collaborator on your `podzone-training-<handle>` repo | PR visibility + the session review loop (they review your session PRs; **you** merge them) |
| `trainee-brief.md` personalised | The trainer fills the skeleton's `{{PLACEHOLDERS}}` (curriculum, programme, dates) at take-on — this file is the offline-first task source |

## Order of operations (first run)

1. Install/authenticate the CLIs (§1) — see `workstation-setup.md`.
2. Run `tools/personalise-trainee.py --handle <handle>`, then fill the Database API
   Key into `training-config.yaml` (§2) with your trainer.
3. Have your trainer complete `trainee-brief.md` and invite themselves as a
   collaborator (§3).
4. Run `python3 .claude/hooks/trainee-preflight.py` — expect all **OK**.
5. Start a session and greet Alex (`Hi Alex, this is <name>`). You are ready to work.

Until steps 1–3 are done the hooks stay quiet (one pointer, no error wall) and no work
is lost — orientation, reading this repo, and full offline training sessions all work.
