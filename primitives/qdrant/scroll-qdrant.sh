#!/usr/bin/env bash
# Scroll (list) points from a Qdrant collection with an optional filter
# Usage: scroll-qdrant.sh <collection> <filter_json> <limit>
# Auth:  PODZONE_QDRANT_APIKEY
set -euo pipefail

# --- parameters ---
COLLECTION="${1:?Usage: scroll-qdrant.sh <collection> <filter_json> <limit>}"
FILTER_JSON="${2:-{}}"
LIMIT="${3:-10}"
QDRANT_URL="${QDRANT_URL:-https://qdrant.agenticflows.co.uk}"

# --- auth check ---
: "${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY is required}"

# --- implementation ---
echo "STUB: not yet implemented"
exit 0
