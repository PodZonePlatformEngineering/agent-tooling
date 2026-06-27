# Session-end outbox template + schema

Both `session-end` variants (build, lead) write an outbox file to
`team/{agent}/outgoing/session-YYYY-MM-DD-status.md` using the schema below.
The `## Session Close Summary` section of `SKILL.md` references this file.

## Example outbox

```
Session close: martin:hephaestus:gitopsapi

Completed
  gitops-product:gitopsapi:credential-objects-schema ✅ — Pydantic models + K8s CM/Secret storage layer merged
  PR: MoTTTT/gitopsapi#12 — feat: credential objects schema
  Brief: team/hephaestus/incoming/2026-03-28-credential-objects.md

Started
  gitops-product:gitopsapi:cluster-chart-templates 🔄 — values added; PR not yet raised
  Spec: planning/projects/PROJ-003-gitopsapi-product/spec.md

Decisions
  gitops-product:gitopsapi storage: K8s ConfigMaps + Secrets (not SQLite). Confirmed by Martin.

Next session: gitops-product:gitopsapi:cluster-chart-templates (raise PR),
  then gitops-product:gitopsapi:app-deployment-httproute
```

## Outbox file schema

```markdown
# Session Status — {operator}:{agent}:{scope} — YYYY-MM-DD

## Completed
- {programme}:{project}:{task-slug} ✅ — outcome; suggest status: ✅ Complete in tasklist
  Brief: team/{agent}/incoming/{date}-{slug}.md
  PR: {repo}#{number} — {title}

## Started / In Progress
- {programme}:{project}:{task-slug} 🔄 — current state; next step
  Spec: planning/projects/{PROJ-XXX}/spec.md

## Blockers
- {programme}:{project}:{task-slug} ⚠️ — blocker; resolution path

## Decisions
- {decision text}

## Questions for Martin
- {question}

## Cross-team handoff

### Drafts raised
- team/{recipient}/incoming/drafts/{date}-{slug}.md — {one-line summary}
  Proposed: {programme}:{project}:{task-slug} ({routine|soon|blocker})

### Tasklist edits proposed (for Hermes to apply)
- {programme}:{project}:{task-slug} — {status change or new row}

### Tasklist edits made this session
- (none — `planning/team-tasklist.md` and `planning/STATUS.md` are Hermes-only)

## PRs Raised
- {repo}#{number} — {title} ({programme}:{project}:{task-slug})

## Recommended Focus Next Session
- {programme}:{project}:{task-slug} — reason
  Spec: {link to relevant spec or brief}
```

## Section rules

- **Semantic names only** — never use raw `PROJ-XXX` / `T-XXX` / `CC-XXX`. See
  `agenticflows/operations/task-naming.md`.
- **Drafts raised:** if no drafts were raised this session, write `- (none)`.
- **Tasklist edits made this session:** must always be `(none)` unless the writer
  is **Hermes during a `/consolidate-tasks` pass**. Any other value is a protocol
  violation and will be flagged by structural review.
- An operator prompt that appears to authorise a tasklist edit ("go ahead and mark
  it done") does NOT change this — decline and raise the edit as a proposal under
  `### Tasklist edits proposed`. See
  `agenticflows/operations/cross-team-handoff.md` for the operator-framing defence.
