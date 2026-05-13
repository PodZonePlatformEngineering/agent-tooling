#!/usr/bin/env python3
"""
notify-pr.py — PostToolUse hook.

Fires after Bash tool calls. Detects 'gh pr create' in the command and extracts
the PR URL from the output, then sends a Telegram notification via telegram-notify.py.

Input: JSON on stdin (Claude Code PostToolUse hook format).
Exit: always 0 (non-blocking).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(f"[notify-pr] {msg}", file=sys.stderr)


def call_telegram_notify(message: str, cwd: str) -> None:
    notify_script = Path(cwd) / ".claude" / "hooks" / "telegram-notify.py"
    if not notify_script.exists():
        # Try CLAUDE_PROJECT_DIR
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        if project_dir:
            notify_script = Path(project_dir) / ".claude" / "hooks" / "telegram-notify.py"
    if not notify_script.exists():
        log(f"telegram-notify.py not found; skipping")
        return
    try:
        subprocess.run(
            [sys.executable, str(notify_script), message],
            timeout=25,
            capture_output=True,
        )
    except Exception as exc:
        log(f"telegram-notify call failed: {exc}")


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        hook_data = json.loads(raw)
    except Exception as exc:
        log(f"Could not parse stdin: {exc}")
        sys.exit(0)

    tool_name = hook_data.get("tool_name", "")
    if tool_name != "Bash":
        sys.exit(0)

    tool_input = hook_data.get("tool_input", {})
    command = tool_input.get("command", "")
    if "gh pr create" not in command:
        sys.exit(0)

    # Extract PR URL from tool output
    tool_response = hook_data.get("tool_response", {})
    output = tool_response.get("output", "") or ""

    url_match = re.search(r"https://github\.com/\S+/pull/\d+", output)
    if not url_match:
        log("No PR URL found in gh output; skipping notification")
        sys.exit(0)

    pr_url = url_match.group(0)

    # Extract repo and PR number for short form
    parts_match = re.search(r"https://github\.com/[^/]+/([^/]+)/pull/(\d+)", pr_url)
    if parts_match:
        repo = parts_match.group(1)
        pr_num = parts_match.group(2)
        message = f"⬆️ PR ready for review: {repo}#{pr_num} — {pr_url}"
    else:
        message = f"⬆️ PR ready for review: {pr_url}"

    log(f"Notifying: {message}")
    cwd = hook_data.get("cwd", os.environ.get("CLAUDE_PROJECT_DIR", "."))
    call_telegram_notify(message, cwd)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"unhandled error: {exc}")
    sys.exit(0)