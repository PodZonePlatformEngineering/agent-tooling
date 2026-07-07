# Trainee repo — first-run dependency analysis (R-13)

**Read this before the first training session.** A freshly generated trainee repo
(*Use this template* → your own `podzone-training-<handle>` clone) does **not** run out
of the box: the substrate hooks depend on credentials and CLIs that are **not** shipped
in the template (secrets never live in git). On an unconfigured workstation the hooks
**fail soft** — they emit one "not configured yet — see the setup guide" pointer and
never block orientation — but a training **brief will not materialise** until the
prerequisites below are in place.

This document surfaces the full dependency set so "some prep is required before a brief
will load" is explicit, not discovered by failure. The trainer-facing, step-by-step
version is [`workstation-setup.md`](./workstation-setup.md) — work through that with
your trainer.

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
| `git` | Session branch + commit (R-2/R-3 are hook-driven; you run no git) | `git --version` | Xcode CLT / package manager |
| `gh` (authenticated) | Opens the session PR to `main` (R-3). Without it the finalise still commits + pushes and tells you to open the PR by hand | `gh auth status` | `brew install gh` → `gh auth login` |
| `python3` | Every substrate hook and the finalise are Python | `python3 --version` | Xcode CLT / package manager |

### 2. Secrets

Secrets are **collected** interactively (you paste/enter them once) and **stored** in
the gitignored workstation env block — never committed. The store is
`.claude/settings.local.json` (its `env` block), which `.gitignore` already excludes;
alternatively the secrets MCP / your shell env may provide them at launch.

| Secret | Collected from | Stored in | Used by |
|---|---|---|---|
| `PODZONE_QDRANT_APIKEY` | Your trainer (the training Qdrant tenant key) | `.claude/settings.local.json` → `env` (gitignored), or secrets MCP | Every hook that reads/writes the substrate: session point, brief materialise, telemetry, finalise |

> If your cohort ships secrets via a parallel channel (a shared secrets vault or the
> secrets MCP), the **injection step** is: bind the key into the launch environment so
> the hooks see `PODZONE_QDRANT_APIKEY` — no plaintext copy in the repo.

Trainee repos are deliberately **slim** (R-14): they do **not** use
`PODZONE_TELEMETRY_REMOTE` (the session log is committed into `logs/` instead of pushed
to a fleet telemetry repo) or `PODZONETEAM_REPO` (no apex clone in trainee
context). So the single secret above is the whole secret surface.

### 3. Collaboration / access

| Dependency | Why |
|---|---|
| Training-lead invited as a collaborator on your `podzone-training-<handle>` repo | PR visibility + the session review loop (they review + merge your session PRs) |
| An **approved** training brief in the `briefs` collection | The first prompt's `Brief:` line resolves it; materialise **HALTs** if the brief is missing or not past the approval gate. Your trainer authors + approves it (see the launch ritual in `README.md`) |

## Order of operations (first run)

1. Install/authenticate the CLIs (§1) and set `PODZONE_QDRANT_APIKEY` (§2) — see
   `workstation-setup.md`.
2. Have your trainer invite themselves as a collaborator and author + **approve** your
   brief (§3).
3. Run `python3 .claude/hooks/trainee-preflight.py` — expect all **OK**.
4. Start a session and paste your `Brief:` line as the first prompt. The brief
   materialises; you are ready to work.

Until step 1–2 are done the hooks stay quiet (one pointer, no error wall) and no work
is lost — orientation, reading this repo, and reading the brief docs all work offline.
