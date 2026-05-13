#!/usr/bin/env bash
# Create a Gmail draft via the Gmail API
# Usage: create-gmail-draft.sh <to> <subject> <body> [attachment_path]
# Auth:  OAuth token file at ~/.config/podzone/gmail-token.json
set -euo pipefail

# --- parameters ---
TO="${1:?Usage: create-gmail-draft.sh <to> <subject> <body> [attachment_path]}"
SUBJECT="${2:?missing subject}"
BODY="${3:?missing body}"
ATTACHMENT_PATH="${4:-}"
GMAIL_TOKEN="${GMAIL_TOKEN_FILE:-$HOME/.config/podzone/gmail-token.json}"

# --- auth check ---
if [[ ! -f "$GMAIL_TOKEN" ]]; then
  echo "ERROR: Gmail token not found at $GMAIL_TOKEN" >&2
  exit 1
fi

# --- implementation ---
echo "STUB: not yet implemented"
exit 0
