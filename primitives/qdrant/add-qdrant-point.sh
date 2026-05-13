#!/usr/bin/env bash
# Add a single point to a Qdrant collection
# Usage: add-qdrant-point.sh <collection> <id> <vector_json> <payload_json>
# Auth:  PODZONE_QDRANT_APIKEY
set -euo pipefail

# --- parameters ---
COLLECTION="${1:?Usage: add-qdrant-point.sh <collection> <id> <vector_json> <payload_json>}"
ID="${2:?missing id}"
VECTOR_JSON="${3:?missing vector_json}"
PAYLOAD_JSON="${4:?missing payload_json}"
QDRANT_URL="${QDRANT_URL:-https://qdrant.agenticflows.co.uk}"

# --- auth check ---
: "${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY is required}"

# --- implementation ---
echo "STUB: not yet implemented"
exit 0
