#!/usr/bin/env bash
# Semantic search over a Qdrant collection. Embeds the query text via Ollama,
# then calls Qdrant /points/search and returns the top-K matches with payload + score.
#
# Usage: search-qdrant.sh <collection> <query> [limit] [filter_json] [embed_model] [ollama_host]
# Auth:  PODZONE_QDRANT_APIKEY (required)
# Env:   AGENTSONLY_QDRANT_URL (default http://qdrant.agenticflows.co.uk:8080)
#        OLLAMA_HOST           (default http://localhost:11434)
#
# Design notes (for third-party adopters):
#   - The embed model is an explicit argument (default: nomic-embed-text).
#     There is intentionally no per-collection registry inside this primitive;
#     callers that mix collections with different embed models should pass the
#     model explicitly per call. This keeps the primitive thin and matches the
#     rest of the qdrant/* family which is parameterised by collection only.
#   - The query is embedded inline rather than shelling to ollama/embed-text.sh
#     so that the embed model can be overridden without touching that primitive.
set -euo pipefail

COLLECTION="${1:?Usage: search-qdrant.sh <collection> <query> [limit] [filter_json] [embed_model] [ollama_host]}"
QUERY="${2:?missing query}"
LIMIT="${3:-10}"
FILTER_JSON="${4:-}"
EMBED_MODEL="${5:-nomic-embed-text}"
OLLAMA_HOST="${6:-${OLLAMA_HOST:-http://localhost:11434}}"

QDRANT_URL="${AGENTSONLY_QDRANT_URL:-http://qdrant.agenticflows.co.uk:8080}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"

# 1. Embed the query text.
VECTOR_JSON=$(curl -sf -X POST "${OLLAMA_HOST}/api/embeddings" \
  -H "Content-Type: application/json" \
  -d "{\"model\": $(printf '%s' "${EMBED_MODEL}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'), \"prompt\": $(printf '%s' "${QUERY}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}" \
  | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["embedding"]))')

# 2. Search the collection.
BODY="{\"vector\": ${VECTOR_JSON}, \"limit\": ${LIMIT}, \"with_payload\": true, \"with_vector\": false"
[ -n "${FILTER_JSON}" ] && BODY="${BODY}, \"filter\": ${FILTER_JSON}"
BODY="${BODY}}"

curl -sf -X POST "${QDRANT_URL}/collections/${COLLECTION}/points/search" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d "${BODY}"
