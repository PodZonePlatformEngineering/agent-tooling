#!/usr/bin/env python3
"""
telegram-notify.py — Telegram notification helper.

Usage: python telegram-notify.py <message>

Called from SessionEnd hook (ingest-transcript.py), notify-pr.py PostToolUse hook,
and the session-end skill (for blocker notifications).

Token retrieval: calls primitives/getSecret.sh to fetch the bot token from the
Qdrant secrets collection. Requires PODZONE_QDRANT_APIKEY in env.
Best-effort: always exits 0; logs and skips on failure.

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

CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# getSecret.sh lives next to this file's parent in primitives/
GET_SECRET = Path(__file__).resolve().parent.parent / "primitives" / "getSecret.sh"


def log(msg: str) -> None:
    print(f"[telegram-notify] {msg}", file=sys.stderr)


def fetch_token() -> str:
    """Fetch the bot token from Qdrant via getSecret.sh. Empty string on failure."""
    if not GET_SECRET.exists():
        log(f"getSecret.sh not found at {GET_SECRET}")
        return ""
    try:
        result = subprocess.run(
            ["bash", str(GET_SECRET), "podzone_cloud_bot_token"],
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        log("getSecret.sh timed out")
        return ""
    except Exception as exc:
        log(f"getSecret.sh exec failed: {exc}")
        return ""

    if result.returncode != 0:
        err = result.stderr.decode(errors="replace").strip()[:200]
        log(f"getSecret.sh failed (exit {result.returncode}): {err}")
        return ""
    return result.stdout.decode(errors="replace").strip()


def send(message: str, token: str) -> None:
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

    token = os.environ.get("PODZONE_CLOUD_BOT_TOKEN") or fetch_token()
    if not token:
        log("no token available; skipping notification")
        sys.exit(0)

    send(message, token)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"unhandled error: {exc}")
    sys.exit(0)
