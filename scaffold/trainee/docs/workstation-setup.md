# Workstation setup — trainer-assisted (take-on Phase A)

**Audience: the trainer assisting a trainee** through first-run configuration. Work
through this with the trainee at the keyboard; it takes ~15 minutes and only needs
doing once per workstation. The dependency rationale is in
[`dependency-analysis.md`](./dependency-analysis.md).

The goal: get `python3 .claude/hooks/trainee-preflight.py` to report **all OK**. Note
that sessions already work **offline** before any of this — the agent briefing
(`AGENTS.md` + `trainee-brief.md`) lives in the repo. This setup enables the online
extras: progress telemetry and the training team's operational-brief channel.

## 1. Command-line tools

```bash
# git + python3 (macOS: install the Xcode Command Line Tools if missing)
git --version || xcode-select --install
python3 --version

# GitHub CLI, authenticated (opens the session PR automatically)
brew install gh        # or your platform's package manager
gh auth login          # choose GitHub.com → HTTPS → login with a browser
gh auth status         # confirm: "Logged in to github.com"
```

If `gh` cannot be installed, the trainee can still work — the finalise commits and
pushes the session branch and prints the branch to open a PR from manually. `git` and
`python3` are **required**.

## 2. Complete `training-config.yaml`

The repo root carries a **committed** `training-config.yaml` — the single
configuration surface for this repo's hooks. Replace its `{{PLACEHOLDERS}}`:

| Key | Fill with |
|---|---|
| `qdrant_api_key` | The trainee's **granular Database API Key** — minted in the Qdrant Cloud console (Database API Keys), scoped **read-write to exactly** `training_briefs` + `training_session_telemetry`. Self-signed JWTs are rejected by the cloud tier — console-minted only |
| `trainee` | The trainee's handle (matches the repo name `podzone-training-<handle>`) |
| `operational_brief_id` | `training/<handle>/operational` (usually correct after filling the handle) |

Committing the filled file is **by design**: the key can only touch the two training
collections named beside it, and the repo is the trainee's own private repo. Run
`tools/personalise-trainee.py --handle <handle>` first (it renames `Trainee/` and
pre-fills the handle fields), then paste the key.

## 3. Repo access

```bash
# invite the training lead as a collaborator (PR visibility + review loop)
gh api -X PUT repos/<handle>/podzone-training-<handle>/collaborators/<training-lead>
```

The trainer also authors the trainee's **personalised training brief** content into
`trainee-brief.md` (curriculum, programme summary, start date) — it ships as a
skeleton with `{{PLACEHOLDERS}}`.

## 4. Verify

```bash
python3 .claude/hooks/trainee-preflight.py
```

Expect every line **OK**. Then start a session — the trainee just greets Alex
(`Hi Alex, this is <name>`, see `README.md`). If the preflight still shows MISSING, it
prints the exact next action for each item — resolve those and re-run.

## Troubleshooting

Hook behaviour and recovery are documented in
[`../memory/hooks-troubleshooting.md`](../memory/hooks-troubleshooting.md) (also loaded
into session context via `memory/`). The short version: an unconfigured repo fails
**soft** — you will see a single "not configured yet — see the setup guide" line, not a
wall of errors. That is expected before this guide is complete.
