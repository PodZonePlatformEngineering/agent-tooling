#!/usr/bin/env bash
# Load secrets into the Qdrant secrets collection from JSON input.
# Vault enumeration is done by the Claude Code agent using secretctl MCP tools.
# See tools/dump-secrets.md for the full bootstrap workflow.
#
# Usage:
#   echo '[{"name":"foo","value":"bar"}]' | load-secrets.sh [--dry-run]
#   load-secrets.sh --secrets-file /tmp/secrets-dump.json [--dry-run]
#
# Auth: PODZONE_QDRANT_APIKEY (Qdrant writes only; no vault access in this script)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DRY_RUN=false
SECRETS_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)       DRY_RUN=true; shift ;;
    --secrets-file)  SECRETS_FILE="${2:?--secrets-file requires a path}"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2
       echo "Usage: load-secrets.sh [--secrets-file <path>] [--dry-run]" >&2
       exit 1 ;;
  esac
done

QDRANT_URL="${QDRANT_URL:-${AGENTSONLY_QDRANT_URL:-https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io}}"
COLLECTION="secrets"

if [[ "${DRY_RUN}" == "false" ]]; then
  : "${PODZONE_QDRANT_APIKEY:?PODZONE_QDRANT_APIKEY not set}"
  API_KEY="${PODZONE_QDRANT_APIKEY}"
fi

# Read JSON input from --secrets-file or stdin
if [[ -n "${SECRETS_FILE}" ]]; then
  [[ -f "${SECRETS_FILE}" ]] || { echo "Error: secrets file not found: ${SECRETS_FILE}" >&2; exit 1; }
  SECRETS_JSON=$(cat "${SECRETS_FILE}")
elif [[ ! -t 0 ]]; then
  SECRETS_JSON=$(cat)
else
  echo "Error: no secrets input. Provide JSON via stdin or --secrets-file <path>." >&2
  echo "  Example: echo '[{\"name\":\"foo\",\"value\":\"bar\"}]' | load-secrets.sh --dry-run" >&2
  exit 1
fi

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

# Parse JSON array and iterate over entries
ENTRIES=$(python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
for entry in data:
    name = entry.get('name', '').strip()
    value = entry.get('value', '').strip()
    if name:
        print(name + '\t' + value)
" <<< "${SECRETS_JSON}")

while IFS=$'\t' read -r ITEM_NAME SECRET_VALUE; do
  [[ -z "${ITEM_NAME}" ]] && continue

  SYSTEM=$(derive_system "${ITEM_NAME}")
  TYPE=$(derive_type "${ITEM_NAME}")
  POINT_ID=$(python3 -c "
import hashlib, uuid
print(str(uuid.UUID(hashlib.md5('${ITEM_NAME}'.encode()).hexdigest())))
")

  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "  [dry-run] would upsert: ${ITEM_NAME} (system=${SYSTEM}, type=${TYPE}, id=${POINT_ID})"
    LOADED=$((LOADED + 1))
    continue
  fi

  if [[ -z "${SECRET_VALUE}" ]]; then
    echo "  SKIP: ${ITEM_NAME} — empty value"
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

  curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}/points?wait=true" \
    -H "Content-Type: application/json" \
    -H "api-key: ${API_KEY}" \
    -d "{\"points\": [{\"id\": \"${POINT_ID}\", \"vector\": [0.0, 0.0, 0.0, 0.0], \"payload\": ${PAYLOAD}}]}" \
    > /dev/null

  echo "  loaded: ${ITEM_NAME}"
  LOADED=$((LOADED + 1))

done <<< "${ENTRIES}"

echo ""
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "Dry-run complete: ${LOADED} secrets would be upserted."
else
  echo "${LOADED} secrets loaded, ${SKIPPED} skipped (empty value)."
fi
