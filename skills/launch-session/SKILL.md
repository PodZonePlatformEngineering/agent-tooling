---
name: launch-session
description: Launch an isolated concurrent agent session using git worktree + VS Code CLI (Team Lead only)
---

This skill is for **the Team Lead** of each team. It creates an isolated working environment
for an agent session, opens it in a new VS Code window, and registers the session so
the Team Lead can track it during consolidation.

The engineer then switches to the new window, runs the agent session, and returns to the
planning window when convenient. Both sessions run concurrently without sharing repo state.

## Prerequisites

- VS Code CLI available at:
  `/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code`
- All required repos already cloned under `~/workspace/`
- `git worktree` supported (standard Git ≥ 2.5)
- podzoneAgentTeam main branch must be clean before launching

## Inputs (ask Martin if not provided)

- **agent** — e.g. `hephaestus`, `atlas`, `thoth`
- **task-slug** — short identifier for the session work, e.g. `dev-functional-testing`
- **repos** — which repos the session needs (default: from the agent's identity file)

## Step 1 — Resolve agent identity and repos

Read the agent's identity YAML. Search in this order:
1. `podzoneAgentTeam/workspaces/identity/{agent}.identity.yaml`
2. `podzoneAgentTeam/workspaces/identity/martin-{agent}-*.identity.yaml`
3. `{fissioned_team}/workspaces/identity/{agent}.identity.yaml` (if a fissioned team repo
   is present in the workspace and the agent belongs to it)

Extract:
- `home_repo` — determines standard vs. fissioned team mode (see Step 2)
- `repos` list — all repos in scope for this agent
- `workspace` — canonical workspace filename
- canonical workspace file: `{home_repo_path}/workspaces/{workspace}.code-workspace`

Filter `repos` to those actually needed for this session's task (ask Martin if unclear;
default to all repos in the identity file).

**Mode determination:**
- If `home_repo == podzoneAgentTeam` → **standard mode**
- If `home_repo != podzoneAgentTeam` → **fissioned team mode**

## Step 2 — Pre-flight brief check

Before creating any worktrees, confirm a commission brief for this session exists on
`origin/main` of the home repo. This prevents agents from starting without proper context
(e.g. when the brief is on an unmerged Team Lead branch).

**Home repo for the check:**
- Standard mode: `podzoneAgentTeam`
- Fissioned mode: the agent's `home_repo`

**Check procedure:**

1. List the incoming briefs on `origin/main`:
   ```bash
   git -C ~/workspace/{home_repo} show origin/main:team/{agent}/incoming/
   ```
2. Scan the listing for a filename matching `*{task-slug}*` (date prefix may vary).
3. **If a matching file exists:** push `origin/main` so the worktree branches from the
   current state, then proceed to Step 3:
   ```bash
   git -C ~/workspace/{home_repo} push origin main
   ```
   Push ensures origin/main is current before the worktree branches, preventing PR diff
   noise from unpushed Team Lead commits.
4. **If no match is found:** abort with:
   ```
   Commission brief not in main — merge the Team Lead session branch first.
   Expected: team/{agent}/incoming/{date}-{task-slug}.md
   Check: git show origin/main:team/{agent}/incoming/
   ```
   Do not create worktrees, workspace files, or session registry entries.

## Step 3 — Create session directory and worktrees

Session ID format: `{agent}-{YYYY-MM-DD}-{task-slug}`
Session directory: `~/sessions/{session-id}/`

```bash
mkdir -p ~/sessions/{session-id}
```

### Standard mode (home_repo == podzoneAgentTeam)

#### Task repo worktrees

For each required task repo (excluding podzoneAgentTeam itself):

```bash
git -C ~/workspace/{repo-path} worktree add \
  ~/sessions/{session-id}/{repo-name} \
  -b {agent}/{YYYY-MM-DD}-{task-slug}
```

Branch naming: `{agent}/{YYYY-MM-DD}-{task-slug}` — session-scoped, collision-free.
If a branch with that name already exists (retry same task): append `-2`, `-3`, etc.

#### podzoneAgentTeam PAT worktree

Always create a podzoneAgentTeam worktree — agents write their outbox and memory files
here on a session branch, then raise a PR to main at session end.

Branch naming: `session/{agent}-{YYYY-MM-DD}-{task-slug}`

```bash
git -C ~/workspace/podzoneAgentTeam worktree add \
  ~/sessions/{session-id}/podzoneAgentTeam \
  -b session/{agent}-{YYYY-MM-DD}-{task-slug}
```

### Fissioned team mode (home_repo != podzoneAgentTeam)

Fissioned agents do **not** get a podzoneAgentTeam session branch. Their PAT branch
lives in their own home repo.

#### Fissioned task repo worktrees

For each repo in the identity `repos` list **except** podzoneAgentTeam:

```bash
git -C ~/workspace/{repo-name} worktree add \
  ~/sessions/{session-id}/{repo-name} \
  -b {agent}/{YYYY-MM-DD}-{task-slug}
```

For the `home_repo` (e.g. `trainingTeam`), create the PAT branch instead:

```bash
git -C ~/workspace/{home_repo} worktree add \
  ~/sessions/{session-id}/{home_repo} \
  -b session/{agent}-{YYYY-MM-DD}-{task-slug}
```

#### podzoneAgentTeam — plain clone reference (no worktree)

