#!/usr/bin/env bash
# Retrieve a secret by name from the Qdrant secrets collection.
# Usage: getSecret.sh <secret-name>
# Output: prints secret value to stdout; also sets PODZONE_SECRET in caller via eval.
# Auth: PODZONE_QDRANT_APIKEY
set -euo pipefail

SECRET_NAME="${1:-}"

if [[ -z "${SECRET_NAME}" ]]; then
  echo "Usage: getSecret.sh <secret-name>" >&2
  exit 1
fi

QDRANT_URL="${AGENTSONLY_QDRANT_URL:-http://qdrant.agenticflows.co.uk:8080}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"
COLLECTION="secrets"

FILTER="{\"must\":[{\"key\":\"name\",\"match\":{\"value\":\"${SECRET_NAME}\"}}]}"
BODY="{\"limit\":1,\"with_payload\":true,\"with_vector\":false,\"filter\":${FILTER}}"

RESPONSE=$(curl -sf -X POST "${QDRANT_URL}/collections/${COLLECTION}/points/scroll" \
  -H "Content-Type: application/json" \
  -H "api-key: ${API_KEY}" \
  -d "${BODY}" 2>&1) || {
  echo "getSecret: connection error — could not reach ${QDRANT_URL}" >&2
  exit 1
}

SECRET_VALUE=$(python3 -c "
import json, sys
data = json.loads(sys.argv[1])
pts = data.get('result', {}).get('points', [])
if not pts:
    sys.exit(1)
print(pts[0]['payload']['secret'])
" "${RESPONSE}" 2>/dev/null) || {
  echo "getSecret: secret '${SECRET_NAME}' not found in collection '${COLLECTION}'" >&2
  exit 1
}

echo "${SECRET_VALUE}"
