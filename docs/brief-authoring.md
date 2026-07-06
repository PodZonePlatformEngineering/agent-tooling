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
5. **Self-banking furniture is mandatory.** Every headless prompt carries
   "commit early and often so a limit-stop self-banks". Post-T-060, session-branch
   commits survive finalise (the result PR forks from the session branch), so a
   banked limit-stop needs zero Team Lead rescue — re-launch fresh with the same
   `BRIEF_ID` and the brief accumulates the new sid.

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

## Model-tier quick reference

| Work shape | Tier | Evidence |
|---|---|---|
| Defect diagnosis, new mechanism, design judgement | default (Opus-class) | T-054/T-060/T-063 class |
| Apply a proven pattern N times across repos | `--model sonnet` | T-059, T-061 sweeps |
| Pure file-copy / rename / re-ingest batches | `--model haiku` viable | (none dispatched yet — start with sonnet) |
| Parallelisable subtasks inside a reasoning brief | subagent fan-out | PROJ-035/T-011 feasibility (CC-285) |
