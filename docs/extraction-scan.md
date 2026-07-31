# Extraction scanner — operating guide (PROJ-011/T-126)

**Control of record:** `podzoneTeam/planning/projects/PROJ-011-academy/session-to-curriculum-extraction-gate.md`
(Athena, PROJ-011/T-124). **Spec:** `team/athena/outgoing/task-proposal-2026-07-31-t124-extraction-gate.md` §P1.
**Paired with:** PROJ-039/T-123, the brief-side clause — see `docs/brief-authoring.md`.

The gate is a checklist owned by the extracting agent. This is the mechanical half:
the part a machine does better than an attentive human at scale. It does not replace
the checklist and cannot.

> **What this gates:** beta-cohort onboarding. Athena's finding was that the documented
> procedure holds for PROJ-009/T-012's bounded exposure and does **not** hold for a
> beta cohort — a scale control, not a T-012 blocker.

## What it checks

| Tier | Behaviour | Contents |
|---|---|---|
| 1 | **hard fail** | SA ID numbers (checksum-validated), passport identifiers in a passport context, contact details, credential and secret material |
| 2 | **hard fail** | the §7 declaration: present, well-formed, covering the destination boundary, inside what the brief authorised |
| 3 | warn only | participant names at B1/B3/B4, non-round amounts, corroborated real-world dates, transcript shape |

**Tier 2 is the highest-value check in the set** and the one to keep if anything has to
give. It does not guess at content, so it cannot be wrong about it: it verifies that
the declaration is there and says what it must. Absence is a one-line grep.

Two scoping rules do most of the work of keeping this usable:

- **Tiers 1 and 3 run over added lines only** — the extract. Re-reporting untouched
  lines in a modified document is noise, and noise gets a scanner switched off.
- **Nothing fires outside an extraction destination.** This is an extraction control,
  not a general-purpose secret scanner. A credential in application code is a real
  problem and a different control's problem.

### Tier 3 and the constraint it lives by

Athena's constraint, taken literally: tier 3 is **silent on Class P at B2**. Participant
attribution is permitted there (gate §2.1) — it is often the point of the document — so
the participant-name check simply does not run at B2. Precision beats recall in this
tier, and everything in it warns rather than fails.

Two consequences of taking that seriously:

- The date check only fires when **corroborated** by a participant name or an exact
  amount on the same line. A planning corpus is made of dates; an uncorroborated date
  warning is how the tier gets disabled.
- Transcript detection requires a **recurring** speaker. Runs of distinct labels are
  definition lists and email headers, which is most structured markdown.

Measured over the full `podzoneTeam` planning + team history: **0 tier-1 findings**,
72 tier-3 warnings, of which 64 vanish once a roster is configured. In `--diff` mode
that is approximately zero per pull request.

## The participant roster

Held in one maintained file, never hardcoded, and **deliberately not shipped in this
repository** — `agent-tooling` is public, and a list of real participant names is
exactly the Class P material the gate exists to contain. `data/participant-roster.example.json`
is fictional and documents the schema.

The real roster lives in a private repo and is passed by path:

```bash
extraction-scan.py --diff origin/main --roster ~/workspace/podzoneTeam/planning/participant-roster.json
# or: export EXTRACTION_ROSTER=...
```

**With no roster configured** the tier-3 name check does not run, and the scanner says
so in its summary rather than passing silently. One tier-1 behaviour also degrades: an
email address at B2 warns instead of failing, because the gate permits Class P at B2
and without a roster a participant's address is indistinguishable from a client's —
blocking would be a guess, and a wrong guess at tier 1 blocks a legitimate document.
Configuring the roster promotes it back to a hard fail for Class A.

## Where it runs

| Instance | Status | Notes |
|---|---|---|
| CI on pull requests | **authoritative** | `scaffold/ci/extraction-scan.yml.template` → `.github/workflows/` in `podzoneTeam` + curriculum repos |
| pre-commit | advisory | `scaffold/ci/pre-commit-extraction-scan.sh`; fast feedback at the real firing moment, bypassable with `--no-verify` |
| `create-brief.py` | **authoritative for B4** | inline; see below |
| trainee repos | **never** | inside the session boundary, not a destination; installing it there re-creates the pre-push framing T-124 retired |

```bash
# CI mode — what this branch adds against a base
extraction-scan.py --diff origin/main --repo . --fail-on both

# with the brief, to catch boundary widening
extraction-scan.py --diff origin/main --brief team-lead/briefs/2026-07-31-x.md

# a corpus or a directory
extraction-scan.py --paths planning team --fail-on tier1

# B4 sweep
extraction-scan.py --substrate training-content
```

Exit codes: `0` clean (warnings may print), `1` blocking findings, `2` usage error.

## B4 — the decision

**B4 is covered, not deferred.** Athena flagged it as needing an explicit decision, and
this is it.

The asymmetry that makes B4 the worst boundary rather than the least: substrate upserts
are not pull requests, so CI cannot see them — *and* the PROJ-013 remedy, mint a clean
repo and delete the old one, **has no equivalent in a vector store** that agents read
from continuously. A leak into Qdrant is not straightforwardly revocable.

Coverage is in two parts, because the write paths have two different owners:

1. **Inline, at the write — for the paths this repository owns.** `create-brief.py`
   scans the brief body at B4 before upserting to the `briefs` collection and refuses
   on tier-1 material. `--skip-extraction-scan` overrides it and says so on stderr;
   use it only with a stated reason. A missing T-123 clause warns rather than blocks,
   deliberately: a hard failure would strand a Team Lead mid-dispatch on a brief
   authored before the clause existed. Promote it once the fleet has converged.
   `--dry-run` exercises both without a live upsert.
2. **A sweep — for the paths owned elsewhere.** Curriculum ingest into
   `training-content` / `academy-content` lives outside `agent-tooling`, so there is no
   write path here to hook. `--substrate <collection>` scans the text payloads of
   points already in a collection and is intended to run periodically.

**The residual, stated plainly:** part 2 is detection *after* the fact. Because B4 has
no revocation remedy, a sweep finding is not a fix — it is a gate §8 escalation, and
it must name the collection and the point ids. The honest position is that the sweep
narrows the window between a leak and its discovery; it does not close it. Closing it
means the ingest tooling calling this scanner at its own write path, which is a change
in a repository this task does not own.

## What it does not do

- **It does not adjudicate Category 3.** Whether a rounded figure is traceable to a
  real matter is a judgement call and always will be.
- **A clean scan is not a clean extract.** The declaration stays mandatory even when
  the scanner passes: the scanner checks what is detectable, the declaring agent checks
  what is not.
- **A declaration detects omission, not falsification** (gate §9). This tooling makes a
  missing check visible. It does not make a written block true, and a deliberate false
  declaration passes tier 2 exactly as a true one does.
- **Recall is deliberately traded for precision** in three named places: bare
  ten-digit runs are not treated as phone numbers, a line pairing a raw shape with its
  §5 placeholder is treated as demonstration context (credentials excepted), and
  passport shapes need a passport keyword nearby. Each was a false positive in the
  acceptance corpus, and each trade is a real, if narrow, recall loss.
