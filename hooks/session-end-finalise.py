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
  7. Session result + PR: author results/session-{date}-{slug}.md from the session
     point and raise a PR that lands it on the repo's `main`.

Step 6/7 ownership splits by repo kind (the load-bearing T-035 decision):
  * **Migrated home repo** (cwd basename `home-*`) — hooks-only by design (there is
    NO `/session-end` skill). Step 6 (apex tasklist/STATUS) is DEFERRED to Hermes
    `/consolidate-tasks` (driven by the session point in Qdrant); the hook must
    never branch/commit in the apex clone. Step 7 is OWNED HERE: the hook authors
    the home-repo result + PR on a branch off home `main`, decoupled from any work
    PR (`lib.session_finalise.author_home_result`). PROJ-039/T-035.
  * **Apex model** (non-migrated) — step 6 applies to the apex tasklist/STATUS in
    PODZONEAGENTTEAM_REPO and step 7 raises the brief-result PR there. Best-effort;
    skipped if PODZONEAGENTTEAM_REPO is unset.

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


def _is_migrated(cwd: str) -> bool:
    """A migrated home-repo session runs under a ``home-*`` repo (the same selector
    the session-end skill uses). For these, steps 6-7 (apex tasklist/STATUS apply +
    brief-result PR) are owned elsewhere — see the gate below — so the finalise hook
    must NOT mutate the apex clone. Detect from the explicit cwd, else CLAUDE_PROJECT_DIR."""
    base = os.path.basename((cwd or os.environ.get("CLAUDE_PROJECT_DIR", "")).rstrip("/"))
    return base.startswith("home-")


