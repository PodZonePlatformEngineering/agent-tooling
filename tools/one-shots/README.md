# One-shot tools

## record_one_shot.py

Records a workflow observation into the `agent-workflow` Qdrant collection.
Any agent can use this at any moment — especially for logging frictions.

### Usage

```bash
python3 tools/one-shots/record_one_shot.py --from-file path/to/friction.yaml
```

### YAML schema

```yaml
one_shot_id: {agent}-{YYYY-MM-DD}-friction-{slug}   # unique; idempotent on re-run
agent: hermes                                         # agent logging the friction
team_lead: hermes
moment: friction                                      # or: session-start, session-end,
                                                      #     consolidate-tasks, brief,
                                                      #     post-session-review
session_date: 2026-05-14
task_slug: relevant-task-slug                         # task being worked on
programme: platform-buildout                          # programme shortform
linked_agent_id: null
linked_brief_id: null
thinking_text: |
  Short title: what the friction is.

  Observed: when/where.

  Symptom: what happens.

  Root cause: why.

  Impact: severity and frequency.

  Resolution: proposed fix or routing.
```

### Friction logging convention

- Frictions get IDs: `WF-001`, `WF-002`, etc. (sequential across all agents)
- Log any repeated friction — no need for Hermes approval first
- The YAML file lives in `frictions/` alongside this README or in the agent's outgoing directory
- Re-running with the same `one_shot_id` is safe (upserts idempotently)
- Cross-reference: `podzoneTeam/agenticflows/operations/workflow-frictions.md`
  for the human-readable friction log
