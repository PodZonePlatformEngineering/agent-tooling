#!/usr/bin/env bash
# Embed text using a local Ollama model (nomic-embed-text)
# Usage: embed-text.sh <text> [ollama_host]
# Auth:  none
set -euo pipefail

# --- parameters ---
TEXT="${1:?Usage: embed-text.sh <text> [ollama_host]}"
OLLAMA_HOST="${2:-${OLLAMA_HOST:-http://localhost:11434}}"

# --- implementation ---
echo "STUB: not yet implemented"
exit 0
