"""finalise_ledger.py — durable per-session SessionEnd-finalise progress marker
(PROJ-039/T-030, CC-329).

The SessionEnd finalise is a multi-step sequence (response → rollup → telemetry
push → CST prune → session-finalise → brief-result PR). On long sessions it has
been **non-deterministically truncated** — the hook is killed by the SessionEnd
timeout, or dies mid-sequence — leaving the substrate partially written with no
record of *which* step it reached (the T-028 / C2b data points).

This module is that record. The finalise hook:
  * ``begin(session_id, transcript_path)`` — marks the session in-progress and
    persists the transcript path so a later run can recompute the rollup;
  * ``record_step(...)`` after each step (``done`` / ``skipped`` / ``failed``);
  * ``complete(session_id)`` once it reaches the end.

A session whose entry is present but ``complete`` is false is a **detectable
partial**. The SessionStart guard (`session-end-finalise.py --guard`) scans for
those and re-runs the finalise — which is safe because every step is idempotent
or ledger-gated (notably ``brief_pr``, the one non-idempotent step, is skipped
when already ``done``).

Storage: ``<logs>/finalise-state.log`` — a single JSON object keyed by
session_id. The ``.log`` suffix rides the home repo's existing ``*.log``
gitignore (runtime-only, never committed). Resolved via the same precedence as
``runtime_log`` ($PODZONE_LOG_DIR → $CLAUDE_PROJECT_DIR/logs → cwd/logs).

Best-effort throughout: every function swallows its own errors and returns a
safe default. A ledger failure must never break the finalise it is recording.
Stdlib-only (no ``requests``; no ``lib/`` imports — a closure leaf).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import runtime_log

LEDGER_FILE = "finalise-state.log"

# The steps the finalise sequence records, in execution order. ``brief_pr`` is
# the only non-idempotent step — the guard relies on its ``done`` mark to avoid
# raising a duplicate PR on re-run.
STEPS = ("response", "rollup", "telemetry_push", "cst_prune",
         "session_finalise", "brief_pr", "transcript_ingest")

# The SessionStart guard re-runs an unfinalised (truncated) session's finalise.
# A finalise that truncates *every* attempt (e.g. it deterministically exceeds the
# timeout) would otherwise be re-run on every session-start forever. Cap the
# recovery attempts so a permanently-failing finalise is flagged-and-dropped, not
# retried indefinitely. PROJ-039/T-030.
MAX_FINALISE_ATTEMPTS = 5


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ledger_path() -> Path:
    override = os.environ.get("PODZONE_LOG_DIR")
    if override:
        base = Path(override)
    else:
        project = os.environ.get("CLAUDE_PROJECT_DIR")
        if project:
            base = Path(project) / "logs"
        else:
            # Last resort only (both envs unset). The ledger is load-bearing for
            # SessionStart recovery, so never let it land inside a `.workspace/`
            # subrepo the shell happened to end in (T-054) — recovery would then
            # look in the wrong clone's logs/.
            base = runtime_log.resolve_log_dir()
    return base / LEDGER_FILE


def load() -> dict:
    """Return the whole ledger dict (``{}`` on any error or absence)."""
    try:
        p = ledger_path()
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _save(ledger: dict) -> None:
    try:
        p = ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        # Atomic-ish: write to a temp sibling then replace.
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


def begin(session_id: str, transcript_path: str = "", cwd: str = "") -> None:
    """Record the start of a finalise (or top-up of a re-run). Sets complete=False
    and increments the attempt counter (the guard caps retries with it).

    Persists ``transcript_path`` (so a later re-run can recompute the rollup) and
    ``cwd`` (so the SessionStart guard re-runs the finalise against the *originating*
    home repo — the repo whose ``results/`` the step-7 PR must target, PROJ-039/T-035)."""
    if not session_id:
        return
    try:
        ledger = load()
        entry = ledger.get(session_id) or {}
        entry.setdefault("started_ts", _utc())
        entry["updated_ts"] = _utc()
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        # Keep a non-empty transcript path; a re-run with "" must not erase it.
        if transcript_path or not entry.get("transcript_path"):
            entry["transcript_path"] = transcript_path or entry.get("transcript_path", "")
        # Same non-erasing discipline for cwd.
        if cwd or not entry.get("cwd"):
            entry["cwd"] = cwd or entry.get("cwd", "")
        entry.setdefault("steps", {})
        entry["complete"] = False
        ledger[session_id] = entry
        _save(ledger)
    except Exception:
        pass


def record_step(session_id: str, step: str, status: str) -> None:
    """Record a step outcome. ``status`` is ``done`` | ``skipped`` | ``failed``."""
    if not session_id:
        return
    try:
        ledger = load()
        entry = ledger.get(session_id) or {"steps": {}}
        entry.setdefault("steps", {})
        entry["steps"][step] = status
        entry["updated_ts"] = _utc()
        ledger[session_id] = entry
        _save(ledger)
    except Exception:
        pass


def step_status(session_id: str, step: str) -> str | None:
    """Return the recorded status for a step (``None`` if unknown)."""
    try:
        return load().get(session_id, {}).get("steps", {}).get(step)
    except Exception:
        return None


def steps(session_id: str) -> dict:
    """Return the recorded step→status map for a session (``{}`` if unknown)."""
    try:
        return dict(load().get(session_id, {}).get("steps", {}))
    except Exception:
        return {}


def attempts(session_id: str) -> int:
    """Return how many finalise passes have begun for this session."""
    try:
        return int(load().get(session_id, {}).get("attempts", 0))
    except Exception:
        return 0


def complete(session_id: str) -> None:
    """Mark the session's finalise as fully reached (no partial)."""
    if not session_id:
        return
    try:
        ledger = load()
        entry = ledger.get(session_id) or {"steps": {}}
        entry["complete"] = True
        entry["updated_ts"] = _utc()
        ledger[session_id] = entry
        _save(ledger)
    except Exception:
        pass


def is_complete(session_id: str) -> bool:
    try:
        return bool(load().get(session_id, {}).get("complete"))
    except Exception:
        return False


def unfinalised() -> list[tuple[str, dict]]:
    """Return ``[(session_id, entry), ...]`` for entries that began but never
    reached ``complete`` — the detectable partials the guard recovers."""
    try:
        return [
            (sid, entry)
            for sid, entry in load().items()
            if isinstance(entry, dict) and not entry.get("complete")
        ]
    except Exception:
        return []
