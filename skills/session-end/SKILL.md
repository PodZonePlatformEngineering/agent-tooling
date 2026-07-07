---
name: session-end
description: Summarise the session and update the programme status digest
---

Two variant bodies live in this single skill: **build** and **lead**. Resolve
identity, select the variant, then execute **only that variant's section**.
Stop after the memory prompt — do not fall through.

## Identity

Use the identity already resolved at session-start (operator:agent:scope from the
briefing header). If session-start was not run this session (e.g. context resumed
from compaction), fall back to the workspace's `claude.projectInstructions`
`identity_file:` line — do not run the full 4-step chain.

## Variant Selection

Select by `task_filter` from the identity file:

| task_filter | Variant |
|---|---|
| `Team Lead`, `Hermes` | **lead** (no branch / no PR — Hermes commits to main at `/consolidate-tasks`) |
| anything else, or fallback | **build** (branch + PR flow — default for Hephaestus, Atlas, Alex, Thoth, etc.) |

---

## Variant: build

Target: ≤ 705 K tokens, ≤ 8 tool calls. Default for Hephaestus, Atlas, Alex,
Thoth, and all non-lead agents.

### 1. Compose outbox

Write `team/{agent}/outgoing/session-YYYY-MM-DD-status.md` using the schema +
example in [template-outbox.md](template-outbox.md). Use today's date; if a
file for today already exists, append a `-2` suffix.

Cover: Completed, Started / In Progress, Blockers, Decisions, Questions for
Martin, Cross-team handoff (Drafts raised / Tasklist edits proposed / Tasklist
edits made this session), PRs Raised, Recommended Focus Next Session.

Use semantic names throughout (`programme:project:task-slug {emoji}`); never
raw IDs. See `agenticflows/operations/task-naming.md`.

### 2. Branch + PR — home-repo routing

Read `home_repo` from the identity YAML.

**Coexistence selector (PROJ-039/T-011 C2, T-005).** If `home_repo` matches
`home-*` (e.g. `home-podzone-hephaestus`), this agent has **migrated** — use the
**§ 2-migrated** flow below. Otherwise (`home_repo` ∈ {`podzoneTeam`,
`trainingTeam`, `roadmapTeam`}) use the legacy **§ 2-legacy** flow. Non-migrated
agents are unaffected by C2.

#### § 2-migrated — home-repo layout (`home-*`)

Session branch lives in the home-repo worktree at `~/sessions/{session-id}/{home_repo}`.
Results land in `results/`, not `team/{agent}/outgoing/`.

```bash
cd ~/sessions/{session-id}/{home_repo}   # the home-repo worktree

# Write the session result (v2.0 layout) — see .claude/output-format.md
#   results/session-{YYYY-MM-DD}-{task-slug}.md

git add results/ memory/
git commit -m "chore: session-close {operator}:{agent}:{scope} {YYYY-MM-DD}"

if git push origin session/{agent}-{YYYY-MM-DD}-{task-slug}; then
  gh pr create \
    --title "session: {agent} {YYYY-MM-DD} {task-slug}" \
    --body "Session result + memory updates. Ref: {task CC numbers}." \
    --base main \
    --repo PodZonePlatformEngineering/{home_repo}
else
  # T-005: record the push failure to Qdrant `sessions` so the team lead sees it.
  python3 ~/workspace/agent-tooling/tools/upsert-current-session.py \
    --session-id "$CLAUDE_SESSION_ID" --cwd "$PWD" \
    --data-source session_end_skill --status ended --push-failed || true
  # Surface the failing files for Martin and DO NOT mark close-out complete.
fi
```

Stage **only** `results/` and `memory/`. Never stage `.workspace/`, `context/`,
`.claude/settings.local.json`, or any other path. Then continue at § 3 (sanity check).

#### § 2-legacy — team-repo layout (not yet migrated)

General rule: `home_repo` value → `PodZonePlatformEngineering/{home_repo}`.

| `home_repo` | PR target |
|---|---|
| `podzoneTeam` | `PodZonePlatformEngineering/podzoneTeam` |
| `trainingTeam` | `PodZonePlatformEngineering/trainingTeam` |
| `roadmapTeam` | `PodZonePlatformEngineering/roadmapTeam` |

