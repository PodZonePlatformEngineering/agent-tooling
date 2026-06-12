#!/usr/bin/env bash
# Create the unified `session_substrate` collection on cloud Qdrant
# (PROJ-039 § 2.1). Sibling to setup-collection.sh (which creates the CST
# observability collection, retained unchanged).
#
# Idempotent: safe to re-run. Creates, in this order (the order is load-bearing,
# DT-015 / F-2-004):
#   1. the collection with named vectors `brief` + `response` (768 cosine);
#      `rollup` carries no vector (Q-002).
#   2. the `point_type` keyword payload index — BEFORE any point is ever
#      ingested, so the filterable HNSW index incorporates filter-aware edges.
#   3. the read-filter keyword indexes: session_id, agent, work_item, status.
#
# This script never ingests a point, so the "index before ingest" invariant
# holds for the whole substrate by construction.
#
# Auth: PODZONE_QDRANT_APIKEY (read from env; if absent, wrap the call in
#       `mcp__secrets__secret_run -k podzone_qdrant_apikey` — never assume the
#       key is inherited, C-004 / PROJ-033/T-016).
set -euo pipefail

QDRANT_URL="${QDRANT_URL:-${AGENTSONLY_QDRANT_URL:-https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io}}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"
COLLECTION="session_substrate"

echo "==> Creating collection ${COLLECTION} (named vectors brief+response, 768 cosine)..."
curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d '{
    "vectors": {
      "brief":    {"size": 768, "distance": "Cosine"},
      "response": {"size": 768, "distance": "Cosine"}
    }
  }' || echo "(collection may already exist — continuing)"
echo

# point_type FIRST — before any ingest (F-2-004, asserted by DT-015).
echo "==> Creating point_type keyword index (before any ingest)..."
curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}/index" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d '{"field_name": "point_type", "field_schema": "keyword"}' \
  || true
echo

echo "==> Creating read-filter keyword indexes..."
for FIELD in session_id agent work_item status; do
  curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}/index" \
    -H "Content-Type: application/json" \
    -H "api-key: ${API_KEY}" \
    -d "{\"field_name\": \"${FIELD}\", \"field_schema\": \"keyword\"}" \
    || true
done
echo

echo "==> Setup complete: ${COLLECTION} ready."
