"""classify_exit.py — launch-wrapper exit classification (PROJ-039/T-108, proposal §5).

The flagged main risk of the launch wrapper: distinguishing session-limit vs
API-error vs complete vs other from a `claude -p` run. This is **two orthogonal
axes**, read from two different sources, combined by :func:`classify_exit` into
the four states the run loop (proposal §3.3) switches on.

  * **Process outcome** (axis 1) — why the CLI process ended, read from its own
    raw terminal output (stdout+stderr concatenated). Session-limit wording
    varies (two captured phrasings already, a third is likely) so it is matched
    tolerantly, not by literal string equality. Drives retry/rotate/exit.
  * **Brief state** (axis 2) — the literal ``Brief-Status: complete`` /
    ``Brief-Status: in_progress`` line from the agent's own final response text.
    Exact match only. Independent of axis 1; only consulted once axis 1 reads
    "clean" (no limit/error marker anywhere in the process output).

**Precedence when both could apply** (§5): a captured limit/error string
anywhere in the process output is authoritative for axis 1 regardless of what
axis 2 shows — a limit-stop's "final response" is often empty or a stub
precisely because the limit interrupted it. So axis 1 is checked first, over
the FULL process output; axis 2 is only read from the final response text once
axis 1 comes back clean.

An unrecognised/unknown exit (no markers, no exit-0-plus-complete-line) falls
through to ``OTHER`` — never an infinite retry loop.
"""

from __future__ import annotations

import re
from typing import Optional

SESSION_LIMIT = "session_limit"
API_ERROR = "api_error"
COMPLETE = "complete"
OTHER = "other"

_CLEAN = "clean"

# Tolerant, not literal — two different captured phrasings already (one carries
# a reset time + timezone, the other references /usage-credits + /model); a
# third wording is expected. Anchored on the recurring nouns, not full strings.
_SESSION_LIMIT_RE = re.compile(
    r"session limit|reached your .* limit|usage-credits", re.IGNORECASE
)
_API_ERROR_RE = re.compile(r"^API Error:", re.IGNORECASE | re.MULTILINE)

# Brief-state markers are exact, not fuzzy — the agent is instructed to print
# the literal line.
_BRIEF_COMPLETE_RE = re.compile(r"^Brief-Status: complete$", re.MULTILINE)
_BRIEF_IN_PROGRESS_RE = re.compile(r"^Brief-Status: in_progress$", re.MULTILINE)


def detect_process_outcome(process_output: str) -> str:
    """Axis 1 — why the CLI process ended, read from its own raw output.

    Returns :data:`SESSION_LIMIT`, :data:`API_ERROR`, or ``"clean"`` (no marker
    found — a clean exit or an as-yet-unclassified crash; axis 2 / exit code
    decide the rest in :func:`classify_exit`).
    """
    text = process_output or ""
    if _SESSION_LIMIT_RE.search(text):
        return SESSION_LIMIT
    if _API_ERROR_RE.search(text):
        return API_ERROR
    return _CLEAN


def brief_status(final_response_text: str) -> Optional[str]:
    """Axis 2 — the literal ``Brief-Status:`` line from the agent's final
    response text. Exact match only. Returns ``"complete"``, ``"in_progress"``,
    or ``None`` (line absent, or any other content — both are "not complete"
    for axis-2 purposes)."""
    text = final_response_text or ""
    if _BRIEF_COMPLETE_RE.search(text):
        return "complete"
    if _BRIEF_IN_PROGRESS_RE.search(text):
        return "in_progress"
    return None


def classify_exit(
    process_output: str,
    exit_code: int = 0,
    final_response_text: Optional[str] = None,
) -> str:
    """Combine both axes into the run loop's four states.

    ``process_output`` is the full captured stdout+stderr of the `claude -p`
    invocation — axis 1 is read from it in full. ``final_response_text``, if
    given separately, is the agent's own final response (axis 2 source); when
    omitted, ``process_output`` doubles as both (the common case — `claude -p`
    prints the final response to stdout, so there is usually nothing separate
    to pass).

    Returns one of :data:`SESSION_LIMIT`, :data:`API_ERROR`, :data:`COMPLETE`,
    :data:`OTHER`. A captured limit/error marker anywhere in ``process_output``
    wins outright, regardless of ``exit_code`` or brief state (§5 precedence).
    Only a clean process outcome consults axis 2: ``exit_code == 0`` AND the
    exact ``Brief-Status: complete`` line ⇒ :data:`COMPLETE`; anything else
    (non-zero exit, missing line, ``in_progress``, unrecognised text) ⇒
    :data:`OTHER` — the same bucket the raise-to-lead-and-exit convention
    lands in, and the safe fallback for a truly unclassifiable exit.
    """
    outcome = detect_process_outcome(process_output)
    if outcome == SESSION_LIMIT:
        return SESSION_LIMIT
    if outcome == API_ERROR:
        return API_ERROR

    resp_text = process_output if final_response_text is None else final_response_text
    status = brief_status(resp_text)
    if exit_code == 0 and status == "complete":
        return COMPLETE
    return OTHER