If podzoneAgentTeam is listed in the agent's repos (for read-only skills/ops doc access),
reference it as a plain folder — do not create a worktree or session branch:

```
podzoneAgentTeam path: ~/workspace/podzoneAgentTeam  (plain clone, read-only)
```

**Note — git remote config is not worktree-isolated:** `git remote set-url` modifies
`.git/config` which is shared across all worktrees of the same repo. URL updates persist
to the main clone — this is intentional. Only file-level changes are branch-isolated.

## Step 4 — Generate session workspace file

**Standard mode:** Copy from `podzoneAgentTeam/workspaces/{workspace}.code-workspace`.

**Fissioned team mode:** Copy from `{home_repo_path}/workspaces/{workspace}.code-workspace`
(e.g. `~/workspace/trainingTeam/workspaces/athena.code-workspace`).

Write to: `~/sessions/{session-id}/session.code-workspace`

Rewrite all `path` entries to point to session-local worktree paths:
- `gitopsapi` → `~/sessions/{session-id}/gitopsapi`
- `podzoneAgentTeam` → `~/sessions/{session-id}/podzoneAgentTeam` (standard mode)
- `podzoneAgentTeam` → `~/workspace/podzoneAgentTeam` (fissioned mode — plain clone path)
- `trainingTeam` → `~/sessions/{session-id}/trainingTeam`
- etc.

Rewrite `identity_file:` in `claude.projectInstructions` to resolve against the
session-local worktree path of the home repo:
- Standard: `podzoneAgentTeam/workspaces/identity/{agent}.identity.yaml`
  → `~/sessions/{session-id}/podzoneAgentTeam/workspaces/identity/{agent}.identity.yaml`
- Fissioned: `{home_repo}/workspaces/identity/{agent}.identity.yaml`
  → `~/sessions/{session-id}/{home_repo}/workspaces/identity/{agent}.identity.yaml`

## Step 5 — Register the session

### Standard mode

Append a row to the **main clone** of `podzoneAgentTeam/planning/sessions/active.md`
(not the session worktree — this file is Team Lead-managed on main):

```markdown
| {session-id} | {agent} | {task-slug} | {YYYY-MM-DD} | in-flight | ~/sessions/{session-id} | session/{agent}-{YYYY-MM-DD}-{task-slug} |
```

### Fissioned team mode

1. Append a full row to `~/workspace/{home_repo}/planning/sessions/active.md`
   (the fissioned team's own session registry):

   ```markdown
   | {session-id} | {agent} | {task-slug} | {YYYY-MM-DD} | in-flight | ~/sessions/{session-id} | session/{agent}-{YYYY-MM-DD}-{task-slug} |
   ```

2. Also append a one-line summary to `~/workspace/podzoneAgentTeam/planning/sessions/active.md`
   for apex visibility (status column = `fissioned — see {home_repo}`):

   ```markdown
   | {session-id} | {agent} | {task-slug} | {YYYY-MM-DD} | fissioned — see {home_repo} | ~/sessions/{session-id} | — |
   ```

## Step 6 — Launch VS Code window

```bash
"/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" \
  --new-window ~/sessions/{session-id}/session.code-workspace
```

## Output

Standard mode:
```
Session launched: atlas-2026-04-06-shared-artifact-store

  Mode:             standard (home_repo: podzoneAgentTeam)
  Workspace:        ~/sessions/atlas-2026-04-06-shared-artifact-store/session.code-workspace
  Task branch:      atlas/2026-04-06-shared-artifact-store
  PAT branch:       session/atlas-2026-04-06-shared-artifact-store  (podzoneAgentTeam)
  Worktrees:        agentsonly-infra, agentsonly-apps, podzoneAgentTeam
  Registered:       planning/sessions/active.md (podzoneAgentTeam)

Switch to the new VS Code window to run the agent session.
At session end, the agent commits team/atlas/ changes, pushes the PAT branch, and raises
a PR to podzoneAgentTeam main. Hermes reviews and merges during /consolidate-tasks.
```

Fissioned team mode:
```
Session launched: athena-2026-05-01-curriculum-content

  Mode:             fissioned (home_repo: trainingTeam)
  Workspace:        ~/sessions/athena-2026-05-01-curriculum-content/session.code-workspace
  Task branch:      athena/2026-05-01-curriculum-content  (prompt-engineering-training)
  PAT branch:       session/athena-2026-05-01-curriculum-content  (trainingTeam)
  Worktrees:        prompt-engineering-training, trainingTeam
  podzoneAgentTeam: ~/workspace/podzoneAgentTeam  (plain clone, read-only)
  Registered:       trainingTeam/planning/sessions/active.md
                    podzoneAgentTeam/planning/sessions/active.md (apex summary line)

Switch to the new VS Code window to run the agent session.
At session end, the agent commits team/athena/ changes, pushes the PAT branch, and raises
a PR to trainingTeam main. trainingTeam Team Lead reviews during /consolidate-tasks.
```

## Cleanup (done by consolidate-tasks, not here)

After all PRs (task repos + podzoneAgentTeam) are merged:

```bash
git -C ~/workspace/{repo} worktree remove ~/sessions/{session-id}/{repo-name}
git -C ~/workspace/podzoneAgentTeam worktree remove ~/sessions/{session-id}/podzoneAgentTeam
rmdir ~/sessions/{session-id}   # only if empty
```

Mark the session `cleaned-up` in `planning/sessions/active.md`.