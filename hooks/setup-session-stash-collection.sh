#!/usr/bin/env bash
# Create the general-purpose `session_stash` collection on cloud Qdrant
# (PROJ-039/T-255, design doc t254-session-stash-design.md §2). Sibling to
# setup-briefs-collection.sh / setup-session-results-collection.sh.
#
# A session-stash entry is transient "resume here" scratch, replaced (never
# appended to) on every push, keyed by brief_id — payload-only, no named
# vector (design doc §2: this is exact-key lookup, not a semantic-search
# corpus). Collection is created with an EMPTY named-vector map, matching the
# `training_token_registry` payload-only precedent
# (hooks/setup-training-collections.sh).
#
# Idempotent: safe to re-run. Creates, in this order (the order is load-bearing,
# DT-015 / F-2-004 — index BEFORE any point is ever ingested so the filterable
# HNSW index incorporates filter-aware edges):
#   1. the collection with an empty named-vector map (payload-only).
#   2. the point_type keyword index.
#   3. the read-filter indexes: brief_id, trigger, status (keyword), pushed_at (datetime).
#
# This script never ingests a point, so "index before ingest" holds for the
# whole collection by construction.
#
# Auth: PODZONE_QDRANT_APIKEY (read from env; if absent, wrap the call in
#       `mcp__secrets__secret_run -k podzone_qdrant_apikey` — never assume the
#       key is inherited, C-004 / PROJ-033/T-016).
set -euo pipefail

QDRANT_URL="${QDRANT_URL:-${AGENTSONLY_QDRANT_URL:-https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io}}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"
COLLECTION="${1:-session_stash}"

echo "==> Creating collection ${COLLECTION} (payload-only, no named vector)..."
curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d '{"vectors": {}}' || echo "(collection may already exist — continuing)"
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
for FIELD in brief_id trigger status; do
  curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}/index" \
    -H "Content-Type: application/json" \
    -H "api-key: ${API_KEY}" \
    -d "{\"field_name\": \"${FIELD}\", \"field_schema\": \"keyword\"}" \
    || true
done
echo

echo "==> Creating pushed_at datetime index..."
curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}/index" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d '{"field_name": "pushed_at", "field_schema": "datetime"}' \
  || true
echo

echo "==> Setup complete: ${COLLECTION} ready."
