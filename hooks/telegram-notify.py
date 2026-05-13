#!/usr/bin/env python3
"""
telegram-notify.py — Telegram notification helper.

Usage: python telegram-notify.py <message>

Called from SessionEnd hook (ingest-transcript.py), notify-pr.py PostToolUse hook,
and the session-end skill (for blocker notifications).

Token injection: re-invokes itself via secretctl to avoid token in shell history.
Best-effort: always exits 0.

Setup: set TELEGRAM_CHAT_ID env var (in settings.json or shell env).
To find your chat ID:
  1. Start a conversation with @podzone_cloud_bot in Telegram
  2. Call: curl https://api.telegram.org/bot<TOKEN>/getUpdates
  3. Copy the chat.id from the response
  4. Add to settings.json env: {"TELEGRAM_CHAT_ID": "<id>"}
"""
import os
import subprocess
import sys
from pathlib import Path

# TODO: set once Martin's chat ID is confirmed via getUpdates
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def log(msg: str) -> None:
    print(f"[telegram-notify] {msg}", file=sys.stderr)


def send_direct(message: str) -> None:
    """Send via Telegram API. Only called when PODZONE_CLOUD_BOT_TOKEN is in env."""
    token = os.environ.get("PODZONE_CLOUD_BOT_TOKEN", "")
    if not token:
        log("PODZONE_CLOUD_BOT_TOKEN not in env after secretctl injection; skipping")
        return
    try:
        import requests

        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
        r.raise_for_status()
        log("sent")
    except Exception as exc:
        log(f"send failed: {exc}")


def main() -> None:
    if len(sys.argv) < 2:
        log("usage: telegram-notify.py <message>")
        sys.exit(0)

    message = " ".join(sys.argv[1:])

    if not CHAT_ID:
        log("TELEGRAM_CHAT_ID not set; skipping notification (see setup instructions above)")
        sys.exit(0)

    # If token already injected by a parent secretctl call, send directly
    if os.environ.get("PODZONE_CLOUD_BOT_TOKEN"):
        send_direct(message)
        sys.exit(0)

    # Re-invoke via secretctl to inject the bot token
    script_path = Path(__file__).resolve()
    try:
        result = subprocess.run(
            [
                "secretctl", "run", "-k", "podzone_cloud_bot_token", "--",
                sys.executable, str(script_path), message,
            ],
            capture_output=True,
            timeout=20,
        )
        if result.returncode != 0:
            log(
                f"secretctl failed (exit {result.returncode}): "
                f"{result.stderr.decode(errors='replace')[:200]}"
            )
        else:
            # Forward stderr from inner invocation (contains our log lines)
            inner_err = result.stderr.decode(errors="replace").strip()
            if inner_err:
                print(inner_err, file=sys.stderr)
    except FileNotFoundError:
        log("secretctl not found; skipping notification")
    except subprocess.TimeoutExpired:
        log("secretctl timed out; skipping notification")
    except Exception as exc:
        log(f"secretctl exec failed: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"unhandled error: {exc}")
    sys.exit(0)