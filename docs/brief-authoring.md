# Brief authoring — sizing, tiering, and budget discipline (PROJ-039/T-058)

Operator-approved 2026-07-06 (CC-368). This is the Team Lead's checklist for scoping
a headless dispatch BEFORE writing the brief. The condensed version lives in
`skills/launch-session/SKILL.md` ("Brief sizing & model tiering") and rolls out to
every team-lead home repo via the T-034 skill sync; this doc carries the full
rationale and the brief-skeleton conventions.

## Why this exists (the evidence)

The PROJ-039 finalise-hardening bundle (2026-07-05/06) put **five tasks plus a
seven-repo fleet sweep in one brief**. It took **four runs across two subscription
windows** (two limit-stops, one disk-full crash), and every recovery run opened with
a review-the-bank re-orientation pass — pure compounding overhead. Re-scoped under
these rules, the follow-on dispatches (T-060+T-055, T-056+T-057, T-059, T-061,
T-062, T-063+T-064) **each closed in a single window**, and the two mechanical
sweeps ran clean on `--model sonnet`.

## The sizing checklist

Run this before authoring any headless brief:

1. **Count the tasks: ≤ 2–3, related, one-window-sized.** If you cannot plausibly
   argue the bundle finishes inside one subscription window, split it. A "phase 2"
   inside a brief is two briefs.
2. **Classify each task: reasoning or mechanical.**
   - *Reasoning* (design judgement, defect diagnosis, new mechanism): default model.
   - *Mechanical* ("apply a proven pattern N times": fleet sync PRs, per-repo
     rollouts, batch renames, re-ingests against a fixed schema): its own brief,
     dispatched `--model sonnet` (or `haiku` for pure file-copy work).
   Never mix a 7×-repeat mechanical tail into a reasoning brief — that tail is
   exactly what gets stranded at a limit-stop.
