#!/usr/bin/env bash
# Bootstrap the Qdrant secrets collection from the secretctl/1Password vault.
# Martin-run tool — requires TTY for 1Password authentication.
# Usage: load-secrets.sh [--dry-run] [--vault <vault-name>]
# Auth: SECRETCTL_PASSWORD (must be set); PODZONE_QDRANT_APIKEY
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DRY_RUN=false
VAULT="${OP_VAULT:-podzone}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --vault)   VAULT="${2:?--vault requires a value}"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; echo "Usage: load-secrets.sh [--dry-run] [--vault <vault-name>]" >&2; exit 1 ;;
  esac
done

: "${SECRETCTL_PASSWORD:?SECRETCTL_PASSWORD must be set before running this script}"
QDRANT_URL="${AGENTSONLY_QDRANT_URL:-http://qdrant.agenticflows.co.uk:8080}"
API_KEY="${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"
COLLECTION="secrets"

command -v op >/dev/null 2>&1 || {
  echo "Error: 'op' (1Password CLI) is required but not found in PATH." >&2
  echo "Install via: brew install 1password-cli" >&2
  exit 1
}

echo "==> Listing secrets from vault '${VAULT}'..."
ITEMS_JSON=$(op item list --vault "${VAULT}" --format json 2>&1) || {
  echo "Error: could not list items from vault '${VAULT}'." >&2
  echo "Ensure you are signed in to 1Password CLI: op signin" >&2
  exit 1
}

ITEM_NAMES=$(python3 -c "
import json, sys
items = json.loads(sys.argv[1])
for item in items:
    print(item['title'])
" "${ITEMS_JSON}")

LOADED=0
SKIPPED=0

derive_system() {
  local name="$1"
  case "${name}" in
    *telegram*|*bot_token*) echo "telegram" ;;
    *qdrant*)               echo "qdrant" ;;
    *github*|*_token*)      echo "github" ;;
    *gmail*|*google*)       echo "gmail" ;;
    *minio*|*s3*)           echo "minio" ;;
    *)                      echo "general" ;;
  esac
}

derive_type() {
  local name="$1"
  case "${name}" in
    *_apikey|*_api_key|*_key) echo "api_key" ;;
    *_token)                  echo "api_key" ;;
    *_password|*_pass)        echo "password" ;;
    *_oauth_token)            echo "oauth_token" ;;
    *_oauth_file|*_json)      echo "oauth_file_path" ;;
    *)                        echo "api_key" ;;
  esac
}

while IFS= read -r ITEM_NAME; do
  [[ -z "${ITEM_NAME}" ]] && continue

  SYSTEM=$(derive_system "${ITEM_NAME}")
  TYPE=$(derive_type "${ITEM_NAME}")
  # Deterministic UUID from item name (md5 → UUID format, matching podzoneAgentTeam convention)
  POINT_ID=$(python3 -c "
import hashlib, uuid
print(str(uuid.UUID(hashlib.md5('${ITEM_NAME}'.encode()).hexdigest())))
")

  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "  [dry-run] would upsert: ${ITEM_NAME} (system=${SYSTEM}, type=${TYPE}, id=${POINT_ID})"
    LOADED=$((LOADED + 1))
    continue
  fi

  # Retrieve secret value: derive env var name from item name (uppercase, replace - and / with _)
  ENV_VAR=$(echo "${ITEM_NAME}" | tr '[:lower:]' '[:upper:]' | tr '-/_' '___')

  SECRET_VALUE=$(secretctl run -k "${ITEM_NAME}" -- bash -c "printf '%s' \"\${${ENV_VAR}:-}\"" 2>/dev/null) || {
    echo "  SKIP: ${ITEM_NAME} — could not retrieve (field '${ENV_VAR}' not found or secretctl error)"
    SKIPPED=$((SKIPPED + 1))
    continue
  }

  if [[ -z "${SECRET_VALUE}" ]]; then
    echo "  SKIP: ${ITEM_NAME} — empty value for env var '${ENV_VAR}'"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  NOW=$(python3 -c "from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())")
  PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({
  'name':        sys.argv[1],
  'system':      sys.argv[2],
  'user':        'martin',
  'secret':      sys.argv[3],
  'type':        sys.argv[4],
  'scope':       ['all'],
  'description': sys.argv[1],
  'created_at':  sys.argv[5],
  'rotated_at':  None,
}))
" "${ITEM_NAME}" "${SYSTEM}" "${SECRET_VALUE}" "${TYPE}" "${NOW}")

  curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}/points" \
    -H "Content-Type: application/json" \
    -H "api-key: ${API_KEY}" \
    -d "{\"points\": [{\"id\": \"${POINT_ID}\", \"vector\": [0.0, 0.0, 0.0, 0.0], \"payload\": ${PAYLOAD}}]}" \
    > /dev/null

  echo "  loaded: ${ITEM_NAME}"
  LOADED=$((LOADED + 1))

done <<< "${ITEM_NAMES}"

echo ""
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "Dry-run complete: ${LOADED} secrets would be upserted."
else
  echo "${LOADED} secrets loaded, ${SKIPPED} skipped (empty value or retrieval error)."
fi
