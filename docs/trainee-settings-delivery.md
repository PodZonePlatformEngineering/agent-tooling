# Getting a `settings.json` change onto repos that already exist

PROJ-011/T-125 (CC-519), re-scoped by **T-128 (CC-525)**. Companion to
`docs/skills-sync.md` (the byte-identity set) and the T-069 updater-wiring design.

## The problem

`.claude/settings.json` can never join the byte-identity sync set — its `env`
block is per-repo. Anything safety-critical living in that file therefore has to
be enforced **structurally** (patch/verify in place) rather than byte-copied.
That mechanism exists: `tools/wire-update-tooling.py`, which since T-125 enforces
two things for a trainee repo:

1. `update-tooling.py` at its canonical SessionStart position (T-069);
2. the `python3` guard on **every** hook command (T-121/T-125/T-128) — a Python
   preflight structurally cannot catch its own interpreter being missing, so the
   guard has to be at shell level, in the command strings.

The gap T-125 closes is not the mechanism, it is the **delivery**: who runs it,
against the six trainee repos that already exist and live on trainees' laptops.

## The guard: one shim, not eleven inline guards (T-128)

T-125 shipped the guard as an inline `command -v python3 … || echo …` on the
**preflight command only**. That is 1 of the trainee's 11 `python3` hook
invocations. A trainee with no Python got one friendly message at session start
and then a raw `python3: command not found` on every `UserPromptSubmit`
(telemetry) and every `PreToolUse` (read-guard) for the rest of the session —
in feel, worse than the honest failure it replaced.

T-128 replaces it with a single shim, `.claude/hooks/run-hook.sh`, that every
command routes through:

```
bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/run-hook.sh [--announce] <path-under-.claude> [args...]
```

Chosen over eleven inline guards for two reasons. Eleven guards are **eleven
places to forget** when hook #12 is added; the shim covers hook #12 by
construction. And a *single* inline guard already took `settings.json` from 6
lines to 76 — eleven would be unreadable.

What the shim must not break, and does not:

| Property | How it is preserved |
|---|---|
| Exit codes, stdout, stderr, stdin | With `python3` present the shim **`exec`s** it, replacing its own process. The hook's exit code *is* the process's exit code — so `PreToolUse` deny-on-exit-2 and `UserPromptSubmit` block-on-exit-2 are unchanged. The shim never reads stdin, so the event payload reaches the hook unconsumed. |
| `$CLAUDE_PROJECT_DIR` / cwd-independence (T-050/T-055) | The caller locates the shim via `$CLAUDE_PROJECT_DIR`; the shim locates `.claude/` from its own `$0`, by bash parameter expansion. Neither leg consults `$PWD`. |
| `timeout` keys (300s updater, 600s finalise) | They are **sibling keys** of the command entry, not part of the command string, so shimming cannot touch them. |
| Message frequency | `--announce` is carried by exactly **one** command — the first `SessionStart` hook. Every other command is silent on the missing-interpreter path (exit 0, no output), which for `PreToolUse`/`UserPromptSubmit` is the neutral "proceed" answer. One message per session, not one per hook. |

The shim's **only** job is the missing-interpreter path. With `python3` present
it is a transparent `exec`; it never suppresses, retries or rewrites anything
else. On the failure path it uses bash builtins only — no `dirname`, no
`basename` — because a machine missing `python3` may be missing more besides,
and an external command there would print its own `command not found` noise on
exactly the path whose purpose is a clean message.

It does add one dependency the trainee settings did not have before: `bash`
must be resolvable by name. That is the same dependency every *other* role's
`settings.json` has carried since the beginning (`bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/session-start.sh`
and friends), on the whole fleet — and the `"$CLAUDE_PROJECT_DIR"` expansion in
the pre-existing commands already required a POSIX shell to interpret them.

**Coverage is enforced, not eyeballed.** `wire-update-tooling.py --check` fails
on *any* unshimmed `python3` command in *any* hook event, and on more (or fewer)
than one `--announce`. `tests/proj039/test_wire_update_tooling.py` asserts the
same counts against the bytes `scaffold.sh` actually writes. Adding a hook
without coverage fails the suite rather than silently reopening the hole.

**The shim and the settings must ship together.** A `settings.json` pointing at
a `run-hook.sh` the repo does not carry fails every hook with `No such file or
directory` — strictly worse than the unguarded state. `deliver-trainee-settings.py`
therefore delivers both files in one commit, and reports `would-patch` for a
repo that has the settings but not the shim.

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
(it restores **both** files after rendering the diff — the settings it patched
and the shim it staged). The repo list is explicit, not an org wildcard, so
`home-training-template` and future repos are never rewritten without a
decision. Idempotent: a repo that already has both halves reports `ok`; a repo
with patched settings but no `run-hook.sh` is **not** done and still reports
`would-patch`.

Only hook command strings change; `env`, matchers, hook ordering, the event set
and the `timeout` siblings are all left alone (pinned by
`tests/proj011/test_deliver_trainee_settings.py`).

## Adding another structural settings.json invariant

1. Put the canonical value in `scaffold.sh`'s `role_settings_json` (new repos).
2. Add a patch + check function to `tools/wire-update-tooling.py` (live repos),
   and call it from `wire()` / `check()` for the roles it applies to.
3. Pin the two together with a scaffold-lockstep test — the constant in the
   patcher must equal what `scaffold.sh` writes, or the fleet ships two variants.
   Make the assertion **count-based over the whole file**, not an assertion about
   the one command you happen to be adding: T-125 shipped a guard that passed its
   own test on 1 of 11 commands precisely because the test measured a single
   command's behaviour and never asked how many commands needed it.
4. If the invariant needs a FILE as well as a setting (as the shim does), deliver
   both in the same commit and make the "already done" check require both halves.
5. Deliver to already-live repos with `tools/deliver-trainee-settings.py`
   (trainee repos) or a `TOOLING_UPDATE` dispatch (agent home repos, where the
   sync does run).
