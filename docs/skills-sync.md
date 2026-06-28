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
     ├─push─▶ podzoneAgentTeam/.claude/skills/      (apex mirror)
     ├─push─▶ trainingTeam/.claude/skills/          (fissioned-team mirror)
     └─push─▶ roadmapTeam/.claude/skills/           (fissioned-team mirror)
```

- **`agent-tooling/skills/` is canonical.** Edit a skill here, then run the sync.
- The three `.claude/skills/` copies are **mirrors**: byte-identical to the source
  for every skill the source defines. Never hand-edit a mirror — it will be
  reverted (and flagged) by the next sync / parity check.

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
| apex (`podzoneAgentTeam`) | `add-embedded-agent`, `check-workstation-tools`, `create-task`, `onboard-agent`, `promote-embedded-agent`, `push-images`, `scaffold-embedded-agent`, `scaffold-scop`, `session-scope-check`, `stand-up-team`, `usage-report` |
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
