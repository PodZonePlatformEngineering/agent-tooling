#!/usr/bin/env bash
# Patch payload on an existing Qdrant point without touching the vector.
# Usage: patch-qdrant-payload.sh <collection> <point_id> <payload_json>
# Auth: PODZONE_QDRANT_APIKEY
set -euo pipefail

COLLECTION="${1:?Usage: patch-qdrant-payload.sh <collection> <point_id> <payload_json>}"
POINT_ID="${2:?missing point_id}"
PAYLOAD_JSON="${3:?missing payload_json}"

QDRANT_URL="${AGENTSONLY_QDRANT_URL:-http://qdrant.agenticflows.co.uk:8080}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"

curl -sf -X POST "${QDRANT_URL}/collections/${COLLECTION}/points/payload" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d "{\"payload\": ${PAYLOAD_JSON}, \"points\": [\"${POINT_ID}\"]}"
