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
  7. Brief-result PR: generate `results/session-{date}-{slug}-{sid}.md` from the
     session point, commit to the home-team-agent repo, raise a PR. The `{sid}`
     suffix keys idempotency on session_id (T-039).

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
import shutil
import subprocess
import tempfile
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
    """Render a ``results/session-{date}-{slug}-{sid}.md`` from the session point payload.

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
    # Key the filename + branch on session_id (T-039) so a re-set-up session on the
    # same date+slug authors a distinct result rather than clobbering the prior one;
    # a same-sid re-run resolves to the same names and stays idempotent.
    sid = (session_id or "nosid")[:8]
    branch_name = branch_name or f"session/{date}-{slug}-{sid}-result"
    results_dir = Path(repo_dir) / "results"
    results_dir.mkdir(exist_ok=True)
    filename = f"session-{date}-{slug}-{sid}.md"
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
# Home-repo session result (PROJ-039/T-035 — hooks-only migrated home repos)
# ---------------------------------------------------------------------------
#
# In a migrated home repo there is NO `/session-end` skill (the repo is hooks-only
# by design — hooks + lib + primitives, no skills/). The SessionEnd finalise hook
# therefore OWNS the session result: it authors `results/session-{date}-{slug}-{sid}.md`
# from the substrate point and raises a home-repo PR that lands it on home `main`,
# decoupled from any work PR. The functions below are the testable core of that
# step; the hook's migrated branch is a thin caller over them.

# The canonical structured sections the home-repo result surfaces. Authored from
# the substrate point's `response.text` (the agent's final `/exit` turn) when it
# carries these headers; any section the response does not provide renders as
# "_None recorded._". The agent's final turn is the source of truth — it should
# carry these headers for a rich result.
RESULT_SECTIONS = ("Completed", "Started", "Blockers", "Decisions",
                   "Questions for Martin")

# Header-text aliases → canonical label (matched case-insensitively).
_SECTION_ALIASES = {
    "completed": "Completed",
    "complete": "Completed",
    "done": "Completed",
    "started": "Started",
    "in progress": "Started",
    "in-progress": "Started",
    "blockers": "Blockers",
    "blocked": "Blockers",
    "blocker": "Blockers",
    "decisions": "Decisions",
    "decision": "Decisions",
    "questions for martin": "Questions for Martin",
    "questions": "Questions for Martin",
    "open questions": "Questions for Martin",
}

_HEADER_RE = re.compile(r"^\s*#{1,6}\s+(?P<t>.+?)\s*$")
_BOLD_RE = re.compile(r"^\s*\*\*(?P<t>.+?)\*\*:?\s*$")
_COLON_RE = re.compile(r"^\s*(?P<t>[A-Za-z][A-Za-z ’'\-]{1,38}):\s*$")


def _normalise_label(raw: str) -> str:
    """Strip markdown/emphasis/emoji decoration from a header label and lowercase."""
    s = raw.strip().strip("*").strip().rstrip(":").strip()
    s = re.sub(r"^[^0-9A-Za-z]+", "", s)  # drop a leading emoji / bullet run
    return s.lower()


def _match_section_header(line: str) -> Optional[str]:
    """Return the canonical section label if ``line`` is a header for one, else None.

    Only header-shaped lines qualify (``## X``, ``**X**``, or a short ``X:`` line),
    so prose that merely contains the word "completed" is never mistaken for a header.
    """
    for rx in (_HEADER_RE, _BOLD_RE, _COLON_RE):
        m = rx.match(line)
        if m:
            key = _normalise_label(m.group("t"))
            if key in _SECTION_ALIASES:
                return _SECTION_ALIASES[key]
    return None


def extract_sections(text: str) -> dict[str, str]:
    """Extract canonical sections from a response/summary markdown body.

    Returns ``{canonical_label: body_text}`` for every recognised section header
    found. Bodies are everything up to the next recognised header. Unrecognised
    headers terminate the current section (so a section never bleeds into the next
    heading) but are not themselves captured.
    """
    sections: dict[str, list[str]] = {}
    current: Optional[str] = None
    for line in (text or "").splitlines():
        label = _match_section_header(line)
        if label is not None:
            current = label
            sections.setdefault(current, [])
            continue
        # A non-canonical markdown header still ends the current section.
        if current is not None and (_HEADER_RE.match(line) or _BOLD_RE.match(line)):
            current = None
            continue
        if current is not None:
            sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


# The finalise hook's placeholder when the transcript yielded no assistant turn.
_NO_RESPONSE_SENTINELS = ("(no response)",)


def _render_result_sections(extracted: dict[str, str], response_text: str) -> str:
    """Render the top structured block of the result doc.

    Normal path — the final turn used the canonical headers: one ``## {label}``
    block per section, absent ones filled with ``_None recorded._``.

    T-047 raw-response fallback (CC-348) — the final turn carried **none** of the
    canonical headers but a real response exists (a free-form summary, or a headless
    ``claude -p`` turn that used its own ``##``/``###`` headers such as
    ``## PROJ-011/T-021`` — see home#20/#21/#24). Authoring five "None recorded"
    stubs there buries a rich response under a false "nothing happened" summary, so
    instead embed the response verbatim under one ``## Session response (raw)``
    block. Only the genuinely empty case (no sections AND no real response) still
    renders the stubs.
    """
    real = bool(response_text.strip()) and response_text.strip() not in _NO_RESPONSE_SENTINELS
    if not extracted and real:
        return (
            "## Session response (raw)\n\n"
            "_The final turn carried none of the canonical section headers "
            "(Completed / Started / Blockers / Decisions / Questions for Martin), "
            "so the full response is embedded verbatim below rather than stubbed._\n\n"
            f"{response_text.strip()}\n\n"
        )
    section_md = ""
    for label in RESULT_SECTIONS:
        body = extracted.get(label) or "_None recorded._"
        section_md += f"## {label}\n\n{body}\n\n"
    return section_md


def generate_session_result(
    session_point: dict,
    *,
    date: Optional[str] = None,
) -> str:
    """Render the home-repo ``results/session-{date}-{slug}-{sid}.md`` from the point.

    Surfaces the five canonical structured sections (Completed / Started /
    Blockers / Decisions / Questions for Martin), extracted from the substrate
    ``response.text`` where present, then the full brief, response and rollup as
    the durable human-readable record. Pure — no I/O — so it is fully unit-tested.
    """
    date = date or _now_date()
    session_id = session_point.get("session_id", "unknown")
    agent = session_point.get("agent", "unknown")
    work_item = session_point.get("work_item", "unknown")
    brief = session_point.get("brief") or {}
    response = session_point.get("response") or {}
    rollup = session_point.get("rollup") or {}

    brief_text = brief.get("text", "(no brief)")
    dispatch_ts = brief.get("dispatch_ts", "")
    response_text = response.get("text", "(no response)")
    status_transition = response.get("status_transition") or ""
    end_ts = response.get("end_ts", "")

    extracted = extract_sections(response_text)
    section_md = _render_result_sections(extracted, response_text)

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
        f"type: session-result\n"
        f"session_id: {session_id}\n"
        f"agent: {agent}\n"
        f"work_item: {work_item}\n"
        f"date: {date}\n"
        f"status_transition: {status_transition}\n"
        f"---\n\n"
        f"# Session result — {agent} / {work_item}\n\n"
        f"{section_md}"
        f"## Brief (dispatched {dispatch_ts})\n\n"
        f"{brief_text}\n\n"
        f"## Full response (ended {end_ts})\n\n"
        f"{response_text}\n\n"
        f"## Rollup\n\n"
        f"### Tool usage\n{tool_lines}\n\n"
        f"### Token cost\n{model_lines}\n"
    )


def _resolve_base_ref(repo_dir: str, base_branch: str) -> Optional[str]:
    """Prefer ``origin/{base}``; fall back to a local ``{base}`` branch."""
    if _git(repo_dir, "rev-parse", "--verify",
            f"refs/remotes/origin/{base_branch}").returncode == 0:
        return f"origin/{base_branch}"
    if _git(repo_dir, "rev-parse", "--verify",
            f"refs/heads/{base_branch}").returncode == 0:
        return base_branch
    return None


def _result_on_base(repo_dir: str, base_ref: str, rel_path: str) -> bool:
    """True if ``rel_path`` already exists on ``base_ref`` (result already merged)."""
    return _git(repo_dir, "cat-file", "-e",
                f"{base_ref}:{rel_path}").returncode == 0


def _open_result_pr_exists(repo_dir: str, branch_name: str) -> bool:
    """True if an open PR already carries the result branch (best-effort via gh)."""
    r = subprocess.run(
        ["gh", "pr", "list", "--head", branch_name, "--state", "open",
         "--json", "number", "-q", ".[].number"],
        capture_output=True, text=True, cwd=repo_dir, check=False,
    )
    return r.returncode == 0 and bool(r.stdout.strip())


def _result_pr_body(session_id: str, work_item: str, date: str) -> str:
    return (
        f"Auto-authored session result for `{work_item}` "
        f"(session `{session_id[:12]}`, {date}).\n\n"
        f"Generated by the SessionEnd **finalise hook** (PROJ-039/T-035) in a "
        f"hooks-only migrated home repo — there is no `/session-end` skill; the "
        f"hook owns the result. Decoupled from any work PR so it cannot be "
        f"orphaned.\n\n"
        f"Please review the **Questions for Martin** section in the result file."
    )


def commit_home_result(
    result_text: str,
    *,
    session_id: str,
    work_item: str,
    date: Optional[str],
    repo_dir: str,
    raise_pr: bool = True,
    base_branch: str = "main",
) -> dict:
    """Author the home-repo session result on a decoupled branch off ``base_branch``
    and (optionally) raise a PR landing it on ``base_branch``.

    Uses an **isolated git worktree** off the base ref, so the live working tree
    and the session's own branch are never touched — the result branch is wholly
    independent of any work PR. Idempotent:

      * if the result file already exists on ``base_branch`` → ``exists`` (no-op);
      * if an open PR already carries the result branch → ``exists`` (no-op);
      * otherwise it authors + pushes + PRs → ``done``.

    Returns ``{"file_path", "branch", "pr_url", "ok", "reason", "disposition"}``
    where ``disposition`` ∈ {``done``, ``exists``, ``deferred-cancelled``}.
    Never raises — best-effort; any failure yields ``deferred-cancelled``.

    **Idempotency keys on ``session_id`` (PROJ-039/T-039).** The result filename
    and branch carry a short session-id suffix, so the existence/no-op checks are
    per-session: a *distinct* session always authors its own result (a re-set-up
    session on the same ``date``+``slug`` after a crash no longer sees the prior
    attempt's file and silently skip), while a *same-sid* re-run resolves to the
    same filename and stays idempotent (the T-035 recovery behaviour).
    """
    date = date or _now_date()
    slug = work_item.replace("/", "-").replace(" ", "-").lower()
    sid = (session_id or "nosid")[:8]
    filename = f"session-{date}-{slug}-{sid}.md"
    rel_path = f"results/{filename}"
    branch_name = f"session-result/{date}-{slug}-{sid}"

    result = {"file_path": rel_path, "branch": branch_name, "pr_url": "",
              "ok": False, "reason": "", "disposition": "deferred-cancelled"}

    worktree: Optional[str] = None
    try:
        # Refresh origin so the idempotency checks + base ref are current (best-effort).
        _git(repo_dir, "fetch", "origin", base_branch)
        base_ref = _resolve_base_ref(repo_dir, base_branch)
        if base_ref is None:
            result["reason"] = f"base branch '{base_branch}' not found"
            return result

        # Idempotency — already landed, or an open PR already carries it.
        if _result_on_base(repo_dir, base_ref, rel_path):
            result.update(ok=True, disposition="exists",
                          reason="result already on base branch")
            return result
        if raise_pr and _open_result_pr_exists(repo_dir, branch_name):
            result.update(ok=True, disposition="exists",
                          reason="open result PR already exists")
            return result

        # Author in an isolated worktree off base — the live tree/branch is untouched.
        _git(repo_dir, "branch", "-D", branch_name)  # drop any stale local branch
        worktree = tempfile.mkdtemp(prefix="session-result-")
        add = _git(repo_dir, "worktree", "add", "-b", branch_name, worktree, base_ref)
        if add.returncode != 0:
            result["reason"] = f"worktree add failed: {add.stderr.strip()}"
            return result

        (Path(worktree) / "results").mkdir(parents=True, exist_ok=True)
        (Path(worktree) / rel_path).write_text(result_text, encoding="utf-8")
        _git(worktree, "add", rel_path)
        commit = _git(worktree, "commit", "-m",
                      f"chore(session-result): {work_item} ({date}) [{session_id[:8]}]")
        out = (commit.stdout + commit.stderr).lower()
        if commit.returncode != 0 and "nothing to commit" not in out:
            result["reason"] = f"commit failed: {commit.stderr.strip()}"
            return result

        push = _git(worktree, "push", "--force-with-lease", "origin", branch_name)
        if push.returncode != 0:
            result["reason"] = f"push failed: {push.stderr.strip()}"
            return result

        if raise_pr:
            pr = subprocess.run(
                ["gh", "pr", "create", "--base", base_branch, "--head", branch_name,
                 "--title", f"Session result: {work_item} ({date})",
                 "--body", _result_pr_body(session_id, work_item, date)],
                capture_output=True, text=True, cwd=worktree, check=False,
            )
            result["pr_url"] = pr.stdout.strip()
            if pr.returncode != 0:
                # Commit + push landed but the PR did not — not fully done; a clean
                # re-run (no open PR, file not on base) will retry the PR.
                result["reason"] = f"PR creation failed: {pr.stderr.strip()}"
                return result

        result.update(ok=True, disposition="done")
    except Exception as exc:  # never break teardown
        result["reason"] = str(exc)
    finally:
        if worktree:
            _git(repo_dir, "worktree", "remove", "--force", worktree)
            shutil.rmtree(worktree, ignore_errors=True)
    return result


def author_home_result(
    session_point: dict,
    *,
    session_id: str,
    repo_dir: str,
    date: Optional[str] = None,
    raise_pr: bool = True,
) -> dict:
    """Render + commit the home-repo result for ``session_point`` (T-035 step 7).

    Thin composition of :func:`generate_session_result` + :func:`commit_home_result`
    so the hook's migrated branch is a one-line call. Returns the
    :func:`commit_home_result` dict.
    """
    date = date or _now_date()
    work_item = session_point.get("work_item", "unknown")
    text = generate_session_result(session_point, date=date)
    return commit_home_result(
        text, session_id=session_id, work_item=work_item, date=date,
        repo_dir=repo_dir, raise_pr=raise_pr,
    )


# ---------------------------------------------------------------------------
# Trainee session PR (PROJ-011/T-021 R-3, CC-351)
# ---------------------------------------------------------------------------
#
# A trainee's LLM never runs git ceremony. So — unlike the migrated-home path,
# which authors an isolated *result file* off main and leaves the session's own
# branch alone — the trainee finalise commits the LIVE working tree to the session
# branch, pushes it, and opens the PR **for that branch** to main. The PR IS the
# trainee-visible session artefact; the next session's review ritual reads it,
# walks the checklist, and merges (training-replatform-plan §1.2/§2.1).

# Fixed review checklist the NEXT session's PR-review ritual walks (brief R-3,
# resolved 2026-07-04: PR body = substrate summary + this checklist).
_TRAINEE_REVIEW_CHECKLIST = (
    "## Review checklist (next session's ritual)\n\n"
    "- [ ] Session summary above read\n"
    "- [ ] Files changed reviewed\n"
    "- [ ] Progress entries look right\n"
    "- [ ] Merge this PR, then start the new session on a clean `main`\n"
)


def build_trainee_pr_body(
    session_point: dict,
    *,
    brief_id: Optional[str],
    date: str,
    session_id: str,
    summary_override: Optional[str] = None,
) -> str:
    """Compose the trainee session PR body: the **substrate session summary** plus the
    fixed **review checklist** (brief R-3 — *both*).

    Summary source with the T-047 raw-response fallback: prefer an explicit
    ``summary_override`` (the hook passes the last-assistant turn it already scraped),
    else the session point's ``response.text``, else a clear placeholder — never an
    empty body.
    """
    response = session_point.get("response") or {}
    summary = (summary_override or "").strip() or (response.get("text") or "").strip()
    if not summary:
        summary = ("_(No session summary was captured — see the session point "
                   "`response` in the substrate, or the transcript.)_")
    ref = brief_id or session_point.get("work_item") or "(unknown brief)"
    return (
        f"**Brief:** `{ref}` · **Session:** `{session_id[:12]}` · **Date:** {date}\n\n"
        f"Auto-opened by the trainee SessionEnd finalise hook (PROJ-011/T-021 R-3) — "
        f"the trainee runs no git.\n\n"
        f"## Session summary\n\n"
        f"{summary}\n\n"
        f"---\n\n"
        f"{_TRAINEE_REVIEW_CHECKLIST}"
    )


def _open_pr_for_branch(repo_dir: str, branch_name: str) -> bool:
    """True if an open PR already carries ``branch_name`` (best-effort via gh)."""
    r = subprocess.run(
        ["gh", "pr", "list", "--head", branch_name, "--state", "open",
         "--json", "number", "-q", ".[].number"],
        capture_output=True, text=True, cwd=repo_dir, check=False,
    )
    return r.returncode == 0 and bool(r.stdout.strip())


def author_trainee_session_pr(
    session_point: dict,
    *,
    session_id: str,
    repo_dir: str,
    brief_id: Optional[str] = None,
    branch: Optional[str] = None,
    date: Optional[str] = None,
    summary_override: Optional[str] = None,
    raise_pr: bool = True,
    base_branch: str = "main",
) -> dict:
    """Commit the trainee's working tree to the session branch, push it, and open a
    PR to ``base_branch`` titled from the brief + date.

    Returns ``{"branch", "pr_url", "ok", "reason", "disposition", "committed"}`` where
    ``disposition`` ∈ {``done``, ``exists``, ``noop``, ``deferred-cancelled``}:

      * ``noop`` — the clone is on ``base_branch`` / no session branch to PR.
      * ``exists`` — an open PR already carries this branch (idempotent re-run).
      * ``done`` — committed (if there were changes) + pushed + PR raised.
      * ``deferred-cancelled`` — a step failed; best-effort, never raises.

    Auth degrades gracefully (brief R-3): if ``gh`` is unavailable the commit + push
    still land and the result flags ``pr_url=""`` with a reason — the caller reports
    "committed + pushed only".
    """
    date = date or _now_date()
    branch = branch or _git(repo_dir, "branch", "--show-current").stdout.strip()
    result = {"branch": branch, "pr_url": "", "ok": False, "reason": "",
              "disposition": "deferred-cancelled", "committed": False}

    if not branch or branch == base_branch:
        result.update(ok=True, disposition="noop",
                      reason=f"clone on '{branch or 'detached'}' — no session branch to PR")
        return result

    try:
        # 1. Commit the live working tree to the session branch (trainee ran no git).
        #    .workspace/ etc. are gitignored, so `-A` picks up only real work.
        _git(repo_dir, "add", "-A")
        has_staged = _git(repo_dir, "diff", "--cached", "--quiet").returncode != 0
        if has_staged:
            commit = _git(
                repo_dir, "commit", "-m",
                f"session({date}): {brief_id or session_point.get('work_item', 'work')} "
                f"[{session_id[:8]}]",
            )
            out = (commit.stdout + commit.stderr).lower()
            if commit.returncode != 0 and "nothing to commit" not in out:
                result["reason"] = f"commit failed: {commit.stderr.strip()}"
                return result
            result["committed"] = True

        # Nothing to PR if the branch has no commits ahead of the base.
        _git(repo_dir, "fetch", "origin", base_branch)
        base_ref = _resolve_base_ref(repo_dir, base_branch) or base_branch
        ahead = _git(repo_dir, "rev-list", "--count", f"{base_ref}..{branch}").stdout.strip()
        if ahead in ("", "0"):
            result.update(ok=True, disposition="noop",
                          reason="session branch has no commits ahead of base — nothing to PR")
            return result

        # Idempotent: an open PR already carries this branch.
        if raise_pr and _open_pr_for_branch(repo_dir, branch):
            result.update(ok=True, disposition="exists",
                          reason="open PR already carries this session branch")
            return result

        # 2. Push the session branch.
        push = _git(repo_dir, "push", "-u", "origin", branch)
        if push.returncode != 0:
            result["reason"] = f"push failed: {push.stderr.strip()}"
            return result

        # 3. Open the PR (graceful degrade if gh is unavailable / unauthed).
        if raise_pr:
            title = f"Trainee session {date} — {brief_id or session_point.get('work_item', 'work')}"
            body = build_trainee_pr_body(
                session_point, brief_id=brief_id, date=date,
                session_id=session_id, summary_override=summary_override,
            )
            pr = subprocess.run(
                ["gh", "pr", "create", "--base", base_branch, "--head", branch,
                 "--title", title, "--body", body],
                capture_output=True, text=True, cwd=repo_dir, check=False,
            )
            result["pr_url"] = pr.stdout.strip()
            if pr.returncode != 0:
                # Committed + pushed landed; PR did not (gh missing/unauthed). The
                # work is safe on the remote branch — degrade gracefully.
                result.update(ok=True, disposition="done",
                              reason=f"committed + pushed only (PR not raised: "
                                     f"{pr.stderr.strip() or 'gh unavailable'})")
                return result

        result.update(ok=True, disposition="done",
                      reason="committed + pushed + PR raised")
    except Exception as exc:  # never break teardown
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
