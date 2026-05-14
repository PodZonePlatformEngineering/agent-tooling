#!/usr/bin/env bash
# Send a Telegram message.
# Usage: send-telegram-message.sh <chat_id> <text>
# Auth: PODZONE_TELEGRAM_TEST_BOT (testing) or PODZONE_CLOUD_BOT_TOKEN (production)
# Test bot: @podzone_test_bot — token in secretctl as 'podzone_telegram_test_bot'
set -euo pipefail

CHAT_ID="${1:?Usage: send-telegram-message.sh <chat_id> <text>}"
TEXT="${2:?missing text}"

# Prefer test bot token if set; fall back to production bot
TOKEN="${PODZONE_TELEGRAM_TEST_BOT:-${PODZONE_CLOUD_BOT_TOKEN:?neither PODZONE_TELEGRAM_TEST_BOT nor PODZONE_CLOUD_BOT_TOKEN set}}"

curl -sf -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  -H "Content-Type: application/json" \
  -d "{\"chat_id\": \"${CHAT_ID}\", \"text\": $(printf '%s' "${TEXT}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}"
