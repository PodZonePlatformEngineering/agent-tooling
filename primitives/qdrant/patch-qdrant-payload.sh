#!/usr/bin/env bash
# Patch the payload of an existing Qdrant point
# Usage: patch-qdrant-payload.sh <collection> <id> <payload_json>
# Auth:  PODZONE_QDRANT_APIKEY
set -euo pipefail

# --- parameters ---
COLLECTION="${1:?Usage: patch-qdrant-payload.sh <collection> <id> <payload_json>}"
ID="${2:?missing id}"
PAYLOAD_JSON="${3:?missing payload_json}"
QDRANT_URL="${QDRANT_URL:-https://qdrant.agenticflows.co.uk}"

# --- auth check ---
: "${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY is required}"

# --- implementation ---
echo "STUB: not yet implemented"
exit 0
