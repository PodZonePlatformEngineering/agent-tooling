#!/usr/bin/env python3
"""
SessionStart hook — query Qdrant for agent identity + active tasks; inject as context.
PROJ-031/T-003. Non-blocking: always exits 0. Output goes to Claude's context window.
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

CLOUD_QDRANT_URL = "https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io"
AGENTSONLY_QDRANT_URL = "http://qdrant.agenticflows.co.uk:8080"
CLOUD_COLLECTIONS = {"sessions", "tasks", "task_events", "prompt_logs", "one_shots"}

COLLECTION_TASKS = "tasks"
COLLECTION_SESSIONS = "sessions"


def get_qdrant_url(collection: str) -> str:
    return CLOUD_QDRANT_URL if collection in CLOUD_COLLECTIONS else AGENTSONLY_QDRANT_URL


def get_qdrant_headers(collection: str) -> dict:
    if collection in CLOUD_COLLECTIONS:
        api_key = os.environ.get("PODZONE_QDRANT_APIKEY", "")
        return {"api-key": api_key} if api_key else {}
    return {}

STATUS_ORDER = {"in-progress": 0, "ready": 1, "blocked": 2, "paused": 3}


def qdrant_filter(collection: str, must: list, limit: int = 10) -> list:
    try:
        import requests
        r = requests.post(
            f"{get_qdrant_url(collection)}/collections/{collection}/points/scroll",
            headers=get_qdrant_headers(collection),
            json={"filter": {"must": must}, "limit": limit, "with_payload": True},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("result", {}).get("points", [])
    except Exception as e:
        print(f"[session-context] qdrant query error: {e}", file=sys.stderr)
        return []


def upsert_session(session_id: str, payload: dict) -> None:
    try:
        import requests
        # Create session record (vector not required — use zero vector)
        r = requests.put(
            f"{get_qdrant_url(COLLECTION_SESSIONS)}/collections/{COLLECTION_SESSIONS}/points",
            headers=get_qdrant_headers(COLLECTION_SESSIONS),
            json={"points": [{"id": session_id, "vector": [0.0] * 768, "payload": payload}]},
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        print(f"[session-context] session upsert error: {e}", file=sys.stderr)


_ROLE_CLASS_MAP = {
    "team-lead": "team-lead",
    "coder": "coder",
    "cluster-operator": "cluster-operator",
    "archivist": "archivist",
}


def _role_from_class(role_class: str) -> str:
    for token, label in _ROLE_CLASS_MAP.items():
        if token in role_class:
            return label
    return "unknown"


def _resolve_from_workspace_settings(cwd: str) -> tuple[str, str, str]:
    """Step 2b — scan *.code-workspace files for identity_file reference.

    When multiple workspace files match (same agent, different scope), prefer the
    one whose scope appears in the cwd path.
    """
    import re

    ws_dir = Path(cwd) / "workspaces"
    if not ws_dir.exists():
        return "unknown", "unknown", "unknown"

    candidates: list[tuple[int, str, str, str]] = []  # (score, agent, role, scope)

    for ws_path in ws_dir.glob("*.code-workspace"):
        try:
            ws = json.loads(ws_path.read_text())
            instructions = ws.get("settings", {}).get("claude.projectInstructions", "")
            m = re.search(r"identity_file:\s*(\S+)", instructions)
            if not m:
                continue
            # Paths are relative to workspace parent (e.g. "podzoneAgentTeam/workspaces/identity/...")
            rel = m.group(1)
            identity_path = Path(cwd).parent / rel
            if not identity_path.exists():
                identity_path = Path(cwd) / rel
            if not identity_path.exists():
                continue
            # Parse flat YAML via regex (avoids yaml dependency)
            text = identity_path.read_text()
            def _field(key: str) -> str:
                fm = re.search(rf"^{key}:\s*(.+)$", text, re.MULTILINE)
                return fm.group(1).strip() if fm else "unknown"
            agent = _field("agent").lower()
            scope = _field("scope")
            role = _role_from_class(_field("role_class"))
            if agent == "unknown":
                continue
            # Prefer workspace whose scope appears in the cwd path
            score = 1 if scope in cwd else 0
            candidates.append((score, agent, role, scope))
        except Exception:
            continue

    if not candidates:
        return "unknown", "unknown", "unknown"
    candidates.sort(key=lambda c: c[0], reverse=True)
    _, agent, role, scope = candidates[0]
    return agent, role, scope


def resolve_agent(cwd: str) -> tuple[str, str, str]:
    """Returns (agent_name, role, scope).

    Resolution chain:
    1. Parse agent name from worktree cwd path (/sessions/{agent}-{date}-{slug}/)
    2. Read CLAUDE_AGENT environment variable
    2b. Scan workspaces/*.code-workspace for identity_file reference
    3. agent-registry.json (no-op at runtime; used by setup-current-agent.sh)
    4. Read .claude/current-agent as final fallback
    """
    import re
    import os

    # Step 1 — Worktree path parse
    m = re.search(r'/sessions/([a-z]+)-\d{4}-', cwd)
    if m:
        agent_name = m.group(1)
        # Look up role/scope from registry if available
        for candidate in [Path(cwd), Path(cwd).parent]:
            rp = candidate / "workspaces" / "identity" / "agent-registry.json"
            if rp.exists():
                try:
                    registry = json.loads(rp.read_text())
                    for entry in registry.values():
                        if entry.get("agent") == agent_name:
                            return (
                                agent_name,
                                entry.get("role", "unknown"),
                                entry.get("scope", "unknown"),
                            )
                except Exception:
                    pass
        return agent_name, "unknown", "unknown"

    # Step 2 — CLAUDE_AGENT environment variable
    env_agent = os.environ.get("CLAUDE_AGENT")
    if env_agent:
        return env_agent, "unknown", "unknown"

    # Step 2b — workspace settings → identity_file
    agent, role, scope = _resolve_from_workspace_settings(cwd)
    if agent != "unknown":
        return agent, role, scope

    # Step 3 — agent-registry.json (reference file; no workspace detection here)
    # Intentionally a no-op at runtime — populated by setup-current-agent.sh.

    # Step 4 — .claude/current-agent fallback
    agent_file = Path(cwd) / ".claude" / "current-agent"
    if agent_file.exists():
        try:
            data = json.loads(agent_file.read_text())
            return (
                data.get("agent", "unknown"),
                data.get("role", "unknown"),
                data.get("scope", "unknown"),
            )
        except Exception:
            pass

    return "unknown", "unknown", "unknown"


ROLE_LABELS = {
    "team-lead": "Team Lead",
    "coder": "Coder",
    "cluster-operator": "Cluster Operator",
    "archivist": "Archivist",
    "hr": "HR",
}

STATUS_EMOJI = {
    "in-progress": "🔄",
    "ready": "🚀",
    "blocked": "⚠️",
    "paused": "⏸",
}


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception as e:
        print(f"[session-context] failed to parse stdin: {e}", file=sys.stderr)
        sys.exit(0)

    session_id = data.get("session_id", str(uuid.uuid4()))
    cwd = data.get("cwd", "")
    transcript_path = data.get("transcript_path", "")
    now = datetime.now(timezone.utc).isoformat()

    agent, role, scope = resolve_agent(cwd)

    # Register session record
    upsert_session(
        session_id,
        {
            "session_id": session_id,
            "agent": agent,
            "role": role,
            "scope": scope,
            "start_ts": now,
            "status": "active",
            "cwd": cwd,
            "transcript_path": transcript_path,
        },
    )

    if agent == "unknown":
        # Graceful degradation — no context injection if identity unknown
        sys.exit(0)

    # Query active tasks for this agent
    active_statuses = list(STATUS_ORDER.keys())
    points = qdrant_filter(
        COLLECTION_TASKS,
        must=[
            {"key": "agent", "match": {"value": agent}},
            {"key": "status", "match": {"any": active_statuses}},
        ],
        limit=15,
    )

    # Sort by status priority
    points.sort(key=lambda p: STATUS_ORDER.get(p["payload"].get("status", ""), 99))

    if not points:
        sys.exit(0)

    # Build context lines
    task_lines = []
    blockers = []
    for p in points[:10]:
        pl = p["payload"]
        status = pl.get("status", "unknown")
        emoji = STATUS_EMOJI.get(status, "•")
        slug = pl.get("task_slug") or pl.get("summary", "")[:40]
        cc = pl.get("cc_number", "")
        cc_str = f" [{cc}]" if cc else ""
        proj = pl.get("project_slug", "")
        label = f"{proj}:{slug}" if proj else slug
        task_lines.append(f"  {emoji} {label}{cc_str}")

        if status == "blocked":
            blockers.append(f"  {label}")

    role_label = ROLE_LABELS.get(role, role)
    context_parts = [
        f"Agent: {agent} | Role: {role_label} | Scope: {scope}",
        f"Active tasks ({len(task_lines)}):",
    ] + task_lines

    if blockers:
        context_parts.append("Blockers:")
        context_parts.extend(blockers)

    context_text = "\n".join(context_parts)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context_text,
        }
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[session-context] unhandled error: {e}", file=sys.stderr)
        sys.exit(0)
