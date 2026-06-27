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
  7. Brief-result PR (R-011 / MVP-8): generate results/session-{date}-{slug}.md
     from the session point, commit to the home-team-agent repo, raise a PR.

Steps 6-7 are best-effort and require PODZONE_QDRANT_APIKEY + PODZONEAGENTTEAM_REPO
to be set; they are silently skipped if either is absent.

Robustness (PROJ-039/T-030, CC-329):
  * Every step is recorded in a durable **finalise ledger** (`finalise_ledger`)
    as done/skipped/failed, and the session is marked `complete` only once the
    sequence reaches the end. A killed/timed-out finalise therefore leaves a
    *detectable partial* (entry present, complete=false).
  * Re-running is a **safe no-op / top-up**: response/rollup/CST steps are
    idempotent; the one non-idempotent step (`brief_pr`) is skipped when the
    ledger already records it `done`, so a re-run never raises a duplicate PR.
  * `--guard` mode (invoked from session-start.sh) scans the ledger for partials
    from prior sessions and re-runs the finalise for each — the SessionStart
    recovery the brief calls for. It flags every partial via libraries.log.
  * Which step a finalise reached is also written to `logs/libraries.log` via
    `runtime_log` (T-029) for post-hoc diagnostics.

Best-effort: logs and exits 0 on any failure (a session-end hook must not break
teardown). Reads stdin JSON: ``session_id``, ``cwd``, ``transcript_path``.

The MVP response.text is derived from the transcript's final assistant turn; the
rich session-result authoring is the brief-result PR (PR-B / post-MVP). The key
substrate properties (response present + vector set + brief preserved + rollups
reconcile) hold regardless of how the text is authored.
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


def finalise_session(session_id: str, transcript_path: str) -> int:
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
        finalise_ledger.begin(session_id, transcript_path)

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

    # 6. session-finalise (§ 1.4): apply the 4 per-session consolidation steps
    #    from the session point (tasklist + STATUS). Idempotent.
    if agent_repo:
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

    # 7. Brief-result PR (R-011 / MVP-8): generate results/session-{date}-{slug}.md,
    #    commit to the home-team-agent repo, and raise a PR (best-effort).
    #    NON-IDEMPOTENT — guard against a duplicate PR on re-run via the ledger.
    already_done = (
        finalise_ledger is not None
        and finalise_ledger.step_status(session_id, "brief_pr") == "done"
    )
    if already_done:
        _log("brief-result PR skipped: already done (ledger) — no duplicate PR",
             session_id=session_id)
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
    """
    try:
        from lib import finalise_ledger
    except Exception as exc:
        _log(f"guard: finalise_ledger import failed: {exc}", level="WARN")
        return 0

    partials = finalise_ledger.unfinalised()
    if not partials:
        return 0

    for sid, entry in partials:
        steps = entry.get("steps", {})
        _log(
            f"guard: detected UNFINALISED prior session — steps={steps}; re-running",
            session_id=sid, level="WARN",
        )
        try:
            finalise_session(sid, entry.get("transcript_path", ""))
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
    if not session_id:
        _log("no session_id — nothing to finalise", level="WARN")
        return 0

    return finalise_session(session_id, transcript_path)


if __name__ == "__main__":
    raise SystemExit(main())
