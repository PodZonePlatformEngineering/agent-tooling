#!/usr/bin/env bash
# Create the training substrate on cloud Qdrant (PROJ-011/T-031):
#   1. training_briefs             — trainee briefs + trainee→trainer channel
#   2. training_session_telemetry  — trainee telemetry (CST-shaped)
#   3. training_token_registry     — JWT value_exists revocation registry
#                                    (training-team-owned; trainee tokens have
#                                    NO access — delete a point to kill its token)
#
# Sibling to setup-briefs-collection.sh / setup-collection.sh. Schemas:
# collections/training_briefs.yaml, collections/training_session_telemetry.yaml;
# docs/training-collections-schema.md, docs/training-jwt-runbook.md.
#
# Idempotent: safe to re-run. Index order is load-bearing (DT-015 / F-2-004):
# every payload index is created BEFORE any point is ever ingested, so the
# filterable HNSW index incorporates filter-aware edges. This script never
# ingests a point, so that holds by construction.
#
# Auth: PODZONE_QDRANT_APIKEY (the MASTER cluster key — never ships to
#       trainees; if absent, wrap in `mcp__secrets__secret_run -k
#       podzone_qdrant_apikey`, C-004 / PROJ-033/T-016).
set -euo pipefail

QDRANT_URL="${QDRANT_URL:-https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"

create_index() {
  local collection="$1" field="$2" schema="${3:-keyword}"
  curl -sf -X PUT "${QDRANT_URL}/collections/${collection}/index" \
    -H "Content-Type: application/json" \
    -H "api-key: ${API_KEY}" \
    -d "{\"field_name\": \"${field}\", \"field_schema\": \"${schema}\"}" \
    || true
  echo
}

# --- 1. training_briefs -----------------------------------------------------
echo "==> Creating collection training_briefs (named vector brief, 768 cosine)..."
curl -sf -X PUT "${QDRANT_URL}/collections/training_briefs" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d '{
    "vectors": {
      "brief": {"size": 768, "distance": "Cosine"}
    }
  }' || echo "(collection may already exist — continuing)"
echo

echo "==> training_briefs payload indexes (before any ingest)..."
for FIELD in point_type direction brief_id trainee channel status message_type; do
  create_index training_briefs "$FIELD" keyword
done
for FIELD in created_at updated_at; do
  create_index training_briefs "$FIELD" datetime
done

# --- 2. training_session_telemetry ------------------------------------------
echo "==> Creating collection training_session_telemetry (CST-shaped vectors)..."
curl -sf -X PUT "${QDRANT_URL}/collections/training_session_telemetry" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d '{
    "vectors": {
      "intent_vector":   {"size": 768, "distance": "Cosine"},
      "action_vector":   {"size": 768, "distance": "Cosine"},
      "response_vector": {"size": 768, "distance": "Cosine"}
    }
  }' || echo "(collection may already exist — continuing)"
echo

echo "==> training_session_telemetry payload indexes (before any ingest)..."
for FIELD in session_id event_type trainee; do
  create_index training_session_telemetry "$FIELD" keyword
done
create_index training_session_telemetry timestamp datetime

# --- 3. training_token_registry ----------------------------------------------
# Payload-only utility collection (empty named-vector map — points take
# "vector": {}). Holds one point per minted trainee JWT; the token's
# value_exists claim binds to it. Trainee tokens get NO claim on this
# collection, so a trainee can never delete (or even read) a registry point.
echo "==> Creating collection training_token_registry (payload-only)..."
curl -sf -X PUT "${QDRANT_URL}/collections/training_token_registry" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d '{"vectors": {}}' || echo "(collection may already exist — continuing)"
echo

echo "==> training_token_registry payload indexes (before any ingest)..."
for FIELD in token_id trainee active; do
  create_index training_token_registry "$FIELD" keyword
done
create_index training_token_registry minted_at datetime

echo "==> Setup complete: training_briefs, training_session_telemetry, training_token_registry ready."
