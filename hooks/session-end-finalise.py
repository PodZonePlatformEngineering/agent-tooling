#!/usr/bin/env python3
"""
session-end-finalise.py — session-end Qdrant write path (PROJ-039 § 2.4).

Ordered contract realised here — **order is load-bearing** (§ 2.4):
  1. Upsert `response = {text, status_transition, event_refs, end_ts}` via
     set_payload, then patch the `response` named vector via update-vectors.
  2. Attach `event_refs` linking measurement points (R-012).
  3. Compute `rollup = {tool_usage, cost_tokens}` from the session JSONL and
     attach via set_payload (R-013/R-014; reconciles with a fresh re-scrape, DT-006).
  4. Commit + push the JSONL to `agent-telemetry.git` FIRST (R-015, § 2.5).
  5. ONLY if the push landed, delete the raw PreToolUse/PostToolUse points for
     this session from CST (R-013, § 2.4 step 5). If the push failed, skip the
     delete and warn — deletion safety depends on the backstop existing (C-006).
  6. session-finalise (§ 1.4): read the session point and apply the 4 per-session
     consolidation steps (apply to tasklist + update STATUS).
  7. Session result + PR: author results/session-{date}-{slug}-{sid}.md from the
     session point and raise a PR that lands it on the repo's `main`. The {sid}
     suffix keys result idempotency on session_id (T-039), so a re-set-up session on
     the same date+slug authors its own result instead of skipping on the prior one.

Step 6/7 ownership (the load-bearing T-035 decision — PROJ-029/T-024: every fleet
agent is now migrated, so the legacy non-migrated apex path this used to describe
has been removed):
  * **Migrated home repo** (resolved home-repo basename `home-*`, T-054) — hooks-only by design (there is
    NO `/session-end` skill). Step 6 (apex tasklist/STATUS) is DEFERRED to Hermes
    `/consolidate-tasks` (driven by the session point in Qdrant); the hook must
    never branch/commit in the apex clone. Step 7 is OWNED HERE: the hook authors
    the home-repo result + PR on a branch off home `main`, decoupled from any work
    PR (`lib.session_finalise.author_home_result`). PROJ-039/T-035.
  * **Trainee** — Step 6 is skipped (no apex tasklist/STATUS); Step 7 authors the
    trainee session PR (R-3).

Robustness (PROJ-039/T-030, CC-329 — load-bearing for T-035: the home-repo result
now rides ENTIRELY on this hook completing):
  * Every step is recorded in a durable **finalise ledger** (`finalise_ledger`)
    as done/skipped/failed (step 7 records its disposition done/exists/
    deferred-cancelled), and the session is marked `complete` only once the
    sequence reaches the end. A killed/timed-out finalise therefore leaves a
    *detectable partial* (entry present, complete=false).
  * Re-running is a **safe no-op / top-up**: response/rollup/CST steps are
    idempotent; the one non-idempotent step (`brief_pr`/result) is skipped when the
    ledger already records it `done`/`exists`, and the authoring path itself
    re-checks the base branch + open PRs, so a re-run never duplicates the result.
  * Result-authoring/PR runs LAST (after the telemetry push) so a timeout/cancel
    never costs telemetry — the accepted residual gap is that a hook cancelled
    *before* step 7 leaves the result un-authored; it is then re-derivable at the
    next session-start guard or by Hermes from the substrate `response`.
  * `--guard` mode (invoked from session-start.sh) scans the ledger for partials
    from prior sessions and re-runs the finalise for each — against the originating
    repo (persisted `cwd`) — the SessionStart recovery the brief calls for. It caps
    retries (finalise_ledger.MAX_FINALISE_ATTEMPTS) and flags every partial via
    libraries.log.
  * Which step a finalise reached is also written to `logs/libraries.log` via
    `runtime_log` (T-029) for post-hoc diagnostics.

Reliability (terminal-launch requirement): migrated sessions launch standalone-
terminal (`claude --session-id`, T-028) where SessionEnd fires reliably; the
"sidebar SessionEnd doesn't fire" caveat does NOT bite migrated sessions. They MUST
be terminal-launched (not the sidebar) for the finalise — and hence the result +
PR — to fire. The `/launch-session` migrated flow already emits that command.

Best-effort: logs and exits 0 on any failure (a session-end hook must not break
teardown). Reads stdin JSON: ``session_id``, ``cwd``, ``transcript_path``.

The response.text is derived from the transcript's final assistant turn; the home-
repo result's structured sections (Completed / Started / Blockers / Decisions /
Questions for Martin) are extracted from it, so the agent's final `/exit` turn
should carry those headers for a rich result.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

COMPONENT = "session-end-finalise"


def _log(message: str, *, session_id: str | None = None, level: str = "INFO") -> None:
    """Diagnostic to stderr (always) + logs/libraries.log (best-effort, T-029)."""
    print(f"[{COMPONENT}] {message}", file=sys.stderr)
    try:
        from lib import runtime_log
        runtime_log.log_library(COMPONENT, message, session_id=session_id, level=level)
    except Exception:
        pass


def _last_assistant_text(transcript_path: str) -> str:
    """Return the last assistant turn's text (best-effort; '' on any error)."""
    if not transcript_path or not Path(transcript_path).exists():
        return ""
    last = ""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "assistant":
                    continue
                # T-047: skip subagent (sidechain) turns. A headless `claude -p`
                # session that spawns Agent-tool subagents interleaves their
                # assistant turns into the same transcript; the LAST assistant entry
                # can be a subagent's closing turn, not the session's own final
                # response. Only the main-thread turns are the session response.
                if entry.get("isSidechain"):
                    continue
                message = entry.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                parts: list[str] = []
                if isinstance(content, str):
                    parts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            t = block.get("text")
                            if isinstance(t, str):
                                parts.append(t)
                turn = "\n".join(parts).strip()
                if turn:
                    last = turn
    except Exception:
        return last
    return last


