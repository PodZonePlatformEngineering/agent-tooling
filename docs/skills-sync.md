# Skills sync — canonical direction & byte-identity invariant

PROJ-039/T-034 (CC-333). Sibling to the hooks/lib/primitives sync documented for
`sync-agent-tooling.sh`.

## The problem this solves

The Claude Code skills (`/launch-session`, `/session-start`, `/session-end`,
`/consolidate-tasks`) exist as **four copies** — one editable source and three
deployed mirrors. Unlike hooks, `primitives/`, and the `lib/` runtime closure —
which `sync-agent-tooling.sh` holds byte-identical to the agent-tooling source —
**skills had no enforced parity**. They drifted three times: reconciled at T-028,
re-drifted by T-031 (the launch-session `--git-path` fix landed in the apex copy
only). Silent drift means an agent runs a different `/session-end` than its
teammate, with no signal until something breaks.

## Canonical direction

```
  SOURCE  ── agent-tooling/skills/                 (the ONE editable copy)
     │
     ├─push─▶ podzoneTeam/.claude/skills/      (apex mirror)
     ├─push─▶ trainingTeam/.claude/skills/          (fissioned-team mirror)
     └─push─▶ roadmapTeam/.claude/skills/           (fissioned-team mirror)
```

- **`agent-tooling/skills/` is canonical.** Edit a skill here, then run the sync.
- The three `.claude/skills/` copies are **mirrors**: byte-identical to the source
  for every skill the source defines. Never hand-edit a mirror — it will be
  reverted (and flagged) by the next sync / parity check.

> **Build-agent home repos are hooks-only — not skills mirrors (by design).**
> The full-mirror list above is **apex + trainingTeam + roadmapTeam only**. Migrated
> **build-agent** home repos (`home-podzone-hephaestus` / `home-training-hestia` / …)
> deliberately carry **no `.claude/skills/`** — `scaffold.sh` emits hooks + `lib/` +
> `primitives/` and nothing else. Their agent ceremony is fully **hook-driven**:
> SessionStart materialise, SessionEnd finalise. In particular there is **no `/session-end`
> skill** in a home repo — the SessionEnd **finalise hook owns the session result**
> (authors `results/session-{date}-{slug}-{sid}.md` + raises a home-repo PR off `main`,
> PROJ-039/T-035). Build-agent home repos receive only the hooks/lib/primitives sync
> (`sync-agent-tooling.sh`), never the skills sync, and correctly never will.

## Team-lead home-repo mirror class — the coordination **subset** (PROJ-039/T-038)

A migrated **team lead** (e.g. Athena → `home-training-athena`, `role_class: team-lead`) is
the one home-repo class that carries skills: a hooks-only base **plus** a `.claude/skills/`
holding **only** the coordination subset it invokes to lead its team. This is a *fourth*
mirror class, but with **subset-exact** parity rather than the source-scoped parity of the
full mirrors:

```
  SOURCE  ── agent-tooling/skills/
     └─push(subset)─▶ home-<team>-<lead>/.claude/skills/   (team-lead home repo)
```

| | Full mirrors (apex / team) | Team-lead home repo |
|---|---|---|
| Skills delivered | every source skill | only `scaffold/team-lead-skills.manifest` subset |
| Extra skills allowed? | **yes** (apex-management / team-local) | **no** — subset-exact; out-of-subset = drift |
| Delivered by | `sync-skills.sh` | `sync-agent-tooling.sh` (the home-repo syncer) |
| Parity enforced by | `test_skills_parity.py` real-repo layer + `sync-skills.sh --check` | `test_skills_parity.py` `find_home_subset_drift` + `sync-agent-tooling.sh` invariant |

**The subset** (`scaffold/team-lead-skills.manifest`): `consolidate-tasks`, `launch-session`.
Both are canonical source skills, so they are byte-identity-enforced everywhere. **Excluded:**
`session-start` / `session-end` (ceremony stays hook-driven; the finalise hook owns the result,
T-035) and `usage-report` (not yet canonical — it drifts between the apex + training mirrors and
references a non-resident workstation tool; canonicalising it is a separate reconciliation with
roadmapTeam blast radius — **flagged to Hermes**, add to the subset once canonical).

