#!/usr/bin/env python3
"""
first-prompt-brief.py — UserPromptSubmit brief-id-from-first-prompt (PROJ-011/T-021
R-1, CC-351).

The trainee-runtime evolution of the brief-first launch (PROJ-039/T-043). A normal
migrated session materialises `.workspace` on **SessionStart** from a pinned sid or a
`BRIEF_ID` env var. A trainee has neither — the enrolment email just says *"paste this
line as your first message"*. So on the **first prompt** of a session this hook parses
a brief id out of the prompt and runs the same brief-first materialise path.

Contract (brief R-1):
  * **First prompt only.** The guard is the materialise sentinel: act only when
    `.workspace/.materialise-status.json` is absent or `ok:false` (i.e. SessionStart
    did not already materialise a brief). Once materialised `ok:true`, every later
    prompt is a no-op — so this is naturally first-prompt-scoped and idempotent.
  * **`BRIEF_ID` env takes precedence** (backwards compatible, T-043 C-003). If the
    env is set, SessionStart already handled it; this hook stays out of the way.
  * **Brief id grammar:** either a ``Brief: <id>`` line, or a bare id matching
    ``{team}/{YYYY-MM-DD}-{slug}`` anywhere in the prompt (trainee form
    ``training/{date}-{curriculum-slug}-{trainee}``). The explicit ``Brief:`` line
    wins if both are present.
  * **No brief id found anywhere → leave current behaviour untouched** (no-op, no
    sentinel write). A non-trainee session that reaches here with an unmaterialised
    workspace simply proceeds as before.

Apex-safe: this hook is wired only in the training-template `settings.json`; even if
it were wired fleet-wide it would no-op for any session whose SessionStart already
materialised a brief (the sentinel guard). Always exits 0 — a UserPromptSubmit hook
must not break the turn. Reads stdin JSON: ``session_id``, ``cwd``, ``prompt``.

Tested by tests/proj039/test_first_prompt_brief.py (parse/precedence/guard/no-op).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# A bare brief id: ``{team}/{YYYY-MM-DD}-{slug}``. Team + slug are lowercase
# kebab tokens; the date anchors it so ordinary prose (paths, urls) does not match.
# Mirrors the session_guard session-branch grammar so ids and branches stay aligned.
_BARE_ID_RE = re.compile(
    r"\b([a-z0-9][a-z0-9-]*/\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]+)\b"
)
# An explicit ``Brief: <id>`` line (case-insensitive label). Wins over a bare match.
_BRIEF_LINE_RE = re.compile(
    r"^\s*Brief:\s*([^\s]+)\s*$", re.IGNORECASE | re.MULTILINE
)


def parse_brief_id(prompt: str) -> str | None:
    """Extract a brief id from a prompt, or ``None``.

    Precedence: an explicit ``Brief: <id>`` line first, else the first bare
    ``{team}/{date}-{slug}`` id found in the text. Returns ``None`` if neither is
    present — the caller then leaves current behaviour untouched.
    """
    if not prompt:
        return None
    m = _BRIEF_LINE_RE.search(prompt)
    if m:
        return m.group(1).strip()
    m = _BARE_ID_RE.search(prompt)
    if m:
        return m.group(1).strip()
    return None


def already_materialised(cwd: str, session_id: str) -> bool:
    """True if `.workspace` was already materialised ``ok:true`` **for this session** —
    the first-prompt guard.

    Session-scoped, not just presence-based: the `.workspace` sentinel is gitignored
    and PERSISTS across sessions, so a prior session leaves ``ok:true`` behind. The
    guard must fire on the *first prompt of THIS session*, so we require both the
    success sentinel AND that ``identity.json`` was written under the current
    ``session_id``. A stale sentinel from a previous sid ⇒ not yet materialised here."""
    workspace = Path(cwd) / ".workspace"
    try:
        status = json.loads((workspace / ".materialise-status.json").read_text("utf-8"))
        identity = json.loads((workspace / "identity.json").read_text("utf-8"))
    except Exception:
        return False
    return bool(status.get("ok")) and identity.get("session_id") == session_id


def _emit_context(text: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        }
    }))


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        print(f"[first-prompt-brief] bad stdin: {exc}", file=sys.stderr)
        return 0

    session_id = data.get("session_id", "")
    cwd = data.get("cwd", ".")
    prompt = data.get("prompt", "") or ""

    # BRIEF_ID env wins — SessionStart already materialised via it (C-003).
    if os.environ.get("BRIEF_ID", "").strip():
        return 0

    # First-prompt guard: if SessionStart already materialised a brief ok:true,
    # every later prompt is a no-op. Naturally first-prompt-scoped + idempotent.
    if already_materialised(cwd, session_id):
        return 0

    brief_id = parse_brief_id(prompt)
    if not brief_id:
        # No brief id anywhere → leave current behaviour untouched (R-1).
        return 0

    # Run the brief-first materialise path with the parsed id (same code the
    # SessionStart BRIEF_ID path uses, so `.workspace` is byte-identical).
    try:
        sys.path.insert(0, str(REPO_ROOT / "hooks"))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "session_materialise", str(REPO_ROOT / "hooks" / "session-materialise.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore
        status = mod.materialise_brief_first(session_id, cwd, brief_id)
    except Exception as exc:
        _emit_context(
            f"⛔ First-prompt brief materialise failed for `{brief_id}` "
            f"(reason: {exc}). Resolve and re-send, or set BRIEF_ID."
        )
        return 0

    if status.get("ok"):
        counts = status.get("counts", {})
        _emit_context(
            f"✅ Session materialised from brief `{brief_id}` (parsed from your first "
            f"prompt) — .workspace populated (brief + {counts.get('tasks', 0)} active "
            f"tasks), session point keyed to the runtime sid, sid appended to "
            f"session_ids[]. Authoritative context is .workspace/, not this prompt."
        )
    else:
        _emit_context(
            f"⛔ MATERIALISE FAILED for brief `{brief_id}` — .workspace is empty/stale. "
            f"Do NOT begin work. (reason: {status.get('reason')})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
