# Getting a `settings.json` change onto repos that already exist

PROJ-011/T-125 (CC-519). Companion to `docs/skills-sync.md` (the byte-identity
set) and the T-069 updater-wiring design.

## The problem

`.claude/settings.json` can never join the byte-identity sync set — its `env`
block is per-repo. Anything safety-critical living in that file therefore has to
be enforced **structurally** (patch/verify in place) rather than byte-copied.
That mechanism exists: `tools/wire-update-tooling.py`, which since T-125 enforces
two things for a trainee repo:

1. `update-tooling.py` at its canonical SessionStart position (T-069);
2. the `python3` **shell guard** on the preflight hook command (T-121) — a
   Python preflight structurally cannot catch its own interpreter being missing,
   so the guard has to be in the command string.

The gap T-125 closes is not the mechanism, it is the **delivery**: who runs it,
against the six trainee repos that already exist and live on trainees' laptops.

## Which channel actually reaches a live trainee repo

| Channel | Reaches the six live repos? |
|---|---|
| `scaffold.sh` | **No** — new repos only. |
| `sync-agent-tooling.sh` via `update-tooling.py` | **No.** The trainee chain wires the updater, but it no-ops unless `TOOLING_UPDATE` is set, and nothing sets it on a trainee launch (their `env` is `TRAINEE_RUNTIME` only). The trainee self-update channel is inert by design — trainees are not handed fleet-release tags. |
| Brief `files` channel (T-121 `trainee-materialise.py --apply`) | **No**, not for this. It is hook-driven, so it cannot fix a machine where hooks do not run, and settings.json is read before hooks anyway. |
| **Operator PR to each repo's `main`** | **Yes** — see below. |

The trainee repos are **org-owned** (`PodZonePlatformEngineering/home-training-*`),
so an operator can open a PR against each one. And the trainee's clone picks the
merge up on its own: every trainee session starts with
`trainee-finalise.py --guard` → `session_guard.preflight()` → `ff_main()`, which
fetches `origin/main` and fast-forwards the local clone before branching. The
trainee performs no git command; the pull is already part of their session.

Timing: Claude Code reads `settings.json` **before** hooks run, so a fix that
fast-forwards in during session *N* is live from session *N+1*.

## The honest limit

The one machine this does not reach is a machine with **no `python3` at all** —
it runs no hooks, so it never fast-forwards, so it never receives the guard that
exists for exactly that machine. There is no mechanism-only answer to this:

- a trainee in that state gets the guard by **pulling or re-cloning by hand**;
- so for that population the deliverable is a **trainer message**, not a PR.

New trainees are unaffected (they clone the template after the fix, so their
first session already has the guard) — which is the beta cohort, and the reason
the class fix matters more than the six-repo rescue.

## Running the delivery pass (operator)

```bash
# inspect: clones each repo to a temp dir, prints the diff, writes nothing
python3 tools/deliver-trainee-settings.py --dry-run

# open one PR per repo that needs it
python3 tools/deliver-trainee-settings.py --apply
```

`--dry-run` is genuinely read-only, including against `--local-clone <path>`
(it restores the file after rendering the diff). The repo list is explicit, not
an org wildcard, so `home-training-template` and future repos are never rewritten
without a decision. Idempotent: an already-guarded repo reports `ok`.

Only the SessionStart hook command strings change; `env` and every other hook
event are left alone (pinned by
`tests/proj011/test_deliver_trainee_settings.py`).

## Adding another structural settings.json invariant

1. Put the canonical value in `scaffold.sh`'s `role_settings_json` (new repos).
2. Add a patch + check function to `tools/wire-update-tooling.py` (live repos),
   and call it from `wire()` / `check()` for the roles it applies to.
3. Pin the two together with a scaffold-lockstep test — the constant in the
   patcher must equal what `scaffold.sh` writes, or the fleet ships two variants.
4. Deliver to already-live repos with `tools/deliver-trainee-settings.py`
   (trainee repos) or a `TOOLING_UPDATE` dispatch (agent home repos, where the
   sync does run).
