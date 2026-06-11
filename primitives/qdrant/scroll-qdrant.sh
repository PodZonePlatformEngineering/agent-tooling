#!/usr/bin/env bash
# Scroll points from a Qdrant collection.
# Usage: scroll-qdrant.sh <collection> [limit] [filter_json]
# Auth: PODZONE_QDRANT_APIKEY
set -euo pipefail

COLLECTION="${1:?Usage: scroll-qdrant.sh <collection> [limit] [filter_json]}"
LIMIT="${2:-10}"
FILTER_JSON="${3:-}"

QDRANT_URL="${QDRANT_URL:-${AGENTSONLY_QDRANT_URL:-https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io}}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"

BODY="{\"limit\": ${LIMIT}, \"with_payload\": true, \"with_vector\": false"
[ -n "${FILTER_JSON}" ] && BODY="${BODY}, \"filter\": ${FILTER_JSON}"
BODY="${BODY}}"

curl -sf -X POST "${QDRANT_URL}/collections/${COLLECTION}/points/scroll" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d "${BODY}"