# A brief-first session (PROJ-039/T-043) declares its brief COMPLETE in the final
# response by an explicit marker; absent it, the brief stays `in_progress` for the
# next session (many-sessions-per-brief). The marker is deterministic so a headless
# agent's `/exit` turn controls the transition without an operator on the line —
# the brief-first launch prompt instructs the agent to emit it only when the brief
# is fully done. Accepted forms (case-insensitive): a `Brief-Status: complete` line,
# or a standalone `BRIEF-COMPLETE` / `BRIEF COMPLETE` token.
_BRIEF_COMPLETE_RE = re.compile(
    r"(?im)^\s*(?:brief[-_ ]?status\s*:\s*complete\b|brief[-_ ]complete\b)\s*$"
)


def _declares_brief_complete(text: str) -> bool:
    """True if the response text carries an explicit brief-complete declaration."""
    return bool(text) and bool(_BRIEF_COMPLETE_RE.search(text))


def _is_migrated(home_repo: str) -> bool:
    """A migrated home-repo session runs under a ``home-*`` repo (the same selector
    the session-end skill uses). For these, steps 6-7 (apex tasklist/STATUS apply +
    brief-result PR) are owned elsewhere — see the gate below — so the finalise hook
    must NOT mutate the apex clone. Takes the already-resolved home repo dir
    (:func:`session_guard.resolve_home_repo`, T-054) — never a bare wandered cwd."""
    return os.path.basename((home_repo or "").rstrip("/")).startswith("home-")


def _is_trainee() -> bool:
    """A trainee-runtime session (PROJ-011/T-021 R-3). Selected by an explicit
    ``TRAINEE_RUNTIME=1`` env flag set ONLY in the training-template ``settings.json``
    (and its clones) — NOT by a repo-name pattern: a real trainee clone is
    ``podzone-training-<handle>`` (not ``home-*``), while the training-team AGENTS'
    own repos (``home-training-athena``/``hestia``) are ordinary migrated home repos
    that must keep the migrated-result path. The flag is the unambiguous signal.

    For a trainee, step 7 is the trainee session PR (commit the working tree to the
    session branch + push + PR), and step 6 (apex tasklist/STATUS) is skipped."""
    return os.environ.get("TRAINEE_RUNTIME", "").strip() == "1"


def _copy_session_log_for_trainee(transcript_path: str, repo_dir: str,
                                  session_id: str) -> bool:
    """R-14: a trainee repo does not push to a fleet telemetry remote — instead the
    session log is copied into the repo's ``logs/`` directory so it rides the R-3
    session PR (consistent with R-6 / T-048 committed logs). Best-effort; never raises.
    Returns True if a log file was written."""
    import shutil
    if not (transcript_path and repo_dir and os.path.isfile(transcript_path)):
        return False
    sid8 = (session_id or "session")[:8]
    logs_dir = os.path.join(repo_dir, "logs")
    try:
        os.makedirs(logs_dir, exist_ok=True)
        shutil.copyfile(transcript_path, os.path.join(logs_dir, f"session-{sid8}.jsonl"))
        return True
    except Exception:
        return False


