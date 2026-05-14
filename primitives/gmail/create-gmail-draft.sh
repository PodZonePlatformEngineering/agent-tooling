#!/usr/bin/env bash
# Create a Gmail draft (optionally with attachment).
# Usage: create-gmail-draft.sh --to <email> --subject <subject> --body <body> [--attachment <path>]
# Auth: Gmail OAuth token at ~/.config/podzone/gmail-token.json
#       Run the OAuth consent flow once if token is missing.
# Credentials: ~/.config/podzone/gmail-client-secret.json
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRIVER="${SCRIPT_DIR}/../../tools/gmail/create_draft.py"
GMAIL_TOKEN="${GMAIL_TOKEN_FILE:-${HOME}/.config/podzone/gmail-token.json}"

if [ ! -f "${GMAIL_TOKEN}" ]; then
  echo "ERROR: Gmail token not found at ${GMAIL_TOKEN}" >&2
  echo "Run the OAuth consent flow once to generate it." >&2
  exit 1
fi

python3 "${DRIVER}" "$@"
