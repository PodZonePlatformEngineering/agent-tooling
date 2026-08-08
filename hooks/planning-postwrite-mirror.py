#!/usr/bin/env python3
"""
planning-postwrite-mirror.py — ``PostToolUse`` hook, the interim
incremental-sync half of the ``.planning/`` mirror (PROJ-029 plannerapi BCP
mechanism, spec §6.2.2, build item 5/6).

**Filter shape**: matches ``notify-pr.py``'s existing precedent (filter
``PostToolUse`` on a specific tool-call shape, not a dedicated event type) —
here, calls to ``mcp__Neon__run_sql``/``mcp__Neon__run_sql_transaction``
against the ``podzone-planner`` project whose SQL touches ``planning.*`` in
a write shape (``INSERT``/``UPDATE``/``DELETE``, or a call to one of the 4
write RPCs, ``006_rpcs.sql``). A plain read against ``planning.*`` (a
``SELECT`` for context) is intentionally ignored — nothing changed, nothing
to mirror or queue.

**Hook-input-shape assumption, spelled out rather than silently relied on**:
Claude Code's documented ``PostToolUse`` contract gives ``tool_name`` as the
full ``mcp__<server>__<tool>`` id and ``tool_input`` as the MCP call's own
JSON arguments verbatim — for these two Neon tools that's ``sql``/
``sqlStatements``/``projectId``, confirmed directly against the tool
schemas (not guessed) — plus ``tool_response`` carrying the MCP result. That
is enough information to filter on and to detect failure. If a live run
ever shows this hook silently never firing (e.g. an MCP-call shape Claude
Code doesn't route through ``PostToolUse`` the same way as a plain tool),
the next-best trigger per the spec's own fallback framing is a periodic
``/schedule`` job polling ``planning.*`` for recent writes — not built here,
flagging only.

On a **successful** planning write: re-run the full materialise pass (see
``lib.planning_mirror.materialise``'s docstring for why v1 is a full
re-write, not a targeted diff). On a **failed** one (the outage case):
best-effort parse the write into a ``{"rpc", "args"}`` record and append it
to ``.planning/pending-changes.jsonl`` instead of failing hard — never a
direct edit to a mirrored row file (spec §6.2.4).

Degrades soft: never raises, never blocks, always exits 0 — matches every
other fleet ``PostToolUse`` hook's contract (see ``notify-pr.py``).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

PLANNING_PROJECT_ID = "shy-firefly-36997432"  # podzone-planner
NEON_TOOL_NAMES = ("mcp__Neon__run_sql", "mcp__Neon__run_sql_transaction")

_WRITE_RE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+planning\.|"
    r"planning\.(close_task|supersede_task|register_session|conclude_session)\s*\(",
    re.IGNORECASE,
)

_RPC_CALL_RE = re.compile(
    r"planning\.(close_task|supersede_task|register_session|conclude_session)\s*\((.*)\)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)

_RPC_PARAM_NAMES = {
    "close_task": ["task_id", "reason", "status"],
    "supersede_task": ["task_id", "superseded_by", "reason"],
    "register_session": ["brief_id", "agent", "task_ids", "home_repo"],
    "conclude_session": ["session_id", "status", "outcome_note", "pr_refs", "task_status"],
}


def log(msg: str) -> None:
    print(f"[planning-postwrite-mirror] {msg}", file=sys.stderr)


def _split_sql_args(arg_str: str) -> list[str]:
    """Best-effort top-level comma split, single-quote-aware. Returns ``[]``
    (never guesses) if the string looks unbalanced — an unclosed quote or
    bracket means this parser isn't equipped for the call shape, and the
    caller falls back to queuing the raw SQL instead."""
    tokens: list[str] = []
    buf: list[str] = []
    in_quote = False
    depth = 0
    i, n = 0, len(arg_str)
    while i < n:
        ch = arg_str[i]
        if in_quote:
            if ch == "'" and i + 1 < n and arg_str[i + 1] == "'":
                buf.append("''")
                i += 1
            elif ch == "'":
                in_quote = False
                buf.append(ch)
            else:
                buf.append(ch)
        elif ch == "'":
            in_quote = True
            buf.append(ch)
        elif ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            tokens.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    if in_quote or depth != 0:
        return []
    tokens.append("".join(buf).strip())
    return tokens


def _unwrap_literal(tok: str):
    tok = tok.strip()
    if tok.upper() == "NULL":
        return None
    if len(tok) >= 2 and tok.startswith("'") and tok.endswith("'"):
        return tok[1:-1].replace("''", "'")
    return tok  # arrays/casts/numbers kept as raw SQL text


def _try_parse_rpc(sql: str) -> dict | None:
    """Parse a simple literal RPC call (e.g.
    ``SELECT planning.close_task('<uuid>', 'reason text', 'closed')``) into
    ``{"rpc", "args"}``. Returns ``None`` — never guesses — for anything more
    complex (bind params, nested expressions, array casts): the caller falls
    back to queuing the raw SQL, which reconcile can still replay."""
    m = _RPC_CALL_RE.search(sql.strip())
    if not m:
        return None
    rpc = m.group(1).lower()
    raw_args = _split_sql_args(m.group(2))
    param_names = _RPC_PARAM_NAMES[rpc]
    if len(raw_args) != len(param_names):
        return None
    return {"rpc": rpc, "args": {name: _unwrap_literal(tok) for name, tok in zip(param_names, raw_args)}}


def _tool_failed(tool_response) -> bool:
    """Best-effort failure detection across the couple of shapes an MCP
    tool_response might take. Errs toward "not failed" (False) on an
    ambiguous shape — a missed failure just means the interim mirror runs
    a redundant materialise pass instead of queuing; a false positive would
    wrongly queue a change that actually landed, which is worse."""
    if not tool_response:
        return False
    if isinstance(tool_response, dict):
        if tool_response.get("isError") or tool_response.get("is_error"):
            return True
        err = tool_response.get("error")
        if err:
            return True
        text = json.dumps(tool_response.get("content", tool_response))
    else:
        text = str(tool_response)
    lowered = text.lower()
    return "error" in lowered and any(
        s in lowered
        for s in ("connect", "timeout", "unreachable", "econnrefused", "could not", "refused")
    )


def main() -> int:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        hook_input = json.loads(raw)
    except Exception as exc:
        log(f"could not parse stdin (degrading soft): {exc}")
        return 0

    try:
        tool_name = hook_input.get("tool_name", "")
        if tool_name not in NEON_TOOL_NAMES:
            return 0

        tool_input = hook_input.get("tool_input") or {}
        if str(tool_input.get("projectId", "")) != PLANNING_PROJECT_ID:
            return 0

        sql = tool_input.get("sql") or ""
        sql_statements = tool_input.get("sqlStatements") or []
        full_sql = sql or "\n".join(sql_statements)
        if not _WRITE_RE.search(full_sql):
            return 0

        repo_dir = os.environ.get("CLAUDE_PROJECT_DIR") or str(hook_input.get("cwd") or os.getcwd())
        from lib import planning_mirror

        if _tool_failed(hook_input.get("tool_response")):
            record = _try_parse_rpc(full_sql)
            if record is None:
                record = {"rpc": "raw_sql", "args": {"sql": sql, "sql_statements": sql_statements}}
            ok = planning_mirror.queue_pending_change(repo_dir, record["rpc"], record["args"])
            log(f"planning write failed — queued to pending-changes.jsonl "
                f"(rpc={record['rpc']!r}, ok={ok})")
            return 0

        result = planning_mirror.materialise(repo_dir)
        if result["ok"]:
            log(f"interim mirror refreshed: {result['counts']}")
        else:
            log(f"interim mirror pass failed (degrading soft): {result['error']}")
        return 0
    except Exception as exc:
        log(f"unexpected error (degrading soft): {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
