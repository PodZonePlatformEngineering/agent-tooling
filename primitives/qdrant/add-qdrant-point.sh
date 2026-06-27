#!/usr/bin/env bash
# Upsert a single point into a Qdrant collection.
# Usage: add-qdrant-point.sh <collection> <point_id> <vector_json> <payload_json>
# Auth: PODZONE_QDRANT_APIKEY
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Best-effort runtime logging (PROJ-039/T-029) — writes only to logs/primitives.log,
# never to stdout, so callers that capture this primitive's output are unaffected.
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/../log_primitive.sh" 2>/dev/null || true
command -v log_primitive >/dev/null 2>&1 || log_primitive() { :; }

COLLECTION="${1:?Usage: add-qdrant-point.sh <collection> <point_id> <vector_json> <payload_json>}"
POINT_ID="${2:?missing point_id}"
VECTOR_JSON="${3:?missing vector_json}"
PAYLOAD_JSON="${4:?missing payload_json}"

QDRANT_URL="${QDRANT_URL:-${AGENTSONLY_QDRANT_URL:-https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io}}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"

log_primitive "add-qdrant-point.sh" "upsert point ${POINT_ID} -> collection ${COLLECTION}"
curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}/points" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d "{\"points\": [{\"id\": \"${POINT_ID}\", \"vector\": ${VECTOR_JSON}, \"payload\": ${PAYLOAD_JSON}}]}"