def finalise_session(session_id: str, transcript_path: str, cwd: str = "") -> int:
    """Run the ordered finalise for one session. Idempotent + re-runnable.

    Records each step in the finalise ledger and marks the session complete on
    reaching the end. Never raises — best-effort throughout.
    """
    try:
        from lib import finalise_ledger
    except Exception as exc:
        _log(f"finalise_ledger import failed: {exc}", session_id=session_id, level="WARN")
        finalise_ledger = None  # type: ignore

    def _step(name: str, status: str) -> None:
        if finalise_ledger is not None:
            finalise_ledger.record_step(session_id, name, status)

    if finalise_ledger is not None:
        finalise_ledger.begin(session_id, transcript_path, cwd)

    try:
        from lib import session_substrate
    except Exception as exc:
        _log(f"lib import failed: {exc}", session_id=session_id, level="ERROR")
        return 0

    # 1. Response upsert (set_payload) + response-vector patch (update-vectors).
    response_text = _last_assistant_text(transcript_path) or f"session {session_id} ended"
    response_text = response_text[:20000]  # generous cap; payload has no hard limit
    try:
        session_substrate.upsert_response(
            session_id,
            text=response_text,
            status_transition=None,  # MVP: enriched post-MVP from session-finalise
            event_refs=[],
            ollama_host=None,
        )
        _log("response upserted + vector patched", session_id=session_id)
        _step("response", "done")
    except Exception as exc:
        _log(f"response upsert skipped: {exc}", session_id=session_id, level="WARN")
        _step("response", "failed")

    # 3. Rollups (tool_usage + cost_tokens) from the JSONL — reconciles (DT-006).
    if transcript_path:
        try:
            rollup = session_substrate.compute_rollup(transcript_path)
            session_substrate.attach_rollup(session_id, rollup)
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
    pushed = False
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
    if pushed:
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

    agent_repo = os.environ.get("PODZONEAGENTTEAM_REPO", "")
    migrated = _is_migrated(cwd)

    # Steps 6-7 split by repo kind. On a MIGRATED home repo (hooks-only, no
    # `/session-end` skill):
    #   * Step 6 (apex tasklist/STATUS) stays DEFERRED to Hermes /consolidate-tasks
    #     (driven by the session point in Qdrant). The hook MUST NOT branch/commit in
    #     the resident apex clone — that would dirty it / move it off main, exactly
    #     what the launch-session + consolidate-tasks apex-on-main guards forbid. We
    #     never reference agent_repo on this path, so the apex clone stays on main,
    #     untouched (acceptance c).
    #   * Step 7 is OWNED HERE: the hook authors the home-repo result + PR off home
    #     `main`, decoupled from any work PR (PROJ-039/T-035). This replaces the old
    #     deferral to a `/session-end` skill that does not exist in a migrated repo.
    # The non-migrated apex path (env PODZONEAGENTTEAM_REPO) is unchanged.
    home_repo = cwd or os.environ.get("CLAUDE_PROJECT_DIR", "")

    # 6. session-finalise (§ 1.4): apply the 4 per-session consolidation steps
    #    from the session point (tasklist + STATUS). Idempotent. (Apex model only.)
    if migrated:
        _log("session-finalise deferred (migrated): tasklist/STATUS -> Hermes "
             "/consolidate-tasks (from session point); apex clone untouched",
             session_id=session_id)
        _step("session_finalise", "skipped")
    elif agent_repo:
        try:
            from pathlib import Path as _Path
            from lib import session_substrate, session_finalise

            point = session_substrate.get_session_point(session_id)
            if point:
                tasklist = _Path(agent_repo) / "planning" / "team-tasklist.md"
                status_md = _Path(agent_repo) / "planning" / "STATUS.md"
                res = session_finalise.apply_from_point(
                    point, tasklist_path=tasklist, status_path=status_md
                )
                _log(
                    f"session-finalise: tasklist_changed={res['tasklist_changed']} "
                    f"status_changed={res['status_changed']} "
                    f"work_item={res['work_item']} new_status={res['new_status']}",
                    session_id=session_id,
                )
                _step("session_finalise", "done")
            else:
                _log("session-finalise skipped: session point not found",
                     session_id=session_id, level="WARN")
                _step("session_finalise", "skipped")
        except Exception as exc:
            _log(f"session-finalise skipped: {exc}", session_id=session_id, level="WARN")
            _step("session_finalise", "failed")
    else:
        _log("session-finalise skipped: PODZONEAGENTTEAM_REPO not set",
             session_id=session_id)
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
    elif migrated:
        # T-035: the hook owns the home-repo result. Author it + PR off home `main`,
        # decoupled from the work PR. Idempotent (re-checks base + open PRs).
        if not home_repo:
            _log("session result skipped (migrated): no home repo dir (cwd / "
                 "CLAUDE_PROJECT_DIR unset)", session_id=session_id, level="WARN")
            _step("brief_pr", "deferred-cancelled")
        else:
            try:
                from lib import session_finalise as _sf, session_substrate as _ss
                from datetime import datetime, timezone as _tz

                point = _ss.get_session_point(session_id)
                if point:
                    date = datetime.now(_tz.utc).strftime("%Y-%m-%d")
                    res = _sf.author_home_result(
                        point, session_id=session_id, repo_dir=home_repo,
                        date=date, raise_pr=True,
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
    elif agent_repo:
        try:
            from lib import session_finalise as _sf, session_substrate as _ss
            from datetime import datetime, timezone as _tz

            point = _ss.get_session_point(session_id)
            if point:
                date = datetime.now(_tz.utc).strftime("%Y-%m-%d")
                result_text = _sf.generate_brief_result(point, date=date)
                pr_res = _sf.commit_brief_result(
                    result_text,
                    session_id=session_id,
                    work_item=point.get("work_item", "unknown"),
                    date=date,
                    repo_dir=agent_repo,
                    raise_pr=True,
                )
                _log(
                    f"brief-result PR: ok={pr_res['ok']} branch={pr_res['branch']} "
                    f"pr_url={pr_res['pr_url'] or '(none)'} {pr_res.get('reason') or ''}",
                    session_id=session_id,
                )
                _step("brief_pr", "done" if pr_res.get("ok") else "failed")
            else:
                _log("brief-result PR skipped: session point not found",
                     session_id=session_id, level="WARN")
                _step("brief_pr", "skipped")
        except Exception as exc:
            _log(f"brief-result PR skipped: {exc}", session_id=session_id, level="WARN")
            _step("brief_pr", "failed")
    else:
        _log("brief-result PR skipped: PODZONEAGENTTEAM_REPO not set",
             session_id=session_id)
        _step("brief_pr", "skipped")

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

    return finalise_session(session_id, transcript_path, cwd)


if __name__ == "__main__":
    raise SystemExit(main())
