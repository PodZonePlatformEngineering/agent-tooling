#!/usr/bin/env bash
# LIVE round-trip for the PROJ-011/T-031 credential machinery: mint → use →
# revoke against cloud Qdrant. Key-gated: skips cleanly when
# PODZONE_QDRANT_APIKEY is absent (CI without the harness env).
#
# Asserts the DOCUMENTED tier behaviour without breaking the day jwt_rbac
# appears: mint must exit 0 (honoured + scoped) or 2 (cluster rejects
# self-signed — the 2026-07-12 live finding); anything else is a failure.
# The "use" leg exercises the collections themselves with the master key
# (write → read → delete a scratch brief point built by the schema helpers).
set -euo pipefail

AGENT_TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$AGENT_TOOLING_DIR"

if [[ -z "${PODZONE_QDRANT_APIKEY:-}" ]]; then
  echo "== proj011: training-jwt live round-trip: SKIPPED (no API key) =="
  exit 0
fi

echo "== proj011: training-jwt live round-trip (mint → use → revoke) =="
TRAINEE="_livetest"
TOKEN_FILE="$(mktemp)"
trap 'rm -f "$TOKEN_FILE"; python3 tools/training-jwt.py revoke --trainee "$TRAINEE" >/dev/null 2>&1 || true' EXIT

# --- mint: rc 0 (honoured+scoped) or 2 (tier rejects self-signed) ---
set +e
python3 tools/training-jwt.py mint --trainee "$TRAINEE" --days 1 > "$TOKEN_FILE" 2>/dev/null
MINT_RC=$?
set -e
if [[ $MINT_RC -ne 0 && $MINT_RC -ne 2 ]]; then
  echo "FAIL: mint exited $MINT_RC (expected 0 honoured or 2 tier-rejected)"
  exit 1
fi
echo "  mint: rc=$MINT_RC ($([[ $MINT_RC -eq 0 ]] && echo 'self-signed HONOURED' || echo 'self-signed rejected by tier — documented fallback applies'))"
[[ -s "$TOKEN_FILE" ]] || { echo "FAIL: no token emitted"; exit 1; }

# --- registry: exactly one active credential for the trainee ---
COUNT=$(python3 tools/training-jwt.py list --trainee "$TRAINEE" | grep -c "trainee=$TRAINEE")
[[ "$COUNT" -eq 1 ]] || { echo "FAIL: expected 1 registry point, got $COUNT"; exit 1; }
echo "  registry: 1 point present"

# --- use: schema helpers write/read/delete a scratch brief point (master key) ---
python3 - <<'EOF'
import sys
sys.path.insert(0, '.')
from lib import qdrant_http
from lib.training_substrate import TRAINING_BRIEFS, brief_id_for, build_brief_point

bid = brief_id_for("_livetest", slug="live-roundtrip")
point = build_brief_point(brief_id=bid, trainee="_livetest",
                          channel="training", body="live round-trip scratch",
                          author="test-training-jwt-live.sh")
qdrant_http.upsert_points([point], collection=TRAINING_BRIEFS)
back = qdrant_http.get_point(point["id"], collection=TRAINING_BRIEFS)
assert back and back["brief_id"] == bid, f"read-back mismatch: {back}"
assert back["status"] == "active" and back["revision"] == 1
qdrant_http.delete_points([point["id"]], collection=TRAINING_BRIEFS)
assert qdrant_http.get_point(point["id"], collection=TRAINING_BRIEFS) is None
print("  use: brief point write -> read-back -> delete OK on", TRAINING_BRIEFS)
EOF

# --- revoke: registry point deleted ---
python3 tools/training-jwt.py revoke --trainee "$TRAINEE" >/dev/null
LEFT=$(python3 tools/training-jwt.py list --trainee "$TRAINEE")
[[ "$LEFT" == *"no credentials"* ]] || { echo "FAIL: registry not empty after revoke: $LEFT"; exit 1; }
echo "  revoke: registry cleared"
echo "PASS: live round-trip complete"
