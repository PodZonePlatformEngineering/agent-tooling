# agent-tooling

Shared agenticflows skills, hooks, and bash primitives.

All podzone agent home repos reference this repo. `workstation-setup` runs `install.sh`
to deploy hooks to `~/.claude/hooks/` and points `skillDirectories` at `skills/`.

---

## Directory layout

```
agent-tooling/
├── primitives/          bash scripts callable from hooks or interactively
│   ├── qdrant/          add-qdrant-point, patch-qdrant-payload, scroll-qdrant
│   ├── telegram/        send-telegram-message
│   ├── gmail/           create-gmail-draft
│   └── ollama/          embed-text
├── skills/              Claude Code skill SKILL.md files
│   ├── session-start/
│   ├── session-end/
│   ├── consolidate-tasks/
│   └── launch-session/
├── hooks/               Python + bash hook scripts (deployed by install.sh)
├── tests/               Integration tests against real fixture environments
│   └── fixtures/        Fixture environment docs (Qdrant, Telegram, Gmail)
├── install.sh           Deploys hooks to ~/.claude/hooks/
└── README.md
```

---

## Primitives

Each primitive is a bash script under `primitives/<category>/`. They validate their
inputs and required env vars, then call the relevant API.

**Current stubs** (T-011 implements them):

| Script | Auth env var | Parameters |
|---|---|---|
| `qdrant/add-qdrant-point.sh` | `PODZONE_QDRANT_APIKEY` | collection, id, vector_json, payload_json |
| `qdrant/patch-qdrant-payload.sh` | `PODZONE_QDRANT_APIKEY` | collection, id, payload_json |
| `qdrant/scroll-qdrant.sh` | `PODZONE_QDRANT_APIKEY` | collection, filter_json, limit |
| `telegram/send-telegram-message.sh` | `PODZONE_CLOUD_BOT_TOKEN` | chat_id, text |
| `gmail/create-gmail-draft.sh` | OAuth token file | to, subject, body, [attachment_path] |
| `ollama/embed-text.sh` | none | text, [ollama_host] |

### Adding a primitive

1. Create `primitives/<category>/<name>.sh` following the header convention in existing stubs.
2. Add a corresponding test in `tests/<category>/test-<name>.sh`.
3. Run tests: `./tests/<category>/test-<name>.sh`
4. Tests must pass before the primitive is deployed via `install.sh`.

---

## Hooks

`hooks/` contains Python and bash scripts wired into Claude Code's hook system. They are
deployed by `install.sh` and must be configured in `~/.claude/settings.json`.

Hook inventory:

| File | Event | Purpose |
|---|---|---|
| `session-context.py` / `.sh` | SessionStart | Inject agent identity + active tasks |
| `ingest-transcript.py` / `.sh` | SessionEnd | Embed user turns → Qdrant `prompt_logs` |
| `stop-heartbeat.py` / `.sh` | Stop | Record session heartbeat in Qdrant |
| `subagent-stop.py` / `.sh` | SubagentStop | Record subagent stop event |
| `sync-tasks.py` / `.sh` | PostToolUse | Sync task events to Qdrant |
| `telegram-notify.py` | SessionEnd | Post session summary to Telegram |
| `notify-pr.py` | PostToolUse | Notify on PR creation |

---

## Skills

`skills/` contains `SKILL.md` files for shared Claude Code slash commands. Configure
in `~/.claude/settings.json`:

```json
"skillDirectories": ["/path/to/agent-tooling/skills"]
```

---

## Installation

```bash
git clone https://github.com/PodZonePlatformEngineering/agent-tooling.git ~/workspace/agent-tooling
cd ~/workspace/agent-tooling
./install.sh
```

Then add to `~/.claude/settings.json`:

```json
"skillDirectories": ["~/workspace/agent-tooling/skills"]
```

---

## Tests

Integration tests require live fixture environments. See `tests/fixtures/README.md`
for setup. Tests are run as part of T-011 primitive implementation.

```bash
./tests/run-all.sh
```

---

## Ownership

Maintained by Hephaestus (Claude Code). Changes require PR to `main`.
ADR: `podzoneAgentTeam/agenticflows/decisions/adr-007-agent-home-repos.md §D4`
