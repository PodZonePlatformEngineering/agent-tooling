#!/usr/bin/env bash
# Patch a single named vector on an existing Qdrant point WITHOUT nulling the
# other named vectors (PROJ-039 IMPL-1 / SD-3-001).
#
# Uses the Qdrant `update-vectors` endpoint (PUT …/points/vectors), which
# replaces only the named vectors supplied and preserves the rest of the
# point's vectors and its whole payload. This is the session-end `response`
# vector patch path: a full upsert (add-qdrant-point.sh) would silently null
# the `brief` vector set at creation.
#
# Usage: update-vectors.sh <collection> <point_id> <vector_json>
#   <vector_json> is the named-vector map, e.g. '{"response": [0.1, 0.2, ...]}'
# Auth: PODZONE_QDRANT_APIKEY
set -euo pipefail

COLLECTION="${1:?Usage: update-vectors.sh <collection> <point_id> <vector_json>}"
POINT_ID="${2:?missing point_id}"
VECTOR_JSON="${3:?missing vector_json (named-vector map, e.g. {\"response\": [...]})}"

QDRANT_URL="${QDRANT_URL:-${AGENTSONLY_QDRANT_URL:-https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io}}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"

curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}/points/vectors" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d "{\"points\": [{\"id\": \"${POINT_ID}\", \"vector\": ${VECTOR_JSON}}]}"
