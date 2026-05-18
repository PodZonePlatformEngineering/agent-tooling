#!/usr/bin/env bash
# Create the secrets collection on agentsonly Qdrant.
# Idempotent: safe to re-run.
# Auth: PODZONE_QDRANT_APIKEY
set -euo pipefail

QDRANT_URL="${AGENTSONLY_QDRANT_URL:-http://qdrant.agenticflows.co.uk:8080}"
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