def finalise_session(session_id: str, transcript_path: str, home_repo: str = "") -> int:
    """Run the ordered finalise for one session. Idempotent + re-runnable.

    ``home_repo`` is the **already-resolved authoritative home repo** for this
    session (:func:`session_guard.resolve_home_repo`, PROJ-039/T-054) — never a bare
    wandered cwd and never a ``.workspace/*`` subrepo. Both callers pass a clean
    value: :func:`main` resolves it from ``$CLAUDE_PROJECT_DIR``-first, and
    :func:`run_guard` passes the ledger's persisted (already-resolved) cwd. Every
    downstream repo op (migrated result PR, return-to-main, lock release, ledger cwd)
    binds to this single value, so the finalise mutates ONLY the agent's own home repo
    regardless of where the shell ended.

    Records each step in the finalise ledger and marks the session complete on
    reaching the end. Never raises — best-effort throughout.
    """
    # Sid-key the runtime logs for everything this finalise touches (PROJ-039/T-048):
    # lib call-sites that don't thread a session_id fall back to this env, so their
    # lines land in this session's logs/libraries-{sid8}.log. Process-local; the guard
    # path re-sets it per recovered sid.
    if session_id:
        os.environ["PODZONE_SESSION_ID"] = session_id

    # Defensive re-strip: a legacy partial recovered by the guard may carry a
    # pre-T-054 ledger cwd that still points inside a `.workspace/` subrepo.
    try:
        from lib import session_guard as _sg_resolve
        home_repo = _sg_resolve.strip_workspace_subrepo(home_repo)
    except Exception:
        pass

    try:
        from lib import finalise_ledger
    except Exception as exc:
        _log(f"finalise_ledger import failed: {exc}", session_id=session_id, level="WARN")
        finalise_ledger = None  # type: ignore

    def _step(name: str, status: str) -> None:
        if finalise_ledger is not None:
            finalise_ledger.record_step(session_id, name, status)

    # T-100 double-fire no-op: a manual `/session-end` (sidebar wrapper) followed by
    # the harness SessionEnd at window close re-delivers the same session end. Skip
    # ONLY when the prior finalise completed AND the transcript hasn't grown since —
    # an F14 re-armed continuation grows the transcript and must re-run (idempotent
    # steps bank the new work).
    if finalise_ledger is not None and finalise_ledger.unchanged_since_complete(session_id):
        _log(
            "finalise no-op: sid already complete and transcript unchanged "
            "(duplicate SessionEnd fire)",
            session_id=session_id,
        )
        return 0

    if finalise_ledger is not None:
        # Persist the RESOLVED home repo as the ledger cwd (T-054): the SessionStart
        # guard re-runs a partial against this repo, so it must be the home repo — not
        # the wandered `.workspace/*` cwd the shell happened to end in.
        finalise_ledger.begin(session_id, transcript_path, home_repo)

    try:
        from lib import session_substrate
    except Exception as exc:
        _log(f"lib import failed: {exc}", session_id=session_id, level="ERROR")
        return 0

    # 1. Response upsert (set_payload) — payload-only (PROJ-041/T-002): no
    #    vector patch on the hook path; the PROJ-042 enrichment job embeds in
    #    retrospect. The step is `done` on the payload write alone.
    response_text = _last_assistant_text(transcript_path) or f"session {session_id} ended"
    response_text = response_text[:20000]  # generous cap; payload has no hard limit
    try:
        session_substrate.upsert_response(
            session_id,
            text=response_text,
            status_transition=None,  # MVP: enriched post-MVP from session-finalise
            event_refs=[],
        )
        _log("response upserted", session_id=session_id)
        _step("response", "done")
    except Exception as exc:
        _log(f"response upsert skipped: {exc}", session_id=session_id, level="WARN")
        _step("response", "failed")

    # 2. Brief-first completion stamp (PROJ-039/T-043). If this session was stood
    #    up from a first-class `briefs` point (the session point carries a
    #    `brief_id`), stamp the brief complete ONLY when the result declares it —
    #    else leave it `in_progress` for the next session (many-sessions-per-brief).
    #    Best-effort + idempotent (complete_brief is a set_payload no-op if re-run).
    brief_id: str | None = None  # hoisted: the return-to-main lock release keys on it (T-054)
    try:
        point = session_substrate.get_session_point(session_id)
        brief_id = (point or {}).get("brief_id")
        if not brief_id:
            _log("brief-status skipped: session not brief-first (no brief_id)",
                 session_id=session_id)
            _step("brief_status", "skipped")
        elif _declares_brief_complete(response_text):
            from lib import brief_substrate
            brief_substrate.complete_brief(brief_id)
            _log(f"brief {brief_id} stamped complete (result declared it)",
                 session_id=session_id)
            _step("brief_status", "complete")
        else:
            _log(f"brief {brief_id} left in_progress (result did not declare complete)",
                 session_id=session_id)
            _step("brief_status", "in_progress")
    except Exception as exc:
        _log(f"brief-status skipped: {exc}", session_id=session_id, level="WARN")
        _step("brief_status", "failed")

    # 3. Rollups (tool_usage + cost_tokens) from the JSONL — reconciles (DT-006).
    if transcript_path:
        try:
            from lib import runtime_log as _rl

            rollup = session_substrate.compute_rollup(transcript_path)
            session_substrate.attach_rollup(
                session_id, rollup, tooling_version=_rl.tooling_version())
            _log(
                f"rollup attached (tools={len(rollup['tool_usage'])}, "
                f"models={len(rollup['cost_tokens'])})",
                session_id=session_id,
            )
            _step("rollup", "done")
        except Exception as exc:
            _log(f"rollup skipped: {exc}", session_id=session_id, level="WARN")
            _step("rollup", "failed")
    else:
        _log("rollup skipped: no transcript_path", session_id=session_id, level="WARN")
        _step("rollup", "skipped")

    # 4. Commit + push the telemetry JSONL FIRST (R-015) — the backstop that
    #    makes step 5 safe. Push failure blocks deletion (C-006).
    #    Trainee (R-14): there is deliberately NO fleet telemetry remote (won't scale to
    #    every student workstation). Instead copy the session log into the repo's logs/
    #    so it rides the R-3 session PR. The telemetry_repo push + CST-prune dance is
    #    skipped entirely (nothing to push to).
    pushed = False
    if _is_trainee():
        wrote = _copy_session_log_for_trainee(transcript_path, home_repo, session_id)
        _log(f"trainee session log {'copied to logs/' if wrote else 'copy skipped'} "
             "(R-14 — no telemetry remote)", session_id=session_id)
        _step("telemetry_push", "done" if wrote else "skipped")
        _step("cst_prune", "skipped")
    else:
      try:
        from lib import telemetry_repo
        from datetime import datetime, timezone

        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Lazily bootstrap the backstop so EXISTING home repos self-heal (T-032):
        # init the repo if absent, lay down the agent-session scope .gitignore, and
        # wire origin from PODZONE_TELEMETRY_REMOTE. Idempotent + no-op once bootstrapped.
        remote = telemetry_repo.resolve_remote()
        ens = telemetry_repo.ensure_repo(remote=remote)
        if not remote:
            _log("telemetry: no remote configured (PODZONE_TELEMETRY_REMOTE unset) — "
                 "commit local-only; push will not land, CST prune stays gated.",
                 session_id=session_id, level="WARN")
        elif ens.get("initialised"):
            _log(f"telemetry: bootstrapped repo at {ens['repo_dir']} (origin={remote})",
                 session_id=session_id)
        # PROJ-039/T-093 item 4: durability for this session's sid-keyed home-repo
        # logs moved here (best-effort, alongside the transcript, same push) now
        # that the home-repo result paths no longer force-add them into git.
        copied = telemetry_repo.copy_session_logs(home_repo, transcript_path, session_id)
        if copied:
            _log(f"telemetry: copied {len(copied)} session log(s) alongside transcript",
                 session_id=session_id)
        res = telemetry_repo.commit_and_push(session_id, date=date)
        pushed = res.get("pushed", False)
        _log(
            f"telemetry: committed={res.get('committed')} pushed={pushed} "
            f"({res.get('reason') or 'ok'})",
            session_id=session_id,
        )
        _step("telemetry_push", "done" if pushed else "failed")
      except Exception as exc:
        _log(f"telemetry push skipped: {exc}", session_id=session_id, level="WARN")
        _step("telemetry_push", "failed")

    # 5. Delete raw PreToolUse/PostToolUse from CST — ONLY if the push landed.
    if _is_trainee():
        pass  # R-14: no telemetry push/CST-prune for trainee — handled in step 4 above.
    elif pushed:
        try:
            from lib import cst_cleanup

            res = cst_cleanup.delete_raw_tool_events(session_id)
            _log(
                f"CST raw events deleted (was {res['deleted_before_count']})",
                session_id=session_id,
            )
            _step("cst_prune", "done")
        except Exception as exc:
            _log(f"CST delete skipped: {exc}", session_id=session_id, level="WARN")
            _step("cst_prune", "failed")
    else:
        _log(
            "CST raw events RETAINED — telemetry push did not land; deletion "
            "gated on the backstop (C-006).",
            session_id=session_id, level="WARN",
        )
        _step("cst_prune", "skipped")

    migrated = _is_migrated(home_repo)
    trainee = _is_trainee()

    # Steps 6-7 split by repo kind. On a MIGRATED home repo (hooks-only, no
    # `/session-end` skill):
    #   * Step 6 (apex tasklist/STATUS) stays DEFERRED to Hermes /consolidate-tasks
    #     (driven by the session point in Qdrant). The hook MUST NOT branch/commit in
    #     the resident apex clone — that would dirty it / move it off main, exactly
    #     what the launch-session + consolidate-tasks apex-on-main guards forbid.
    #   * Step 7 is OWNED HERE: the hook authors the home-repo result + PR off home
    #     `main`, decoupled from any work PR (PROJ-039/T-035). This replaces the old
    #     deferral to a `/session-end` skill that does not exist in a migrated repo.
    # ``home_repo`` is the resolved authoritative home repo (T-054) — every step
    # below binds to it. PROJ-029/T-024: the non-migrated apex path (env
    # PODZONETEAM_REPO) was removed once every fleet agent reached `migrated` status
    # (`podzoneTeam/planning/projects/PROJ-032-agent-home-repos/migrated-agents.md`);
    # the `else` branches below are a defensive fallback, not an expected path.

    # 6. session-finalise (§ 1.4): apply the 4 per-session consolidation steps
    #    from the session point (tasklist + STATUS). Idempotent. (Apex model only.)
    if trainee:
        _log("session-finalise skipped (trainee): no apex tasklist/STATUS — the "
             "trainee session PR is the artefact (R-3)", session_id=session_id)
        _step("session_finalise", "skipped")
    elif migrated:
        _log("session-finalise deferred (migrated): tasklist/STATUS -> Hermes "
             "/consolidate-tasks (from session point); apex clone untouched",
             session_id=session_id)
        _step("session_finalise", "skipped")
    else:
        _log("session-finalise skipped: not a migrated home repo or trainee "
             "session", session_id=session_id, level="WARN")
        _step("session_finalise", "skipped")

    # 7. Session result + PR. NON-IDEMPOTENT raise — guard against a duplicate on
    #    re-run via the ledger (`done`/`exists`) AND, defensively, via the base-branch
    #    + open-PR checks inside the authoring path. Runs LAST so a timeout/cancel
    #    never costs telemetry.
    prior = (
        finalise_ledger.step_status(session_id, "brief_pr")
        if finalise_ledger is not None else None
    )
    if prior in ("done", "exists"):
        _log(f"session result skipped: already '{prior}' (ledger) — no duplicate",
             session_id=session_id)
    elif trainee:
        # R-3: the trainee session PR — commit the working tree to the session
        # branch, push, and open the PR to main (body = substrate summary + review
        # checklist). The trainee runs no git. Auth degrades gracefully (commit +
        # push only if gh is unavailable). Idempotent (skips when a PR already
        # carries the branch). The return-to-main main-guard below then ff's main +
        # deletes the pushed branch, leaving the clone on a clean main with an open PR.
        if not home_repo:
            _log("trainee session PR skipped: no repo dir (cwd / CLAUDE_PROJECT_DIR unset)",
                 session_id=session_id, level="WARN")
            _step("brief_pr", "deferred-cancelled")
        else:
            try:
                from lib import session_finalise as _sf, session_substrate as _ss
                from datetime import datetime, timezone as _tz

                point = _ss.get_session_point(session_id) or {}
                brief_id = point.get("brief_id")
                date = datetime.now(_tz.utc).strftime("%Y-%m-%d")
                res = _sf.author_trainee_session_pr(
                    point, session_id=session_id, repo_dir=home_repo,
                    brief_id=brief_id, date=date,
                    summary_override=response_text, raise_pr=True,
                )
                _log(
                    f"trainee session PR [{res['disposition']}]: ok={res['ok']} "
                    f"branch={res['branch']} pr_url={res['pr_url'] or '(none)'} "
                    f"committed={res['committed']} {res.get('reason') or ''}",
                    session_id=session_id,
                )
                _step("brief_pr", res["disposition"])
            except Exception as exc:
                _log(f"trainee session PR deferred-cancelled: {exc}",
                     session_id=session_id, level="WARN")
                _step("brief_pr", "deferred-cancelled")
    elif migrated:
        # T-035: the hook owns the home-repo result. Idempotent (re-checks base +
        # open PRs / origin main, per mode). PROJ-039/T-084: repos flagged
        # `lifecycle_mode: trunk` skip the branch+PR ceremony entirely — the result
        # commit lands straight on `main` (commit_and_push_trunk); every other repo
        # (default, `lifecycle_mode` absent or `branch`) keeps the existing
        # decoupled-branch + PR path unchanged.
        if not home_repo:
            _log("session result skipped (migrated): no home repo dir (cwd / "
                 "CLAUDE_PROJECT_DIR unset)", session_id=session_id, level="WARN")
            _step("brief_pr", "deferred-cancelled")
        else:
            try:
                from lib import session_finalise as _sf, session_substrate as _ss
                from lib import session_guard as _sg
                from lib import lifecycle_mode as _lm
                from datetime import datetime, timezone as _tz

                point = _ss.get_session_point(session_id)
                if point:
                    date = datetime.now(_tz.utc).strftime("%Y-%m-%d")
                    if _lm.is_trunk_mode(home_repo):
                        res = _sf.author_home_result_trunk(
                            point, session_id=session_id, repo_dir=home_repo, date=date,
                        )
                        _log(
                            f"home result (trunk) [{res['disposition']}]: "
                            f"ok={res['ok']} {res.get('reason') or ''}",
                            session_id=session_id,
                            level="ERROR" if res["disposition"] == "halted" else "INFO",
                        )
                        if res["disposition"] == "halted":
                            _log(
                                "HALT: trunk-mode result commit could not be pushed "
                                f"to {home_repo} main after a rebase + retry — the "
                                "commit is LOCAL ONLY (never force-pushed). Resolve "
                                f"by hand: git -C {home_repo} push (or "
                                f"git -C {home_repo} reset --soft HEAD~1 to undo).",
                                session_id=session_id, level="ERROR",
                            )
                    else:
                        # T-060: capture the live session branch BEFORE the
                        # return-to-main guard runs (below) resets the clone to main
                        # — so a mid-session commit on this branch (e.g. a resident
                        # self-sync) can be forked into the result PR instead of
                        # being stranded when the branch is later reaped.
                        session_branch = _sg.current_branch(home_repo)
                        res = _sf.author_home_result(
                            point, session_id=session_id, repo_dir=home_repo,
                            date=date, raise_pr=True, session_branch=session_branch,
                        )
                        _log(
                            f"home result [{res['disposition']}]: ok={res['ok']} "
                            f"branch={res['branch']} pr_url={res['pr_url'] or '(none)'} "
                            f"{res.get('reason') or ''}",
                            session_id=session_id,
                        )
                    _step("brief_pr", res["disposition"])
                else:
                    _log("session result skipped (migrated): session point not found",
                         session_id=session_id, level="WARN")
                    _step("brief_pr", "deferred-cancelled")
            except Exception as exc:
                _log(f"home result deferred-cancelled: {exc}",
                     session_id=session_id, level="WARN")
                _step("brief_pr", "deferred-cancelled")
    else:
        _log("brief-result PR skipped: not a migrated home repo or trainee "
             "session", session_id=session_id, level="WARN")
        _step("brief_pr", "skipped")

    # Session-end main-guard (PROJ-039/T-045): in serial simple-repo mode the session
    # ran directly in the primary clone on a session branch — return it to a ff'd main
    # and delete the now-pushed session branch, and release the one-session lock. This
    # is the load-bearing substitute for worktree isolation; a crash before here leaves
    # the branch in place and the launch-time preflight guard catches it next time.
    # Self-guarding + best-effort: a no-op on a legacy linked worktree (cwd under
    # ~/sessions/) and on a clone already on main, so it is safe during the migration
    # window while some sessions still run in worktrees.
    # Binds to the resolved home_repo (T-054): return-to-main + lock release always
    # act on the agent's own home repo, never the wandered cwd's `.workspace/*` subrepo.
    # T-075 F13: the guard now covers EVERY clone this session locked, not just the
    # home repo — the launch-recorded lock set (~/.claude/session-locks/*.lock keyed
    # on this session's sid/brief_id) enumerates the task-repo clones that were
    # previously stranded on their session branches (three consecutive manual
    # Hermes sweeps of ~/workspace/agent-tooling: t069/t070/t071).
    # PROJ-043/T-003: plus the agent-scoped workspace scan — a clone branched
    # MID-SESSION with a bare `git checkout -b` has no lock and was invisible to
    # the F13 set (t002 live hit, sid e29fed8b: ~/workspace/agent-tooling stranded
    # on its merged session branch; Hermes's next pull failed on the deleted ref).
    if home_repo:
        try:
            from lib import session_guard
            results = session_guard.end_guard_all_clones(
                home_repo, session_id=session_id, brief_id=brief_id or "")
            task_failed = False
            for entry in results:
                res = entry["return"]
                # An unpushed branch is deliberately kept — but that is a loud
                # condition (possible lost work), not a routine INFO line.
                kept_unpushed = res["disposition"] == "returned-branch-kept-unpushed"
                _log(f"session-end main-guard [{entry['kind']}"
                     f"/{entry.get('via', '')}] {entry['repo']}: "
                     f"{res['disposition']} — {res['reason']}; "
                     f"lock released: {entry['lock_released']}",
                     session_id=session_id,
                     level="INFO" if res["ok"] and not kept_unpushed else "WARN")
                if entry["kind"] == "home":
                    _step("return_to_main", res["disposition"] if res["ok"] else "failed")
                elif not res["ok"]:
                    task_failed = True
            task_count = sum(1 for e in results if e["kind"] == "task")
            _step("return_task_clones",
                  "skipped" if task_count == 0 else ("failed" if task_failed else "done"))
        except Exception as exc:  # never break teardown
            _log(f"session-end main-guard skipped: {exc}",
                 session_id=session_id, level="WARN")
            _step("return_to_main", "failed")

    # 8. Archivist transcript ingest (PROJ-039/T-053). FOLDED INTO the finalise as the
    #    LAST step so the archivist runs a SINGLE SessionEnd hook, not two. Two heavy
    #    SessionEnd hooks (this finalise + ingest-transcript.py, which at the time did
    #    N sequential embeds + a Telegram notify — hooks no longer embed at all per
    #    PROJ-041/T-002) overran the CLI's teardown budget and the
    #    finalise was "Hook cancelled" mid-run — BEFORE telemetry/result/return-to-main
    #    (Thoth T-022 sid 9035370d, T-023 sid 38ae63e3; Hephaestus, single SessionEnd
    #    hook, is 4/4 clean). Ordered LAST + ledger-tracked + time-bounded: every
    #    load-bearing step completes first, and a cancel during the slow ingest leaves a
    #    recoverable partial the SessionStart guard tops up (ingest upserts are uuid5-
    #    keyed → idempotent). Gated by PODZONE_INGEST_TRANSCRIPT=1 (archivist settings),
    #    so it is a pure no-op for every other role. Best-effort — never breaks teardown.
    if os.environ.get("PODZONE_INGEST_TRANSCRIPT", "").strip() == "1":
        try:
            import subprocess
            # Invoke the WRAPPER (`ingest-transcript.sh`), not `ingest-transcript.py`
            # directly (T-054 review): the wrapper injects PODZONE_QDRANT_APIKEY via
            # `secretctl run` when the key is not already in the env — the exact
            # invocation the pre-fold SessionEnd hook used (`bash ingest-transcript.sh`).
            # Calling the .py directly would drop that secret-injection path and the
            # ingest would silently fail to reach Qdrant on any session where the key
            # is not pre-exported. bash does not consume stdin, so the reconstructed
            # SessionEnd JSON still flows through to the .py.
            ingest_sh = Path(__file__).resolve().parent / "ingest-transcript.sh"
            if ingest_sh.exists():
                stdin_json = json.dumps({
                    "session_id": session_id,
                    "transcript_path": transcript_path,
                    "cwd": home_repo,
                })
                r = subprocess.run(
                    ["bash", str(ingest_sh)], input=stdin_json, text=True,
                    capture_output=True, timeout=180,
                )
                _log(f"transcript ingest rc={r.returncode}: "
                     f"{(r.stdout or r.stderr or '').strip()[:200]}",
                     session_id=session_id)
                _step("transcript_ingest", "done" if r.returncode == 0 else "failed")
            else:
                _log("transcript ingest skipped: ingest-transcript.sh not resident",
                     session_id=session_id, level="WARN")
                _step("transcript_ingest", "skipped")
        except Exception as exc:  # never break teardown (incl. subprocess timeout)
            _log(f"transcript ingest skipped: {exc}", session_id=session_id, level="WARN")
            _step("transcript_ingest", "failed")

    # Reached the end of the sequence — mark complete. The `complete` flag means
    # "the finalise ran to the end" (no truncation), which is the failure mode the
    # SessionStart guard recovers: a killed / timed-out finalise leaves begin()
    # recorded but never reaches complete(). Individual step failures (e.g. a
    # telemetry push with no backstop configured) are recorded per-step for
    # diagnostics but do NOT keep the session partial — re-running would not fix a
    # permanent config gap, and treating every gated step as "partial" would make
    # the guard re-run normal sessions forever. A step that failed transiently is
    # already idempotently re-applied on the next clean finalise if one occurs.
    if finalise_ledger is not None:
        step_map = finalise_ledger.steps(session_id)
        failed = [k for k, v in step_map.items() if v == "failed"]
        if failed:
            _log(f"finalise reached end with failed steps={failed} (recorded for "
                 f"diagnostics; not a truncation)", session_id=session_id, level="WARN")
        finalise_ledger.complete(session_id)
    _log("finalise complete", session_id=session_id)

    # NOTE (PROJ-039/T-093): there is no post-finalise log-tail commit for either
    # mode. `logs/` is fully gitignored (root .gitignore) and nothing force-adds
    # session logs into git anymore — the T-091 trunk tail commit this comment
    # block used to describe is retired along with the machinery it existed to
    # sweep up after (`commit_trunk_log_tail`, `session_guard.stage_session_logs`
    # from the home-repo result paths). A trunk session's result commit is the
    # only commit; nothing written after it can dirty a tracked path. Durable
    # copies of this session's logs ride the telemetry-repo push instead (step 7
    # above, `lib/telemetry_repo`). The trainee path is unaffected — it commits
    # the live tree wholesale and still force-stages its own sid-keyed logs
    # (`session_guard.stage_session_logs`, unchanged).
    return 0


