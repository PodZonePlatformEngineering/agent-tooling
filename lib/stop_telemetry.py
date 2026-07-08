"""
stop_telemetry.py — Stop-hook payload assembly (PROJ-039/T-071, CC-381).

Everything ``stop.sh`` used to assemble inline bash→python (untestable) lives
here as pure, stdlib-only functions over parsed transcript records, so the
extractors are fixture-testable. The thin hook entry point is
``hooks/stop-telemetry.py``; this module does no embedding and no Qdrant I/O.

Three enrichment fields (plan §T-071, operator-confirmed 2026-07-07):

  * ``last_assistant_message`` — the last ``end_turn`` assistant record's text
    blocks concatenated (fallback: last assistant record carrying text), capped
    at ~16 KB. Read via a bounded backwards tail of ``transcript_path``
    (Stop hooks have a latency budget) that widens only on a miss.
  * ``background_tasks``   — best-effort transcript derivation: background
    ``Bash``/``Agent`` starts not yet matched by a completion at stop time.
    No harness state file is documented for these; the transcript is the source.
  * ``session_crons``      — discovery result (2026-07-08, this workstation):
    no CronCreate/CronList registry exists under ``~/.claude/`` (``jobs/`` holds
    background-job state, ``tasks/`` task lists — neither is a cron store), so
    :func:`find_cron_registry` probes the plausible locations for a future
    harness version and callers fall back to reconstruction from
    CronCreate/CronDelete tool_use records. Empty default.

Deviation noted for Hermes: the live Stop point id gains turn linkage — when the
last ``end_turn`` record is identifiable its ``uuid`` keys a deterministic
``uuid5("stop/{session_id}/{turn_uuid}")`` point id (a retried Stop hook on the
same turn converges instead of duplicating); the legacy random uuid4 remains the
fallback when no turn is identifiable.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Stored-message cap (~16 KB, plan §T-071). Caps the payload field only — the
# embed input is bounded separately (T-027) by the hook entry.
MESSAGE_CAP_CHARS = 16 * 1024

# Bounded backwards tail-read of the transcript (Stop-hook latency budget):
# 256 KB covers the recent turns a Stop needs; widen ×8 then whole-file only
# when the extract misses (plan: "widen only on miss").
TAIL_WINDOW_BYTES = 256 * 1024
WIDEN_FACTOR = 8

# Heads stored for best-effort derivations — enough to recognise the task/cron,
# not a second copy of the transcript.
DESCRIPTION_HEAD_CHARS = 160
PROMPT_HEAD_CHARS = 200

# The harness's own phrasing in a background Bash tool_result:
#   "Command running in background with ID: bj7qvs89u. Output is being written…"
_BACKGROUND_ID_RE = re.compile(r"background with ID:\s*([A-Za-z0-9_-]+)")
# Words that mark a later mention of a known background-task id as a completion.
_COMPLETION_MARKERS = ("complet", "finish", "exit", "fail")

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)

CRON_REGISTRY_CANDIDATES = ("crons", "cron", "schedules", "routines")


# --------------------------------------------------------------------------- #
# Transcript reading
# --------------------------------------------------------------------------- #

def read_tail_records(transcript_path: str | Path,
                      *, window: Optional[int] = TAIL_WINDOW_BYTES) -> list[dict]:
    """Parse the last ``window`` bytes of a JSONL transcript into records.

    ``window=None`` reads the whole file. When the window starts mid-file the
    first (almost certainly partial) line is dropped. Unparseable lines are
    skipped — a Stop hook must tolerate a transcript still being flushed.
    """
    path = Path(transcript_path)
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if window is not None and size > window:
                fh.seek(size - window)
                fh.readline()  # discard the partial first line
            data = fh.read()
    except OSError:
        return []
    records: list[dict] = []
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _content_blocks(record: dict) -> list[dict]:
    blocks = (record.get("message") or {}).get("content")
    if not isinstance(blocks, list):
        return []
    return [b for b in blocks if isinstance(b, dict)]


def _text_of(record: dict) -> str:
    """Concatenated text blocks of an assistant record ('' when none)."""
    parts = [b.get("text", "") for b in _content_blocks(record)
             if b.get("type") == "text" and b.get("text")]
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# 1. last_assistant_message
# --------------------------------------------------------------------------- #

def extract_last_assistant_message(records: list[dict]) -> dict:
    """The turn-ending assistant message from parsed records.

    Returns ``{"text", "source", "turn_uuid"}`` where ``source`` is
    ``"end_turn"`` (an ``stop_reason == "end_turn"`` record carried the text),
    ``"fallback"`` (no such record — last assistant record carrying text), or
    ``""`` (nothing found). ``text`` is capped at :data:`MESSAGE_CAP_CHARS`.
    Sidechain (subagent) records never qualify.
    """
    fallback: Optional[dict] = None
    for rec in reversed(records):
        if rec.get("type") != "assistant" or rec.get("isSidechain"):
            continue
        text = _text_of(rec)
        if not text:
            continue
        entry = {
            "text": text[:MESSAGE_CAP_CHARS],
            "turn_uuid": rec.get("uuid", "") or "",
        }
        if (rec.get("message") or {}).get("stop_reason") == "end_turn":
            entry["source"] = "end_turn"
            return entry
        if fallback is None:
            fallback = {**entry, "source": "fallback"}
    return fallback or {"text": "", "source": "", "turn_uuid": ""}


def read_transcript_for_stop(transcript_path: str,
                             *, window: int = TAIL_WINDOW_BYTES) -> tuple[list[dict], dict]:
    """Tail-read ``transcript_path`` with widen-on-miss; returns (records, message).

    Window ladder: ``window`` → ``window × WIDEN_FACTOR`` → whole file, stopping
    as soon as a message is found or the window already covered the file. The
    returned records are from the widest read performed, so the other extractors
    see at least the span the message came from.
    """
    empty = {"text": "", "source": "", "turn_uuid": ""}
    if not transcript_path:
        return [], empty
    path = Path(transcript_path)
    if not path.is_file():
        return [], empty
    try:
        size = path.stat().st_size
    except OSError:
        return [], empty
    records: list[dict] = []
    message = empty
    for w in (window, window * WIDEN_FACTOR, None):
        records = read_tail_records(path, window=w)
        message = extract_last_assistant_message(records)
        if message["text"] or w is None or w >= size:
            break
    return records, message


# --------------------------------------------------------------------------- #
# 2. background_tasks
# --------------------------------------------------------------------------- #

def extract_background_tasks(records: list[dict]) -> list[dict]:
    """Background ``Bash``/``Agent`` starts not yet matched by a completion.

    Strictly best-effort (no harness state file is documented; the transcript is
    the only source visible to the hook):

      * start   — an assistant ``tool_use`` block: ``Bash`` with
        ``run_in_background: true``, or ``Agent`` (background by default unless
        ``run_in_background: false``).
      * task id — the matching ``tool_result`` ("… running in background with
        ID: x") or the record-level ``toolUseResult.backgroundTaskId``.
      * done    — a matching ``tool_result`` that is NOT a background-launch
        notice (a synchronous/final result), or any later record that mentions a
        known task id alongside a completion marker.

    Returns ``[{type, description, started_at, task_id?}]``; empty default.
    """
    pending: dict[str, dict] = {}       # tool_use_id -> entry
    id_to_tool_use: dict[str, str] = {}  # background task id -> tool_use_id
    for rec in records:
        rtype = rec.get("type")
        if rtype == "assistant" and not rec.get("isSidechain"):
            for block in _content_blocks(rec):
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                inp = block.get("input") or {}
                if name == "Bash" and inp.get("run_in_background"):
                    kind = "bash"
                    desc = inp.get("description") or inp.get("command") or ""
                elif name == "Agent" and inp.get("run_in_background", True) is not False:
                    kind = "agent"
                    desc = inp.get("description") or ""
                else:
                    continue
                pending[block.get("id", "")] = {
                    "type": kind,
                    "description": desc[:DESCRIPTION_HEAD_CHARS],
                    "started_at": rec.get("timestamp", "") or "",
                }
        elif rtype == "user" and pending:
            record_bg = rec.get("toolUseResult")
            record_bg_id = (record_bg.get("backgroundTaskId")
                            if isinstance(record_bg, dict) else None)
            for block in _content_blocks(rec):
                if block.get("type") != "tool_result":
                    continue
                tool_use_id = block.get("tool_use_id", "")
                if tool_use_id not in pending:
                    continue
                content = block.get("content")
                text = content if isinstance(content, str) else json.dumps(content or "")
                match = _BACKGROUND_ID_RE.search(text)
                task_id = record_bg_id or (match.group(1) if match else None)
                if task_id:
                    pending[tool_use_id]["task_id"] = task_id
                    id_to_tool_use[task_id] = tool_use_id
                elif pending[tool_use_id]["type"] == "agent" and (
                    "background" in text.lower() or "launched" in text.lower()
                ):
                    pass  # background-agent launch notice — still running
                else:
                    # A synchronous/final result arrived — not (or no longer)
                    # running in the background.
                    pending.pop(tool_use_id, None)
        # Completion notification: any later record naming a known task id with
        # a completion-ish word ("… completed / exited / failed …").
        if id_to_tool_use:
            blob = json.dumps(rec).lower()
            for task_id, tool_use_id in list(id_to_tool_use.items()):
                if tool_use_id not in pending:
                    id_to_tool_use.pop(task_id, None)
                    continue
                if task_id.lower() in blob and any(
                    marker in blob for marker in _COMPLETION_MARKERS
                ):
                    # Ignore the launch notice itself.
                    if "running in background with id" in blob:
                        continue
                    pending.pop(tool_use_id, None)
                    id_to_tool_use.pop(task_id, None)
    return list(pending.values())


# --------------------------------------------------------------------------- #
# 3. session_crons
# --------------------------------------------------------------------------- #

def find_cron_registry(claude_home: Optional[str] = None) -> Optional[Path]:
    """Locate an on-disk cron registry under ``~/.claude`` — None when absent.

    Discovery result (T-071, 2026-07-08): no such registry exists on the
    reference workstation, so the live extractor is the transcript fallback
    (:func:`extract_session_crons`); this probe exists so a future harness
    version that persists one is picked up without a code change.
    """
    base = Path(claude_home or os.path.expanduser("~/.claude"))
    for name in CRON_REGISTRY_CANDIDATES:
        candidate = base / name
        try:
            if candidate.is_dir() and any(candidate.iterdir()):
                return candidate
        except OSError:
            continue
    return None


def extract_session_crons(records: list[dict]) -> list[dict]:
    """Session crons reconstructed from CronCreate/CronDelete tool_use records.

    Best-effort fallback (no on-disk registry — see :func:`find_cron_registry`):
    a CronCreate adds ``{id, schedule, prompt}`` (id parsed from its tool_result
    when one is quotable); a CronDelete removes the entry whose id matches.
    Empty default.
    """
    crons: dict[str, dict] = {}  # tool_use_id -> entry
    for rec in records:
        rtype = rec.get("type")
        if rtype == "assistant" and not rec.get("isSidechain"):
            for block in _content_blocks(rec):
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                inp = block.get("input") or {}
                if name == "CronCreate":
                    crons[block.get("id", "")] = {
                        "id": str(inp.get("id") or ""),
                        "schedule": str(
                            inp.get("schedule") or inp.get("cron")
                            or inp.get("interval") or ""
                        ),
                        "prompt": str(
                            inp.get("prompt") or inp.get("command") or ""
                        )[:PROMPT_HEAD_CHARS],
                    }
                elif name == "CronDelete":
                    target = str(
                        inp.get("id") or inp.get("cronId") or inp.get("name") or ""
                    )
                    if target:
                        for key, entry in list(crons.items()):
                            if entry.get("id") == target:
                                crons.pop(key)
        elif rtype == "user" and crons:
            for block in _content_blocks(rec):
                if block.get("type") != "tool_result":
                    continue
                tool_use_id = block.get("tool_use_id", "")
                entry = crons.get(tool_use_id)
                if entry is None or entry["id"]:
                    continue
                content = block.get("content")
                text = content if isinstance(content, str) else json.dumps(content or "")
                match = _UUID_RE.search(text) or re.search(
                    r"\bid[:=\s]+['\"]?([A-Za-z0-9_-]{4,})", text, re.IGNORECASE
                )
                if match:
                    entry["id"] = match.group(1) if match.lastindex else match.group(0)
    return list(crons.values())


# --------------------------------------------------------------------------- #
# Payload assembly
# --------------------------------------------------------------------------- #

def _git_info(cwd: str) -> dict:
    """Repository block for the payload — mirrors the old stop.sh bash probes."""
    def _git(*args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", "-C", cwd, *args],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            return "unknown"
        out = proc.stdout.strip()
        return out if proc.returncode == 0 and out else "unknown"

    name = os.path.basename(os.path.normpath(cwd or ".")) or "unknown"
    return {
        "name": name,
        "git_branch": _git("branch", "--show-current"),
        "commit_sha": _git("rev-parse", "--short", "HEAD"),
    }


def build_payload(hook_input: dict, *, now_iso: Optional[str] = None,
                  tail_window: int = TAIL_WINDOW_BYTES) -> dict:
    """The full CST Stop payload from the hook's stdin JSON.

    Pure assembly — no embed, no Qdrant. Extraction failures degrade to the
    documented empty defaults rather than raising (a Stop hook must never break
    the session); a missing/empty transcript yields the same defaults.
    """
    session_id = str(hook_input.get("session_id", ""))
    stop_reason = str(hook_input.get("stop_reason", "end_turn") or "end_turn")
    cwd = str(hook_input.get("cwd", ".") or ".")
    transcript_path = str(hook_input.get("transcript_path", "") or "")

    try:
        records, message = read_transcript_for_stop(
            transcript_path, window=tail_window
        )
    except Exception:
        records, message = [], {"text": "", "source": "", "turn_uuid": ""}
    try:
        background_tasks = extract_background_tasks(records)
    except Exception:
        background_tasks = []
    try:
        session_crons = extract_session_crons(records)
    except Exception:
        session_crons = []

    return {
        "event_type": "Stop",
        "session_id": session_id,
        "timestamp": now_iso or datetime.now(timezone.utc).isoformat(),
        "repository": _git_info(cwd),
        "environment": {
            "user": os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown",
            "platform": platform.system().lower(),
        },
        "stop_reason": stop_reason,
        "last_assistant_message": message["text"],
        "message_source": message["source"],
        "turn_uuid": message["turn_uuid"],
        "background_tasks": background_tasks,
        "session_crons": session_crons,
    }


def embed_input_for(payload: dict) -> str:
    """The embed input for the Stop point's ``response_vector``.

    T-071 embed switch (operator-confirmed): the head-truncated message text
    replaces the constant ``"Stop: end_turn"``; the constant survives only as
    the fallback when no message could be extracted, so the vector is never
    empty. Head-truncation (T-027 bounding) is applied by the hook entry.
    """
    message = payload.get("last_assistant_message") or ""
    if message:
        return message
    return f"Stop: {payload.get('stop_reason', 'end_turn')}"


def point_id_for_stop(session_id: str, turn_uuid: str = "") -> str:
    """Deterministic Stop point id when the turn is identifiable, else uuid4.

    ``uuid5("stop/{session_id}/{turn_uuid}")`` gives the live hook turn linkage
    and retry convergence; the uuid4 fallback preserves the legacy behaviour
    when no ``end_turn`` record was found. (T-072's backfill ids use the
    distinct ``stop-backfill/…`` namespace string.)
    """
    if session_id and turn_uuid:
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"stop/{session_id}/{turn_uuid}"))
    return str(uuid.uuid4())
