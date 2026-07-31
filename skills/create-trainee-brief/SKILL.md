---
name: create-trainee-brief
description: Fill the trainee-briefing template from onboarding info, author the brief to the briefs collection, and draft the enrolment email (draft only, never send)
---

PROJ-011/T-038 (WS-2.2). Not a coordination skill — trainer (Athena) is a dispatched
build-agent role with no coordination-skill subset (§ AGENTS.md), so this is a
domain/task skill scoped to the training-programme onboarding job, resident in this
home repo pending Hermes confirming that placement (raised — see the T-038 session
result; the training-replatform-plan §2.2 predates the 2026-07-17 team-lead → trainer
re-role and calls this a "team-lead skill", which no longer describes Athena).

Inputs, plan, and template are read via the `.workspace/podzoneTeam` clone (this
skill never writes there — the template stays owned by its own T-038 PR).

## Inputs

Collect (ask if not given):

- **Trainee name** — full name.
- **GitHub handle** — becomes `{trainee}` and the repo suffix
  (`podzone-training-<handle>`, plan §1.2 R-4).
- **Curriculum slug** — `{curriculum_slug}`, one of the estate's canonical slugs
  (`planning/projects/PROJ-011-academy/curriculum-naming-convention.md` §2 — e.g.
  `prompt-engineering`, `code-ai`, `finops`, `governance`, `applied-ai`).
- **Track/tier** — `{track}`, if the curriculum has tracks (else "—").
- **Cohort / start date**.
- **Start module** — the first module id in that curriculum's scheme (e.g. `M1`).
  Ask which modules to include/skip if the take-on conversation flagged any
  customisation, and record it in the programme summary.

## Steps

1. **Resolve the curriculum's module map.** From the onboarding info and (if needed)
   the curriculum repo's module list, build `{module_map}`: an ordered module list +
   the trainee's start point. This is the one field that may need a quick lookup
   rather than a direct answer from the intake.

2. **Fill the brief body** from
   `.workspace/podzoneTeam/planning/projects/PROJ-011-academy/templates/trainee-briefing/trainee-brief.md`
   (T-038 template — do not use an older cached copy). Substitute:
   `{{TRAINEE_NAME}}`, `{{TRAINEE_HANDLE}}`, `{{CURRICULUM_ID}}`/`{{CURRICULUM_TITLE}}`,
   `{{TRACK_LABEL}}`, `{{MODULE_MAP}}`, `{{START_DATE}}`, `{{GENERATED_DATE}}`
   (today), `{{TEMPLATE_VERSION}}` (the template's current git rev/tag),
   `{{COORDINATOR_CONTACT}}` (Athena), `{{PROGRAMME_SUMMARY}}` (2-4 sentences per the
   template's own guidance), `{{OPERATIONAL_BRIEF_ID}}` and `{{CONFIG_FILE_PATH}}`
   (leave as literal placeholders — those are filled by the trainee repo's own hooks
   at repo-generation time, not by this skill). Write the filled body to a scratch
   file (not committed — the brief body lives in Qdrant, not in a repo).

3. **Author the brief** with `agent-tooling/tools/create-brief.py` (trainee form —
   `--assignee-type trainee`):

   ```bash
   python3 agent-tooling/tools/create-brief.py \
     --brief-id "training/$(date +%F)-{curriculum_slug}-{trainee}" \
     --team training --author athena \
     --assignee "{trainee-github-handle}" --assignee-type trainee \
     --body-file {scratch-file} \
     --summary "{one-line: name, curriculum, start module}" \
     [--work-item PROJ-011/T-YYY ...]
   ```

   Leave `--status` at the default (`draft`) unless the operator has already
   confirmed the take-on — promote with `--approve` only on explicit go-ahead
   (mirrors the worker-brief approval gate other briefs use; a trainee brief is not
   a peer-to-peer lead brief and should not self-approve).

4. **Verify read-back.** Call `lib.brief_substrate.get_brief(brief_id)` (or scroll
   the `briefs` collection by `brief_id`) and confirm the body round-trips and
   `status` is what was requested. Do not report success without this check.

5. **Draft the enrolment email** (Gmail MCP `create_draft` — draft only, **never**
   `send`). Content, per plan §2.2:
   - Repo-creation link: the *Use this template* / `generate` link for
     `PodZonePlatformEngineering/home-training-template`, with the trainee's repo
     name pre-filled: `.../home-training-template/generate?name=podzone-training-{handle}&owner={handle}`.
   - A short note that the repo must be **private**.
   - Anthropic API-key setup guide pointer (per academy D-2 — the trainee runs
     sessions on their own credential; link or attach whatever the current setup
     doc is — check `trainee-onboarding/` for the live guide before assuming one).
   - The **first-prompt line** (R-1): tell the trainee to paste
     `Brief: training/{date}-{curriculum_slug}-{trainee}` as their very first message
     once the repo is created and they open a session.
   - Do **not** include repo mechanics beyond that — branching/PR/push is hook-owned
     (R-2/R-3) and needs no trainee instruction.

6. **Report** the `brief_id`, the draft's id/subject, and the read-back verification
   result. Do not send the draft. Do not re-author over a live trainee's existing
   `brief_id` in this skill without the operator's explicit go-ahead — re-authoring a
   live brief is the convergent-brief mechanism (same `brief_id`, new body) and is a
   deliberate, gated action, not this skill's default path.

## Guardrails

- Never invent a curriculum slug, module scheme, or repo name — pull from
  `curriculum-naming-convention.md` or ask.
- Never put secrets (API keys, tokens) in the brief body or the email draft — link to
  the setup guide instead.
- This skill authors to Qdrant and drafts an email; it does not touch the trainee's
  repo (that repo does not exist yet at this point in the flow — R-4 has the trainee
  create it themselves from the template).
