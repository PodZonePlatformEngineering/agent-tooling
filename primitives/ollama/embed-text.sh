#!/usr/bin/env bash
# Embed text using nomic-embed-text via Ollama. Outputs the embedding JSON array.
# Usage: embed-text.sh <text> [ollama_host]
# Auth: none
set -euo pipefail

TEXT="${1:?Usage: embed-text.sh <text> [ollama_host]}"
OLLAMA_HOST="${2:-${OLLAMA_HOST:-http://localhost:11434}}"

curl -sf -X POST "${OLLAMA_HOST}/api/embeddings" \
  -H "Content-Type: application/json" \
  -d "{\"model\": \"nomic-embed-text\", \"prompt\": $(printf '%s' "${TEXT}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}" \
  | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["embedding"]))'