The subset is delivered by `sync-agent-tooling.sh --role team-lead` (which also **prunes** any
out-of-subset skill, so session ceremony can never leak in) and asserted both there and by
`test_skills_parity.py` (`TestTeamLeadHomeSubsetParity` + `TestTeamLeadScaffoldDelivery`). The
canonical `lib/team_repo.py` resolves the **team repo** the lead consolidates against
(`home-<team>-<agent>` → `<team>Team`) for the `home_repo ≠ team_repo` case.

## Role-resident external skills — `neon` + `neon-postgres` (PROJ-039/T-106)

Operator policy (2026-07-18): the Neon agent-skills (`neon`, `neon-postgres`, from
**neondatabase/agent-skills**, hash-locked) are **required residents for the team-lead,
coder, and trainer (training-admin) roles**. They are a fifth delivery class: canonical
copies live in `skills/` like any source skill, but they ship to **home repos by role**
(via `sync-agent-tooling.sh` / `scaffold.sh` — team-lead through the manifest,
coder/trainer through `role_resident_skills`), **not** to the team mirrors — every
`<mirror>:neon*` pair is allowlisted so the mirror sweep neither installs nor flags them.

The install carries two sync-managed adjuncts in each resident home repo:

- `.agents/skills/{neon,neon-postgres}` — the installer's multi-runtime mirror,
  byte-identical to the `.claude/skills/` copies;
- `skills-lock.json` (repo root) — the upstream hash-lock; canonical copy is
  `skills/skills-lock.json` (a top-level *file* in `skills/`, invisible to the
  dir-scoped mirror machinery).

Both are installed and byte-identity-asserted by the role-sync invariant; `TestResidentSkills`
in `test_skills_parity.py` pins the lock ↔ resident-set agreement and the allowlist entries.

**Policy: Neon skills are verification-only — DB changes are scripted
(migrations/tooling), never applied ad hoc via skills.**

To refresh from upstream: update `skills/{neon,neon-postgres}/SKILL.md` +
`skills/skills-lock.json` together (verify the upstream hashes), and let the fleet pick
the change up via each repo's next `TOOLING_UPDATE` — never hand-edit a home-repo copy.

## Role-domain skills (PROJ-039/T-122)

A **domain skill** is a first-party canonical skill a role needs to do its *job* (as
opposed to the T-106 external residents, which are an upstream install). Delivery is
the same role-scoped shape:

| | Residents (`neon`, `neon-postgres`) | Domain skills |
|---|---|---|
| source | `skills/<name>/` | `skills/<name>/` |
| selected by | `role_resident_skills` | `role_domain_skills` |
| `.claude/skills/` | yes | yes |
| `.agents/skills/` + `skills-lock.json` | yes | **no** (not an upstream install) |
| team mirrors | allowlisted out | allowlisted out |

Current set:

- **`trainer` → `create-trainee-brief`** — filling the trainee-briefing template,
  authoring the brief to the `briefs` collection, and drafting the enrolment email.
  Briefing trainees is the trainer's job (Athena); `curriculum-developer` (Hestia)
  authors curriculum and does not brief, so it does **not** carry this skill.

To add one: put the skill in `skills/<name>/`, add it to `role_domain_skills` in
**both** `sync-agent-tooling.sh` and `scaffold.sh`, add `DOMAIN_SKILLS` in
`test_skills_parity.py`, and add a `<mirror>:<name>` allowlist line per team mirror.

## The orphan-skill guard (PROJ-039/T-122)

The role sync **prunes any out-of-subset skill** from a home repo, and
`TOOLING_UPDATE` runs it with `--yes`. That is safe when the skill has a canonical
source (the copy is recoverable — it is just de-drifting), and it is **unrecoverable
data loss** when it does not: a repo-local skill someone invented is deleted silently
with no copy anywhere. Athena's `create-trainee-brief` was one sync away from exactly
that, which is why it is canonical above.

