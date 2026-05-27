# agent-tooling

Shared primitives, hooks, skills, and scaffolding for building **Claude Code agents**
that observe themselves and collaborate via a shared semantic memory.

Each agent gets its own home repo (`{team}-{agent}` pattern). This repo gives them:

- **Primitives** — small bash scripts wrapping Qdrant, Ollama, Telegram, and Gmail
  so hooks and skills don't reimplement the same HTTP calls.
- **Hooks** — Claude Code lifecycle hooks (`SessionStart`, `Stop`, `PostToolUse`,
  …) that record session activity into Qdrant for later querying.
- **Skills** — shared slash commands (`/session-start`, `/session-end`,
  `/launch-session`, `/consolidate-tasks`).
- **Tools** — operator-facing CLIs (`session-report`, `usage-report`,
  `decay-detector`, …) that read the recorded data.
- **Scaffold** — `scaffold.sh` stamps out a new agent home repo wired to this
  repo's hooks.

Licence: **MIT** (see [LICENSE](LICENSE)).

---

## Prerequisites

| Component | Used for | Notes |
|---|---|---|
| `bash` ≥ 4 | primitives, hooks | macOS ships 3.2 — install via Homebrew if scripts fail |
| `python3` ≥ 3.10 | hooks, tools | 3.9 still works but Google libs warn |
| `curl`, `jq` | primitives | `jq` only used by a few tools |
| **Qdrant** | semantic memory | self-hosted or cloud; collections listed below |
| **Ollama** | local embeddings | `nomic-embed-text` is the default model |
| Telegram bot token | optional — notifications | only if you enable `telegram-notify` |
| Gmail OAuth credentials | optional — drafts | only if you use `create-gmail-draft.sh` |

The repo assumes Qdrant + Ollama are reachable; everything else is optional.

---

## Quick smoke test

Goal: clone, install, and successfully embed a string + write a point to your
own Qdrant inside 10 minutes.

```bash
git clone https://github.com/PodZonePlatformEngineering/agent-tooling.git
cd agent-tooling
./install.sh                                # installs primitives + tools to ~/.local/bin

# Point the primitives at your services
export AGENTSONLY_QDRANT_URL="https://<your-qdrant-host>:6333"
export PODZONE_QDRANT_APIKEY="<your-qdrant-api-key>"
export OLLAMA_HOST="http://localhost:11434"

# 1. Embed a string
embed-text "hello world" | head -c 80

# 2. Add a point to a collection you own
VEC=$(embed-text "hello world")
add-qdrant-point my-collection my-point-1 "$VEC" '{"name":"hello"}'

# 3. Search the collection
search-qdrant my-collection "hello" 3
```

If all three calls return JSON without erroring, the install is good.

---

## Repository layout

```
agent-tooling/
├── primitives/       bash wrappers around Qdrant / Ollama / Telegram / Gmail HTTP APIs
├── hooks/            Claude Code lifecycle hooks (deployed into agent home repos)
├── skills/           Shared slash commands (SKILL.md per skill)
├── tools/            Operator-facing CLIs (session-report, usage-report, …)
├── scaffold/         Templates used by scaffold.sh
├── tests/            Structural + live integration tests
├── collections/      Qdrant collection schemas
├── install.sh        Installs primitives + tools to ~/.local/bin
├── scaffold.sh       Creates a new agent home repo from the v2.0 template
└── sync-agent-tooling.sh   Updates an existing home repo to the latest hooks
```

---

## Primitives

Each primitive is a small bash script that validates its inputs + env vars,
then calls one HTTP endpoint. All Qdrant primitives honour `AGENTSONLY_QDRANT_URL`
and `PODZONE_QDRANT_APIKEY`.

| Script | Auth | Arguments |
|---|---|---|
| `qdrant/add-qdrant-point.sh` | `PODZONE_QDRANT_APIKEY` | `collection id vector_json payload_json` |
| `qdrant/patch-qdrant-payload.sh` | `PODZONE_QDRANT_APIKEY` | `collection id payload_json` |
| `qdrant/scroll-qdrant.sh` | `PODZONE_QDRANT_APIKEY` | `collection [limit] [filter_json]` |
| `qdrant/search-qdrant.sh` | `PODZONE_QDRANT_APIKEY` | `collection query [limit] [filter_json] [embed_model] [ollama_host]` |
| `ollama/embed-text.sh` | none | `text [ollama_host]` |
| `telegram/send-telegram-message.sh` | `PODZONE_CLOUD_BOT_TOKEN` (or test variant) | `chat_id text` |
| `gmail/create-gmail-draft.sh` | OAuth token file | `--to --subject --body [--attachment]` |

### Adding a primitive

1. Create `primitives/<service>/<verb-resource>.sh` (e.g. `qdrant/search-qdrant.sh`).
   Follow the header convention in existing primitives: a one-line purpose,
   the `Usage:` line, the `Auth:` line, then `set -euo pipefail`.
2. Validate required args and env vars with `${1:?...}` / `${VAR:?...}`.
3. Add a structural test at `tests/<service>/test-<verb-resource>.sh` that exercises
   missing-arg and missing-env-var cases. Optionally extend `tests/test_primitives.sh`
   with a live integration test if you have a fixture environment.