3. **Order by risk; name the deferrable tail.** Hardest/blocking task first. The
   brief's Conventions section names the explicitly droppable last step, and the
   launch prompt repeats it ("if budget tightens, deliver X alone with a clear
   per-task account"). A limit-stop should yield a mergeable partial delivery.
4. **Consider in-session fan-out** (PROJ-035/T-011): parallelisable mechanical
   subtasks *inside* a reasoning brief should be delegated by the agent to
   sonnet/haiku subagents instead of a separate dispatch — the expensive model
   keeps the core reasoning. Say so in the brief if you want it.
5. **Self-banking furniture is mandatory — for a directly-emitted `claude -p`
   launch.** Every prompt emitted by hand (Step 8 of `skills/launch-session/
   SKILL.md`) carries "commit early and often so a limit-stop self-banks".
   Post-T-060, session-branch commits survive finalise (the result PR forks
   from the session branch), so a banked limit-stop needs zero Team Lead
   rescue — re-launch fresh with the same `BRIEF_ID` and the brief accumulates
   the new sid. **This does NOT apply to a `launch.sh <brief-id>` dispatch**
   (PROJ-039/T-108) — the wrapper itself owns 100% of git ceremony (add,
   commit, push, branch, PR) for the home repo and every working repo, at
   every loop-exit boundary and at final cleanup, so the inner session never
   needs to run git at all. Drop the "commit early and often" clause entirely
   from a wrapper-launched brief's prompt furniture — the minimal prompt stays
   `"Hi {Agent}. Continue with the brief."`, nothing about git.

## Brief skeleton conventions

Every headless brief carries these sections (see any
`team-lead/briefs/2026-07-06-*.md` in `home-podzone-hephaestus` for worked
examples):

- **Header:** From/To/date, work items with CC numbers, mode line (headless
  one-shot, serial simple-repo, and the model tier if not default), authority
  (board rows + plan docs, with paths).
- **Objective:** one paragraph; state explicitly what is OUT of scope.
- **Context you can rely on:** pin the facts the agent must not re-derive or
  re-litigate (tags that exist, mechanisms already proven, prior-session sids).
- **Order of work:** hardest first; per task, the evidence, the invariant to
  encode, and the regression tests expected.
- **Delivery:** expected PRs by repo; `VERSION` bump + tag-reminder-for-the-lead
  (T-055 ritual); self-sync/live-proof instruction where the session's own close
  can prove the fix.
- **Conventions:** serial simple-repo; deferrable-tail clause;
  raise-to-lead-and-exit; `Brief-Status: complete` as the deterministic completion
  signal.
- **Extraction authorisation:** the standing clause below. Mandatory in every brief,
  including the (common) case where the answer is `none`.

The full skeleton, ready to copy, is `scaffold/brief.template`.

## The extraction-authorisation clause (PROJ-039/T-123)

**Control of record:** `podzoneTeam/planning/projects/PROJ-011-academy/session-to-curriculum-extraction-gate.md`
(Athena, PROJ-011/T-124). §3.1 obligation 1: *an agent cannot honour a control it was
never handed.* A brief is the only place the handing-over can happen, so every brief
carries the clause — an omitted clause is indistinguishable from an unconsidered one.

**Every brief carries this block verbatim**, with the boundary list filled in:

```markdown
## Extraction authorisation

**Extraction-gate:** `PROJ-011-academy/session-to-curriculum-extraction-gate.md`
- Boundaries authorised: none
```

`Boundaries authorised:` takes either `none` or a list drawn from the gate's §2
boundaries:

| token | boundary | destination |
|---|---|---|
| `B1` | → curriculum | module content, lab assets, glossary, tutor `AGENTS.md`, `home-training-template` |
| `B2` | → `podzoneTeam` planning | triage docs, proposals, board rows, session results, briefs, `team/*/outgoing/` |
| `B3` | → another trainee | material reused across trainees; one trainee's artefact in another's session |
| `B4` | → shared substrate | Qdrant collections; any non-git store — **no git-history remedy exists** |

Three rules the Team Lead applies when filling it in:

1. **Authorise the narrowest set that lets the work land.** The clause is a ceiling,
   not a forecast. `B2` does not imply `B1`; the gate's §3.1 fourth obligation is that
   the agent does not widen its own boundary, and the clause is what that is measured
   against.
2. **`none` is the normal answer** and must still be written. Most briefs build
   tooling, fix defects, or ship code, and carry no session content anywhere. Saying so
   explicitly is what makes the clause's absence detectable.
3. **If the brief authorises anything but `none`,** it also states which data classes
   are expected (Class A third-party / Class P participant) and points at the gate's
   §6 checklist. The agent then owes a §7 declaration in every artefact it lands.

### What the agent owes back — the declaration shape

The artefact-side declaration is **the gate's §7 block, unchanged**. It is Athena's
shape and this document does not redefine it; it is reproduced here only so the brief
and the checker are demonstrably reading the same thing:

```markdown
**Extraction declaration** — gate: `PROJ-011-academy/session-to-curriculum-extraction-gate.md`
- Boundaries crossed: B2 (session → podzoneTeam planning)
- Class A (third-party): none present / replaced as [TOKEN_n]
- Class P (participants): retained (B2 only) / removed
- Categories 3–5 judgement calls: <one line, or "none">
- Declared by: <agent> · session <sid> · <date>
```

`tools/extraction-scan.py` (PROJ-011/T-126) parses exactly these six lines: the
`**Extraction declaration**` header with its `gate:` reference, then the four labelled
bullets and the signature. Boundaries in the declaration must be a subset of the
boundaries the brief authorised — `--brief <path>` makes the scanner check that, which
is the mechanical form of "the headless agent does not widen its own boundary".

### The stated limit

A declaration detects **omission, not falsification** (gate §9). The clause makes a
missing check visible as a missing block; it does not make a written block true, and
neither does a clean scanner run. The declaring agent remains the owner (gate §3); the
scanner and the reviewer are the second line.

## Model-tier quick reference

| Work shape | Tier | Evidence |
|---|---|---|
| Defect diagnosis, new mechanism, design judgement | default (Opus-class) | T-054/T-060/T-063 class |
| Apply a proven pattern N times across repos | `--model sonnet` | T-059, T-061 sweeps |
| Pure file-copy / rename / re-ingest batches | `--model haiku` viable | (none dispatched yet — start with sonnet) |
| Parallelisable subtasks inside a reasoning brief | subagent fan-out | PROJ-035/T-011 feasibility (CC-285) |