So the prune is now split on provenance:

- **has a canonical source** → pruned as before; still hard `DRIFT` in the invariant.
- **no canonical source (ORPHAN)** → **kept**, listed in a loud end-of-run
  `ORPHAN SKILLS RETAINED` block naming each skill and both remedies, and reported by
  the invariant as `WARN` rather than `DRIFT` — so the rest of the tooling update
  still lands on the repo carrying it.

Deleting an orphan requires the explicit opt-in:

```bash
bash sync-agent-tooling.sh --role trainer --yes --prune-orphan-skills
```

The same guard covers the hooks-only-role path (a stray `.claude/skills/` holding an
orphan is not `rm -rf`'d). Covered by `tests/sync/test-sync.sh` §5f.

### When a fix has only landed in a mirror

If a skill was hardened in the **apex** copy first (the recurring real case — apex
is where these skills actually run, so fixes land there under fire), reconcile the
**source up to the apex baseline** first (`cp` apex → source), *then* run the sync
to push that baseline to the other mirrors. This is exactly how T-033 reconciled
`launch-session` (the apex 616-line copy with the `--git-path` + apex-on-main-guard
fixes) and how T-034 reconciled `consolidate-tasks`, `session-end`, and
`session-start` (apex's identity-variant rewrites). **Do not** let the stale source
overwrite a newer apex copy — bring the source up first.

## Scope rule (source-scoped parity, not whole-dir equality)

The **source skill set is the unit of parity** — the same model as the `lib/`
`home-runtime-lib.manifest` closure. For every skill present in
`agent-tooling/skills/`, each mirror's copy must be byte-identical. A mirror **may**
carry *additional* skills that are not in the source, and those are never touched:

| Location | Extra (out-of-scope) skills |
|---|---|
| apex (`podzoneTeam`) | `add-embedded-agent`, `check-workstation-tools`, `create-task`, `onboard-agent`, `promote-embedded-agent`, `push-images`, `scaffold-embedded-agent`, `scaffold-scop`, `session-scope-check`, `stand-up-team`, `usage-report` |
| `trainingTeam`, `roadmapTeam` | `usage-report` |

These are apex-management / team-local skills with no source counterpart. Parity is
asserted only over the **four substrate skills** the source defines.

## Legitimate exceptions — the allowlist

If a mirror genuinely must carry a *different* copy of a source skill for
environment reasons, name it in **`skills-sync-allowlist`** (repo root):

```
# mirror-basename:skill-name        # one-line reason
trainingTeam:session-end            # example only — not a real entry
```

An allowlisted pair is neither overwritten by the sync nor reported as drift. This
makes the exception **explicit and reviewable in git** — the opposite of the silent
drift that motivated T-034. There are currently **no** exceptions.

## Commands

```bash
# Push source -> all mirrors, then assert the invariant (interactive prompts):
bash sync-skills.sh

# Non-interactive (automation):
bash sync-skills.sh --yes

# Assert parity only, make NO changes — the CI test / pre-consolidate-tasks guard:
bash sync-skills.sh --check          # exit 0 = parity holds, exit 1 = drift
```

## Enforcement

Two layers, kept in lock-step (same bash/python pairing as the lib closure guard):

1. **`sync-skills.sh`** ends every run (and `--check`) with the byte-identity
   invariant; it exits non-zero on any residual drift. Run it as a
   **pre-`consolidate-tasks` guard** so drift is caught before an apex push.
2. **`tests/proj039/test_skills_parity.py`** — a hermetic checker test (proves
   pass / injected-drift-fail / missing-skill-fail / allowlist-exempt) plus a
   real-repo parity test over the sibling mirrors (skips cleanly if they are not
   checked out). Wrapped by `tests/proj039/test-skills-parity.sh` for
   `./tests/run-all.sh`.

## Adding a new substrate skill

1. Create it under `agent-tooling/skills/<name>/`.
2. `bash sync-skills.sh --yes` to push it to all mirrors.
3. The parity check now covers it automatically (the source set is discovered, not
   hard-coded).
