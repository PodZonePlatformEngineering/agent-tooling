"""
session_finalise.py — per-session Qdrant-driven consolidation (PROJ-039 § 1.4).

Implements the 4 session-scoped `consolidate-tasks` steps, driven from the
`session` point's `response.status_transition` and `work_item` link rather
than the legacy outbox-markdown file. DTD § 1.4:

  1. Parse outbox → DISSOLVED. The session point *is* the structured record.
  2. Apply to tasklist. Read `response.status_transition` + `work_item` from
     the session point; update the matching row in `planning/team-tasklist.md`.
  3. Update STATUS. Rewrite the `### {Agent}` block in `planning/STATUS.md`
     using the status transition (if present).
  4. Mark processed → DISSOLVED. No outbox file to mark.

Plus § 2.4 steps 6-7 (the deferred bits):
  6. session-finalise reads the session point and applies steps 1-4 above.
  7. Brief-result PR: generate `results/session-{date}-{slug}.md` from the
     session point, commit to the home-team-agent repo, raise a PR.

**Scope guard (brief § "Out of scope"):** this module does NOT reshape the
live `consolidate-tasks` skill — it is a library/tool implementation.  The
actual skill reshape is Phase C (T-011). The 9 cross-session steps (F-2-016)
are untouched.

**Parity contract (DT-008):** `apply_from_point` applied to a session point +
the equivalent outbox markdown must produce the same tasklist and STATUS state
(diff = 0).  The outbox-markdown path is `_apply_from_outbox_markdown` (used
only by DT-008 fixture; never called in production).
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _git(repo_dir: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_dir, *args],
        capture_output=True, text=True, check=False,
    )


# ---------------------------------------------------------------------------
# Tasklist operations (step 2 / DT-008)
# ---------------------------------------------------------------------------

def _find_work_item_row(tasklist_text: str, work_item: str) -> Optional[tuple[int, str]]:
    """Return ``(line_index, line)`` for the first tasklist row matching ``work_item``.

    Matches task rows that contain the work_item string (e.g. ``PROJ-039/T-009``),
    the derived CC number, or the slug — whichever appears in the row.
    """
    for i, line in enumerate(tasklist_text.splitlines(keepends=True)):
        # Table row: starts with | and contains the work_item token
        if "|" in line and work_item.replace("/", "/").lower() in line.lower():
            return i, line
    return None


_STATUS_EMOJI = {
    "complete": "✅ Complete",
    "done": "✅ Complete",
    "in_progress": "🔄 In Progress",
    "in-progress": "🔄 In Progress",
    "blocked": "⛔ Blocked",
    "ready": "🚀 Ready",
}


def _canonical_status(status_transition: Optional[str], date: str) -> Optional[str]:
    """Map a ``from->to`` transition (or bare status) to a STATUS-column string."""
    if not status_transition:
        return None
    # "in_progress->complete" → "complete"
    final = status_transition.split("->")[-1].strip().lower()
    canon = _STATUS_EMOJI.get(final)
    if canon and "✅" in canon:
        return f"{canon} {date}"
    return canon


def apply_status_to_tasklist(
    tasklist_text: str,
    *,
    work_item: str,
    status_transition: Optional[str],
    date: Optional[str] = None,
) -> str:
    """Return updated tasklist text with the status column rewritten for ``work_item``.

    Idempotent: if the row already carries the target status, returns text unchanged.
    If the row is not found, returns text unchanged (non-fatal — the tasklist may
    predate the work_item, or use a different slug form).
    """
    if not work_item or not status_transition:
        return tasklist_text
    date = date or _now_date()
    new_status = _canonical_status(status_transition, date)
    if not new_status:
        return tasklist_text

    match = _find_work_item_row(tasklist_text, work_item)
    if match is None:
        return tasklist_text

    idx, old_line = match
    # Replace the Status cell (second | ... | column).
    # Table rows look like: | T-009 | CC-322 | 🚀 Ready | Agent | Summary |
    cells = old_line.split("|")
    if len(cells) < 4:
        return tasklist_text
    # Find the cell that contains a status emoji or keyword.
    for ci, cell in enumerate(cells):
        stripped = cell.strip()
        if stripped and any(
            kw in stripped.lower()
            for kw in ("ready", "in progress", "complete", "blocked", "🚀", "✅", "🔄", "⛔")
        ):
            cells[ci] = f" {new_status} "
            break
    new_line = "|".join(cells)
    lines = tasklist_text.splitlines(keepends=True)
    lines[idx] = new_line
    return "".join(lines)


# ---------------------------------------------------------------------------
# STATUS.md operations (step 3 / DT-008)
# ---------------------------------------------------------------------------

def _agent_section_pattern(agent: str) -> re.Pattern:
    # Matches the `### Hephaestus` heading (case-insensitive) through the next
    # `###` heading or end of string.  Used to find and update the agent block.
    return re.compile(
        rf"(### {re.escape(agent)}.*?)(?=^### |\Z)",
        re.DOTALL | re.MULTILINE | re.IGNORECASE,
    )


def update_status_block(
    status_text: str,
    *,
    agent: str,
    work_item: str,
    brief_summary: str,
    status_transition: Optional[str],
    session_id: str,
    date: Optional[str] = None,
) -> str:
    """Rewrite the `### {Agent}` block line for ``work_item`` in STATUS.md.

    Idempotent: if the line already reflects the target state, returns text
    unchanged. If the agent section is not found, the text is returned unchanged
    (non-fatal — the section may be empty at fresh setup).
    """
    date = date or _now_date()
    new_status = _canonical_status(status_transition, date)
    if not new_status:
        return status_text

    pattern = _agent_section_pattern(agent)
    match = pattern.search(status_text)
    if not match:
        return status_text

    section = match.group(0)
    lines = section.splitlines(keepends=True)
    updated_lines = []
    found = False
    for line in lines:
        if work_item.lower() in line.lower():
            # Replace the status prefix (emoji + text up to first —) or whole line.
            parts = line.split("—", 1)
            if len(parts) == 2:
                # Rewrite the status prefix while keeping the rest of the line.
                prefix_raw = parts[0]
                prefix_stripped = prefix_raw.lstrip("- ")
                # Remove old emoji + status words.
                clean_prefix = re.sub(
                    r"^[^\w`\[]*"
                    r"(?:✅[^—]*|🚀[^—]*|🔄[^—]*|⛔[^—*]*|⏸[^—*]*)?"
                    r"",
                    "",
                    prefix_stripped,
                ).strip()
                new_line = f"- `{work_item}` {new_status} — {parts[1].lstrip()}"
                updated_lines.append(new_line)
                found = True
                continue
        updated_lines.append(line)

    if not found:
        return status_text

    new_section = "".join(updated_lines)
    return status_text[:match.start()] + new_section + status_text[match.end():]


# ---------------------------------------------------------------------------
# Brief-result file (§ 2.4 step 7 / R-011 / MVP-8)
# ---------------------------------------------------------------------------

def generate_brief_result(
    session_point: dict,
    *,
    date: Optional[str] = None,
) -> str:
    """Render a ``results/session-{date}-{slug}.md`` from the session point payload.

    This is the commit-able brief-result that fulfils the PR obligation (R-011).
    It is deliberately plain text: the git history + the PR description carry the
    structured data; this file is the human-readable session record.
    """
    date = date or _now_date()
    session_id = session_point.get("session_id", "unknown")
    agent = session_point.get("agent", "unknown")
    work_item = session_point.get("work_item", "unknown")
    brief = session_point.get("brief") or {}
    response = session_point.get("response") or {}
    rollup = session_point.get("rollup") or {}
    stops = session_point.get("session_stop") or []

    brief_text = brief.get("text", "(no brief)")
    dispatch_ts = brief.get("dispatch_ts", "")
    response_text = response.get("text", "(no response)")
    status_transition = response.get("status_transition", "")
    end_ts = response.get("end_ts", "")

    tool_usage = rollup.get("tool_usage", {})
    cost_tokens = rollup.get("cost_tokens", {})
    tool_lines = "\n".join(
        f"  {tool}: {count}" for tool, count in sorted(tool_usage.items())
    ) or "  (none)"
    model_lines = "\n".join(
        f"  {model}: input={stats.get('input_tokens', 0)} "
        f"output={stats.get('output_tokens', 0)}"
        for model, stats in sorted(cost_tokens.items())
    ) or "  (none)"

    return (
        f"---\n"
        f"type: brief-result\n"
        f"session_id: {session_id}\n"
        f"agent: {agent}\n"
        f"work_item: {work_item}\n"
        f"date: {date}\n"
        f"status_transition: {status_transition}\n"
        f"---\n\n"
        f"# Session result — {agent} / {work_item}\n\n"
        f"## Brief (dispatched {dispatch_ts})\n\n"
        f"{brief_text}\n\n"
        f"## Response (ended {end_ts})\n\n"
        f"{response_text}\n\n"
        f"## Rollup\n\n"
        f"### Tool usage\n{tool_lines}\n\n"
        f"### Token cost\n{model_lines}\n\n"
        f"## Session stops ({len(stops)} recorded)\n\n"
        + "".join(
            f"- {s.get('ts', '?')}: tool_uses={s.get('tool_uses', '?')}\n"
            for s in stops
        )
        + "\n"
    )


def commit_brief_result(
    result_text: str,
    *,
    session_id: str,
    work_item: str,
    date: Optional[str],
    repo_dir: str,
    branch_name: Optional[str] = None,
    raise_pr: bool = True,
) -> dict:
    """Write the brief-result file, commit it to a new branch, and raise a PR.

    Returns ``{"file_path", "branch", "pr_url", "ok", "reason"}``.
    Failures are non-fatal (best-effort): ``ok=False`` with a reason string.
    """
    date = date or _now_date()
    slug = work_item.replace("/", "-").replace(" ", "-").lower()
    branch_name = branch_name or f"session/{date}-{slug}-result"
    results_dir = Path(repo_dir) / "results"
    results_dir.mkdir(exist_ok=True)
    filename = f"session-{date}-{slug}.md"
    file_path = results_dir / filename

    result = {"file_path": str(file_path), "branch": branch_name,
               "pr_url": "", "ok": False, "reason": ""}
    try:
        _git(repo_dir, "checkout", "-B", branch_name)
        file_path.write_text(result_text, encoding="utf-8")
        _git(repo_dir, "add", str(file_path))
        commit_msg = f"chore: session-result {session_id[:8]} {work_item}"
        r = _git(repo_dir, "commit", "-m", commit_msg)
        if r.returncode != 0 and "nothing to commit" not in r.stderr:
            result["reason"] = f"commit failed: {r.stderr.strip()}"
            return result
        if raise_pr:
            push = _git(repo_dir, "push", "origin", branch_name, "--force-with-lease")
            if push.returncode != 0:
                result["reason"] = f"push failed: {push.stderr.strip()}"
                return result
            pr = subprocess.run(
                ["gh", "pr", "create", "--title",
                 f"Session result: {work_item} ({date})",
                 "--body",
                 f"Auto-generated brief-result for session `{session_id}` "
                 f"(work_item: {work_item}, date: {date}).",
                 "--head", branch_name],
                capture_output=True, text=True, cwd=repo_dir, check=False,
            )
            result["pr_url"] = pr.stdout.strip()
            if pr.returncode != 0:
                result["reason"] = (
                    f"PR creation failed: {pr.stderr.strip()}"
                )
        result["ok"] = True
    except Exception as exc:
        result["reason"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# Top-level entry point (§ 2.4 step 6)
# ---------------------------------------------------------------------------

def apply_from_point(
    session_point: dict,
    *,
    tasklist_path: str | Path,
    status_path: str | Path,
    date: Optional[str] = None,
) -> dict:
    """Run the 4 session-scoped consolidation steps from the ``session`` point.

    Returns ``{"tasklist_changed", "status_changed", "work_item", "new_status"}``.
    Reads and rewrites the files in place; caller is responsible for committing.
    Does not raise — returns a dict with ``error`` key on failure.
    """
    date = date or _now_date()
    response = session_point.get("response") or {}
    work_item = session_point.get("work_item", "")
    agent = session_point.get("agent", "")
    session_id = session_point.get("session_id", "")
    status_transition = response.get("status_transition", "")
    brief_summary = (session_point.get("brief") or {}).get("text", "")[:80]

    result: dict = {
        "work_item": work_item,
        "status_transition": status_transition,
        "tasklist_changed": False,
        "status_changed": False,
        "new_status": None,
    }

    # Step 2: Apply to tasklist
    tasklist_path = Path(tasklist_path)
    if tasklist_path.exists() and work_item:
        old_text = tasklist_path.read_text(encoding="utf-8")
        new_text = apply_status_to_tasklist(
            old_text, work_item=work_item,
            status_transition=status_transition, date=date,
        )
        if new_text != old_text:
            tasklist_path.write_text(new_text, encoding="utf-8")
            result["tasklist_changed"] = True
        result["new_status"] = _canonical_status(status_transition, date)

    # Step 3: Update STATUS.md
    status_path = Path(status_path)
    if status_path.exists() and agent and work_item:
        old_text = status_path.read_text(encoding="utf-8")
        new_text = update_status_block(
            old_text, agent=agent, work_item=work_item,
            brief_summary=brief_summary, status_transition=status_transition,
            session_id=session_id, date=date,
        )
        if new_text != old_text:
            status_path.write_text(new_text, encoding="utf-8")
            result["status_changed"] = True

    # Step 4: Mark processed → dissolved (no outbox file; the marker vanishes).
    return result


# ---------------------------------------------------------------------------
# Legacy outbox-markdown path — used ONLY by DT-008 parity fixture
# ---------------------------------------------------------------------------

def _apply_from_outbox_markdown(
    outbox_text: str,
    *,
    tasklist_path: str | Path,
    status_path: str | Path,
    date: Optional[str] = None,
) -> dict:
    """Mirror of apply_from_point driven from an outbox markdown file.

    Parses ``work_item``, ``agent``, ``status_transition`` from frontmatter
    (YAML-ish ``key: value`` lines in the ``---`` block), then applies the
    same tasklist + STATUS updates as the point-driven path.

    This function is ONLY for DT-008 parity verification.  It is never called
    in production — the outbox file is the legacy path (F-2-016).
    """
    date = date or _now_date()
    # Parse frontmatter
    fm: dict[str, str] = {}
    if outbox_text.startswith("---"):
        end = outbox_text.find("---", 3)
        if end != -1:
            for line in outbox_text[3:end].splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    fm[k.strip()] = v.strip()

    work_item = fm.get("work_item", "")
    agent = fm.get("agent", "")
    session_id = fm.get("session_id", "")
    # Accept "status_transition: in_progress->complete" from the frontmatter
    status_transition = fm.get("status_transition", "")

    # If no status_transition in frontmatter, scan the body for a canonical pattern.
    if not status_transition:
        m = re.search(
            r"\bstatus[_-]transition\b[:\s]+([a-z_\-]+\s*->\s*[a-z_\-]+)",
            outbox_text, re.IGNORECASE,
        )
        if m:
            status_transition = m.group(1).replace(" ", "")

    result: dict = {
        "work_item": work_item,
        "status_transition": status_transition,
        "tasklist_changed": False,
        "status_changed": False,
        "new_status": None,
    }

    tasklist_path = Path(tasklist_path)
    if tasklist_path.exists() and work_item:
        old_text = tasklist_path.read_text(encoding="utf-8")
        new_text = apply_status_to_tasklist(
            old_text, work_item=work_item,
            status_transition=status_transition, date=date,
        )
        if new_text != old_text:
            tasklist_path.write_text(new_text, encoding="utf-8")
            result["tasklist_changed"] = True
        result["new_status"] = _canonical_status(status_transition, date)

    status_path = Path(status_path)
    if status_path.exists() and agent and work_item:
        old_text = status_path.read_text(encoding="utf-8")
        brief_summary = ""
        new_text = update_status_block(
            old_text, agent=agent, work_item=work_item,
            brief_summary=brief_summary, status_transition=status_transition,
            session_id=session_id, date=date,
        )
        if new_text != old_text:
            status_path.write_text(new_text, encoding="utf-8")
            result["status_changed"] = True

    return result
