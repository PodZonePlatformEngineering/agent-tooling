#!/usr/bin/env bash
# Upsert a single point into a Qdrant collection.
# Usage: add-qdrant-point.sh <collection> <point_id> <vector_json> <payload_json>
# Auth: PODZONE_QDRANT_APIKEY
set -euo pipefail

COLLECTION="${1:?Usage: add-qdrant-point.sh <collection> <point_id> <vector_json> <payload_json>}"
POINT_ID="${2:?missing point_id}"
VECTOR_JSON="${3:?missing vector_json}"
PAYLOAD_JSON="${4:?missing payload_json}"

QDRANT_URL="${QDRANT_URL:-${AGENTSONLY_QDRANT_URL:-https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io}}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"

curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}/points" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d "{\"points\": [{\"id\": \"${POINT_ID}\", \"vector\": ${VECTOR_JSON}, \"payload\": ${PAYLOAD_JSON}}]}"