```bash
cd ~/sessions/{session-id}/{home_repo}   # the worktree, NOT the main clone

git add team/{agent}/
git commit -m "chore: session-close {operator}:{agent}:{scope} {YYYY-MM-DD}"
git push origin session/{agent}-{YYYY-MM-DD}-{task-slug}

gh pr create \
  --title "session: {agent} {YYYY-MM-DD} {task-slug}" \
  --body "Session outbox + memory updates. Ref: {task CC numbers}." \
  --base main \
  --repo PodZonePlatformEngineering/{home_repo}
```

Add the PR reference to `## PRs Raised` in the outbox:

```
{home_repo}#{number} — session-close {agent} {date}
```

**Rules (enforced by the home-repo Team Lead during structural review):**

- Stage **only** `team/{agent}/`. Never stage `planning/`, `specifications/`,
  `.claude/`, or any other agent's `team/` directory.
- `planning/sessions/active.md` is Hermes-managed — do not write, delete, or
  modify it. Hermes handles session registration/cleanup at `/consolidate-tasks`.
- Never write into another agent's `team/{other-agent}/`. Route cross-agent
  findings via your own outbox addressed to the recipient.
- One PR per session — do not combine multiple sessions on one branch.
- Cross-team handoff from a fissioned agent: draft goes to
  `team/{recipient}/incoming/drafts/` in your own fissioned repo. If the
  recipient is on a different team (apex or sibling), write the draft into the
  plain podzoneTeam clone at
  `~/workspace/podzoneTeam/team/{recipient}/incoming/drafts/{date}-{slug}.md`.
  Hermes picks these up via the fissioned-team draft scan during
  `/consolidate-tasks`.

If `git push` fails (e.g. uncommitted/local-only state), surface the failing
files for Martin and do not mark close-out complete.

### 3. Post-push sanity check

```bash
git status --short      # must be empty
git log @{u}..          # must be empty
```

Delete merged local branches with `git branch -d {branch}` (safe — refuses if
unmerged). Do not force-delete.

### 4. Worktree signal (isolated sessions only)

If running in a worktree (detect with `git worktree list`), add to the
outbox `## PRs Raised`:

```
worktree-cleanup: ~/sessions/{session-id}
```

Hermes removes the worktree at the next `/consolidate-tasks` pass.

### 5. Upsert session usage to Qdrant

```bash
python3 ~/workspace/agent-tooling/tools/upsert-current-session.py \
  --session-id "$CLAUDE_SESSION_ID" --cwd "$PWD" \
  --data-source session_end_skill --status ended || true
```

Schema: `agent-tooling/docs/sessions-schema.md`. Best-effort — if
`agent-tooling` isn't on disk, `$CLAUDE_SESSION_ID` isn't set, or Qdrant
isn't reachable, the script logs to stderr and exits 0.

### 6. Telegram

Telegram notification is sent automatically by the `SessionEnd` hook
(`.claude/hooks/ingest-transcript.py` → `telegram-notify.py`). **Do not call
`telegram-notify.py` from this skill body.** If `TELEGRAM_CHAT_ID` is unset,
the hook logs a warning and skips silently.

### 7. Memory prompt

Ask: "Any decisions or context worth committing to `team/{agent}/memory/`?"
If yes, write the memory file and update `team/{agent}/memory/MEMORY.md` index.

**Stop here.** Do not execute the lead variant.

---

## Variant: lead

Target: ≤ 1.0 M tokens. For Hermes and fissioned Team Leads. **No branch / no
PR** — Hermes commits to main during `/consolidate-tasks`; recording the same
content as a feature-branch PR is duplicate work.

### 1. Compose outbox

Write `team/{agent}/outgoing/session-YYYY-MM-DD-status.md` using the schema +
example in [template-outbox.md](template-outbox.md). Same content as the build
variant. Note in the outbox that the session's substantive commits land at the
next `/consolidate-tasks`.

### 2. Upsert session usage to Qdrant

```bash
python3 ~/workspace/agent-tooling/tools/upsert-current-session.py \
  --session-id "$CLAUDE_SESSION_ID" --cwd "$PWD" \
  --data-source session_end_skill --status ended || true
```

### 3. Telegram

Same as build variant — the `SessionEnd` hook handles it. Do not call
`telegram-notify.py` from this skill body.

### 4. Memory prompt

Ask: "Any decisions or context worth committing to `team/{agent}/memory/`?"
If yes, write the memory file and update `team/{agent}/memory/MEMORY.md` index.