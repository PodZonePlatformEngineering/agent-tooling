#!/usr/bin/env bash
# Create the secrets collection on the configured Qdrant instance.
# Idempotent: safe to re-run.
# Auth: PODZONE_QDRANT_APIKEY
set -euo pipefail

QDRANT_URL="${QDRANT_URL:-${AGENTSONLY_QDRANT_URL:-https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io}}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"
COLLECTION="secrets"

echo "==> Creating collection ${COLLECTION} on ${QDRANT_URL}..."
curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d '{
    "vectors": {
      "size": 4,
      "distance": "Cosine"
    }
  }' || echo "(collection may already exist — continuing)"

echo "==> Creating payload index on 'name'..."
curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}/index" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d '{"field_name": "name", "field_schema": "keyword"}' \
  || true

echo "==> Setup complete."
