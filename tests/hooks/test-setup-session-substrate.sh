#!/usr/bin/env bash
# Structural tests for hooks/setup-session-substrate.sh (PROJ-039 § 2.1, DT-015).
# Asserts the static "index before ingest" ordering (F-2-004) and the auth guard.
# Live creation is exercised against a real cloud instance during MVP verification.
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/hooks/setup-session-substrate.sh"
PASS=0; FAIL=0
ok()   { echo "  PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

echo "=== setup-session-substrate.sh (structural) ==="

[[ -x "$SCRIPT" ]] && ok "script is executable" || fail "not executable"
bash -n "$SCRIPT" && ok "bash syntax valid" || fail "bash syntax invalid"

# Missing API key → loud non-zero (never a silent unauthenticated create).
if env -u PODZONE_QDRANT_APIKEY bash "$SCRIPT" >/dev/null 2>&1; then
  fail "missing PODZONE_QDRANT_APIKEY — expected non-zero"
else
  ok "missing PODZONE_QDRANT_APIKEY (exit non-zero as expected)"
fi

# DT-015 (static half): the point_type index PUT must precede any points PUT.
# This script never PUTs points, so the invariant holds by construction — assert
# both halves of that: an index PUT exists, and no `/points` (full upsert) PUT.
grep -q '/index' "$SCRIPT" && ok "creates a payload index" || fail "no index creation"
if grep -E 'X PUT .*/points("|/)' "$SCRIPT" >/dev/null 2>&1; then
  fail "script PUTs points — would violate index-before-ingest"
else
  ok "never PUTs points (index-before-ingest holds by construction)"
fi

# point_type index is created before the read-filter indexes.
PT_LINE=$(grep -n 'point_type' "$SCRIPT" | head -1 | cut -d: -f1)
SID_LINE=$(grep -n 'session_id agent work_item status' "$SCRIPT" | head -1 | cut -d: -f1)
if [[ -n "$PT_LINE" && -n "$SID_LINE" && "$PT_LINE" -lt "$SID_LINE" ]]; then
  ok "point_type index precedes read-filter indexes"
else
  fail "point_type not created first (line PT=$PT_LINE SID=$SID_LINE)"
fi

# Targets the unified collection on cloud (not the CST collection).
grep -q 'session_substrate' "$SCRIPT" && ok "targets session_substrate" || fail "wrong collection"
grep -q 'cloud.qdrant.io' "$SCRIPT" && ok "cloud default URL" || fail "not cloud-defaulted"

echo "  Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
