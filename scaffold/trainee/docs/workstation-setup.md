# Workstation setup — trainer-assisted (R-13)

**Audience: the trainer assisting a trainee** through first-run configuration. Work
through this with the trainee at the keyboard; it takes ~15 minutes and only needs
doing once per workstation. The dependency rationale is in
[`dependency-analysis.md`](./dependency-analysis.md).

The goal: get `python3 .claude/hooks/trainee-preflight.py` to report **all OK**, then
a `Brief:` first prompt materialises the trainee's brief.

## 1. Command-line tools

```bash
# git + python3 (macOS: install the Xcode Command Line Tools if missing)
git --version || xcode-select --install
python3 --version

# GitHub CLI, authenticated (opens the session PR in R-3)
brew install gh        # or your platform's package manager
gh auth login          # choose GitHub.com → HTTPS → login with a browser
gh auth status         # confirm: "Logged in to github.com"
```

If `gh` cannot be installed, the trainee can still work — the finalise commits and
pushes the session branch and prints the PR URL to open manually. `git` and `python3`
are **required**.

## 2. The one secret — `PODZONE_QDRANT_APIKEY`

The trainer supplies the training Qdrant tenant key. Store it in the **gitignored**
workstation env block (never committed):

```bash
# from the repo root
mkdir -p .claude
cat > .claude/settings.local.json <<'JSON'
{
  "env": {
    "PODZONE_QDRANT_APIKEY": "PASTE-THE-KEY-HERE"
  }
}
JSON
```

`.gitignore` already excludes `.claude/settings.local.json`. Confirm it is not staged:

```bash
git status --porcelain .claude/settings.local.json   # expect: no output
```

> **Alternative (secrets MCP / shell env):** if your cohort injects secrets through the
> secrets MCP or the launch shell, bind `PODZONE_QDRANT_APIKEY` there instead and skip
> the file. The hooks only need the variable present in the launch environment.

## 3. Repo access + an approved brief

```bash
# invite the training lead as a collaborator (PR visibility + review loop)
gh api -X PUT repos/<handle>/podzone-training-<handle>/collaborators/<training-lead>
```

The trainer then authors and **approves** the trainee's brief as a `briefs` point (see
*Trainee launch ritual* in `README.md`). Without `--approve` the brief sits behind the
execution gate and materialise HALTs — nothing is broken, the brief just will not load
until it is approved.

## 4. Verify

```bash
python3 .claude/hooks/trainee-preflight.py
```

Expect every line **OK**. Then start a session and paste the `Brief:` first prompt from
`README.md`. If the preflight still shows MISSING, it prints the exact next action for
each item — resolve those and re-run.

## Troubleshooting

Hook behaviour and recovery are documented in
[`../memory/hooks-troubleshooting.md`](../memory/hooks-troubleshooting.md) (also loaded
into session context via `memory/`). The short version: an unconfigured repo fails
**soft** — you will see a single "not configured yet — see the setup guide" line, not a
wall of errors. That is expected before this guide is complete.
