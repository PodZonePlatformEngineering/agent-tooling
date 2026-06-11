#!/usr/bin/env bash
# Create claude_session_telemetry collection with named vectors + payload indexes.
# Idempotent: safe to re-run.
set -euo pipefail

QDRANT_URL="${QDRANT_URL:-${AGENTSONLY_QDRANT_URL:-https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io}}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"
COLLECTION="claude_session_telemetry"

echo "==> Creating collection ${COLLECTION}..."
curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d '{
    "vectors": {
      "intent_vector":   {"size": 768, "distance": "Cosine"},
      "action_vector":   {"size": 768, "distance": "Cosine"},
      "response_vector": {"size": 768, "distance": "Cosine"}
    }
  }' || echo "(collection may already exist — continuing)"

echo "==> Creating payload indexes..."
for FIELD in session_id event_type; do
  curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}/index" \
    -H "Content-Type: application/json" \
    -H "api-key: ${API_KEY}" \
    -d "{\"field_name\": \"${FIELD}\", \"field_schema\": \"keyword\"}" \
    || true
done

# datetime index enables order_by timestamp (required for session-report.sh no-arg resolution)
curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}/index" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d '{"field_name": "timestamp", "field_schema": "datetime"}' \
  || true

echo "==> Setup complete."
