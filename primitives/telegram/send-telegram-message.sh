#!/usr/bin/env bash
# Send a Telegram message.
# Usage: send-telegram-message.sh <chat_id> <text>
# Auth: bot token retrieved from Qdrant via getSecret.sh.
#   Default: podzone_cloud_bot_token (production bot @podzone_cloud_bot)
#   Override: set TELEGRAM_BOT_SECRET=podzone_telegram_test_bot for the test bot
# Requires PODZONE_QDRANT_APIKEY in env for getSecret.sh to reach Qdrant.
set -euo pipefail

CHAT_ID="${1:?Usage: send-telegram-message.sh <chat_id> <text>}"
TEXT="${2:?missing text}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GET_SECRET="${SCRIPT_DIR}/../getSecret.sh"

SECRET_NAME="${TELEGRAM_BOT_SECRET:-podzone_cloud_bot_token}"

if ! TOKEN=$("${GET_SECRET}" "${SECRET_NAME}"); then
  echo "send-telegram-message: getSecret.sh failed for '${SECRET_NAME}' (Qdrant unreachable or secret missing)" >&2
  exit 1
fi

curl -sf -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  -H "Content-Type: application/json" \
  -d "{\"chat_id\": \"${CHAT_ID}\", \"text\": $(printf '%s' "${TEXT}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}"
