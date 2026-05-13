#!/usr/bin/env bash
# Send a message to a Telegram chat via the Bot API
# Usage: send-telegram-message.sh <chat_id> <text>
# Auth:  PODZONE_CLOUD_BOT_TOKEN
set -euo pipefail

# --- parameters ---
CHAT_ID="${1:?Usage: send-telegram-message.sh <chat_id> <text>}"
TEXT="${2:?missing text}"

# --- auth check ---
: "${PODZONE_CLOUD_BOT_TOKEN:?PODZONE_CLOUD_BOT_TOKEN is required}"

# --- implementation ---
echo "STUB: not yet implemented"
exit 0