def run_guard() -> int:
    """SessionStart recovery: detect prior sessions whose finalise never reached
    completion (killed / timed-out mid-sequence) and re-run them. Flags each
    partial in libraries.log. Idempotent — runs a top-up, raises no duplicate PR.

    Re-runs against the *originating* repo (the ``cwd`` persisted at begin()), so a
    migrated session's step-7 result PR targets the right home repo even when the
    guard fires from a different session. Caps retries at
    ``finalise_ledger.MAX_FINALISE_ATTEMPTS`` so a finalise that truncates on every
    attempt is flagged-and-dropped, not re-run forever (PROJ-039/T-030).
    """
    try:
        from lib import finalise_ledger
    except Exception as exc:
        _log(f"guard: finalise_ledger import failed: {exc}", level="WARN")
        return 0

    partials = finalise_ledger.unfinalised()
    if not partials:
        return 0

    cap = getattr(finalise_ledger, "MAX_FINALISE_ATTEMPTS", 5)
    for sid, entry in partials:
        steps = entry.get("steps", {})
        attempts = int(entry.get("attempts", 0))
        if attempts >= cap:
            _log(
                f"guard: UNFINALISED session exceeded retry cap "
                f"(attempts={attempts} >= {cap}); flagged-and-dropped, not re-run. "
                f"steps={steps}",
                session_id=sid, level="ERROR",
            )
            continue
        _log(
            f"guard: detected UNFINALISED prior session (attempts={attempts}) — "
            f"steps={steps}; re-running",
            session_id=sid, level="WARN",
        )
        try:
            # Re-run against the ORIGINATING repo (the ledger's persisted, already-
            # resolved home repo) — NOT this guard session's own CLAUDE_PROJECT_DIR,
            # which would hijack the recovery to the wrong clone (T-054). finalise_session
            # re-strips any legacy `.workspace/` tail defensively.
            finalise_session(
                sid, entry.get("transcript_path", ""), entry.get("cwd", "")
            )
        except Exception as exc:
            _log(f"guard: re-finalise raised (swallowed): {exc}",
                 session_id=sid, level="ERROR")
    return 0


def main() -> int:
    if "--guard" in sys.argv[1:]:
        return run_guard()

    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        _log(f"bad stdin: {exc}", level="WARN")
        return 0

    session_id = data.get("session_id")
    transcript_path = data.get("transcript_path", "")
    cwd = data.get("cwd", "")
    if not session_id:
        _log("no session_id — nothing to finalise", level="WARN")
        return 0

    # T-054: resolve the authoritative home repo BEFORE finalising — CLAUDE_PROJECT_DIR
    # (the CLI's fixed launch anchor) first, else the wandered cwd with any `.workspace/`
    # tail stripped. Never the bare `.workspace/*` subrepo the shell may have ended in.
    home_repo = cwd
    try:
        from lib import session_guard
        home_repo = session_guard.resolve_home_repo(cwd)
    except Exception as exc:
        _log(f"home-repo resolve failed, using raw cwd: {exc}",
             session_id=session_id, level="WARN")
    if home_repo != cwd:
        _log(f"home repo resolved to {home_repo!r} (shell cwd was {cwd!r})",
             session_id=session_id)

    return finalise_session(session_id, transcript_path, home_repo)


if __name__ == "__main__":
    raise SystemExit(main())