4. Run `./tests/run-all.sh` — must pass before merge.

---

## Hooks

Hooks live under `hooks/` and are deployed into **agent home repos** by
`scaffold.sh` / `sync-agent-tooling.sh`, not directly into `~/.claude/`.
A home repo's `.claude/settings.json` references them via repo-relative paths.

| File | Event | Purpose |
|---|---|---|
| `session-context.{py,sh}` | SessionStart | Inject agent identity + active tasks |
| `startup.sh` | SessionStart | Role-aware startup (resolved from identity file) |
| `ingest-transcript.{py,sh}` | SessionEnd | Embed user turns → Qdrant `prompt_logs` |
| `stop-heartbeat.{py,sh}` | Stop | Record session heartbeat |
| `stop.sh` | Stop | Roll up the stop event (calls heartbeat + sync) |
| `session-end.sh` | SessionEnd | Final session summary write |
| `subagent-stop.{py,sh}` | SubagentStop | Record subagent stop event |
| `sync-tasks.{py,sh}` | PostToolUse | Sync task events to Qdrant |
| `post-tool-use.sh` | PostToolUse | Multiplexer for the PostToolUse event |
| `pre-tool-use.sh` | PreToolUse | Multiplexer for the PreToolUse event |
| `user-prompt-submit.sh` | UserPromptSubmit | Capture operator prompts |
| `post-compact.sh` | PostCompact | Record context-compaction events |
| `notify-pr.py` | PostToolUse | Telegram notification on PR creation |
| `telegram-notify.py` | SessionEnd | Telegram session summary |
| `task-event.sh` | manual | Helper called by other hooks |
| `setup-collection.sh` | manual | Idempotent Qdrant collection creator |

`hooks/settings-snippet.json` shows the canonical wiring; copy entries from
there into your home repo's `.claude/settings.json`.

---

## Skills

`skills/` contains `SKILL.md` files for shared slash commands:

| Skill | Purpose |
|---|---|
| `session-start` | Load programme context + show active tasks |
| `session-end` | Summarise the session, update status digest |
| `launch-session` | Spawn an isolated worktree + VS Code session for a brief |
| `consolidate-tasks` | Merge per-agent outbox files into a single task board |

To make them available in Claude Code, point `skillDirectories` at this folder:

```json
{ "skillDirectories": ["~/workspace/agent-tooling/skills"] }
```

---

## Tools

Operator-facing CLIs under `tools/` and exposed via `install.sh` as wrappers in
`~/.local/bin`:

| Tool | Purpose |
|---|---|
| `session-report.sh` | Reconstruct a session timeline from Qdrant events |
| `usage-report.py` | Aggregate usage over a time window |
| `efficiency-report.py` | Tool-call efficiency + retry-loop detection |
| `rollup-report.py` | Weekly / monthly programme rollups |
| `decay-detector.py` | Surface structurally-decayed files (long, fragmented) |
| `backfill-prompt-logs.py` | One-shot backfill of prompt logs into Qdrant |
| `backfill-sessions.py` | One-shot backfill of session metadata |
| `upsert-current-session.py` | Manual sync of the currently-running session |

`usage-report.py`, `efficiency-report.py`, and `rollup-report.py` write their
markdown into a podzone-internal default directory
(`~/workspace/podzoneAgentTeam/team/hermes/outgoing/usage-reports/`). External
adopters can redirect output with `--out-dir <path>` — filename derivation is
unchanged.

---

## Collections

Qdrant collection schemas live in `collections/`. Each YAML file documents the
collection's dimensions, distance metric, and payload contract.

`work_items.yaml` is the canonical schema for cross-agent task records.

---

## Creating a new agent home repo

```bash
./scaffold.sh {team} {agent} {role-class}
```

Roles: `team-lead`, `coder`, `archivist`, `trainer`, `cluster-operator`.

The scaffold stamps out a home repo with `.claude/settings.json` already wired
to this repo's hooks. Run `./sync-agent-tooling.sh` from inside the home repo
to pull hook updates later.

---

## Tests

```bash
./tests/run-all.sh           # structural tests (no live services needed)
./tests/test_primitives.sh   # live integration (needs Qdrant + Ollama reachable)
```

See `tests/fixtures/README.md` for the fixture environment expected by the live
tests (collection name, point IDs, test bot, …).

---

## What's `PODZONE_…` and `AGENTSONLY_…` about?

This repo originated as podzone's internal agent tooling, so a few env-var
names still carry that prefix:

- `PODZONE_QDRANT_APIKEY` — Qdrant API key
- `AGENTSONLY_QDRANT_URL` — Qdrant base URL (falls back to a podzone default)
- `PODZONE_CLOUD_BOT_TOKEN` — Telegram bot token
- `OLLAMA_HOST` — standard upstream Ollama env var

For external use, set those variables to your own values; the defaults baked
into the primitives point at podzone-internal hosts and won't be reachable.

---

## Contributing

This repo is maintained as the in-use tooling for the podzone agent fleet,
so changes are reviewed against that workload first. External issues and PRs
are welcome via <https://github.com/PodZonePlatformEngineering/agent-tooling>;
expect maintainer responses to be best-effort.
