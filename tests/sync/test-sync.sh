#!/usr/bin/env bash
# Test: sync-agent-tooling.sh
# 1. Scaffold a test home repo (trainer)
# 2. Modify one hook to simulate drift
# 3. Run sync --yes
# 4. Assert modified hook was restored to agent-tooling version
set -euo pipefail

AGENT_TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET="/tmp/test-sync-home-$$"
PASS=0
FAIL=0

assert() {
  local desc="$1" result="$2"
  if [[ "$result" == "ok" ]]; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc"
    FAIL=$((FAIL + 1))
  fi
}

cleanup() { rm -rf "$TARGET"; }
trap cleanup EXIT

echo "=== test-sync.sh ==="

# 1. Scaffold
bash "${AGENT_TOOLING_DIR}/scaffold.sh" training sync-test trainer --target-dir "$TARGET" > /dev/null
assert "scaffold produced .claude/hooks/" "$([ -d "${TARGET}/.claude/hooks" ] && echo ok || echo fail)"

# 2. Simulate drift in session-start.sh (a real substrate hook) AND in a resident lib module
HOOK="${TARGET}/.claude/hooks/session-start.sh"
echo "# DRIFTED CONTENT" >> "$HOOK"
LIBMOD="${TARGET}/.claude/lib/qdrant_http.py"
echo "# DRIFTED LIB" >> "$LIBMOD"
assert "hook drift introduced" "$(grep -q 'DRIFTED CONTENT' "$HOOK" && echo ok || echo fail)"
assert "lib drift introduced"  "$(grep -q 'DRIFTED LIB' "$LIBMOD" && echo ok || echo fail)"

# 3. Sync with --yes (non-interactive)
SYNC_RC=0
bash "${AGENT_TOOLING_DIR}/sync-agent-tooling.sh" \
  --role trainer \
  --home-repo "$TARGET" \
  --agent-tooling "${AGENT_TOOLING_DIR}" \
  --yes > /tmp/test-sync-out-$$ 2>&1 || SYNC_RC=$?

# 4. Assert hook was restored to agent-tooling version
assert "session-start.sh restored to source" \
  "$(diff -q "${AGENT_TOOLING_DIR}/hooks/session-start.sh" "$HOOK" > /dev/null 2>&1 && echo ok || echo fail)"
assert "drifted hook content removed" \
  "$(! grep -q 'DRIFTED CONTENT' "$HOOK" && echo ok || echo fail)"

# 4b. Resident lib restored byte-identical (v2.1 dependency sync)
assert "lib/qdrant_http.py restored to source" \
  "$(diff -q "${AGENT_TOOLING_DIR}/lib/qdrant_http.py" "$LIBMOD" > /dev/null 2>&1 && echo ok || echo fail)"
assert "drifted lib content removed" \
  "$(! grep -q 'DRIFTED LIB' "$LIBMOD" && echo ok || echo fail)"

# 4c. Byte-identity invariant asserted + clean exit
assert "sync exit 0 (invariant PASS)" "$([ "$SYNC_RC" -eq 0 ] && echo ok || echo fail)"
assert "sync printed invariant PASS"  "$(grep -q 'Byte-identity invariant: PASS' /tmp/test-sync-out-$$ && echo ok || echo fail)"
rm -f /tmp/test-sync-out-$$

# 4d. Shipped manifest v2 (PROJ-039/T-055): version, source_commit, role, synced_at,
# files{path: sha256} — written only after a byte-identity PASS.
MANIFEST="${TARGET}/.claude/tooling-manifest.json"
assert "manifest written" "$([ -f "$MANIFEST" ] && echo ok || echo fail)"
EXPECT_VERSION="$(tr -d '[:space:]' < "${AGENT_TOOLING_DIR}/VERSION")"
assert "manifest version matches VERSION file" \
  "$(python3 -c "import json; d=json.load(open('${MANIFEST}')); exit(0 if d.get('version')=='${EXPECT_VERSION}' else 1)" && echo ok || echo fail)"
assert "manifest role matches" \
  "$(python3 -c "import json; d=json.load(open('${MANIFEST}')); exit(0 if d.get('role')=='trainer' else 1)" && echo ok || echo fail)"
assert "manifest carries source_commit + synced_at + files" \
  "$(python3 -c "import json; d=json.load(open('${MANIFEST}')); exit(0 if d.get('source_commit') and d.get('synced_at') and d.get('files') else 1)" && echo ok || echo fail)"
assert "manifest hooks/session-start.sh sha256 matches source" \
  "$(python3 -c "
import hashlib, json
d = json.load(open('${MANIFEST}'))
want = hashlib.sha256(open('${AGENT_TOOLING_DIR}/hooks/session-start.sh', 'rb').read()).hexdigest()
exit(0 if d['files'].get('hooks/session-start.sh') == want else 1)
" && echo ok || echo fail)"

# 5. Unchanged hooks not touched (stop.sh should still match)
assert "stop.sh still matches source" \
  "$(diff -q "${AGENT_TOOLING_DIR}/hooks/stop.sh" "${TARGET}/.claude/hooks/stop.sh" > /dev/null 2>&1 && echo ok || echo fail)"

# 6. Auto-detect role from identity YAML (no --role flag)
bash "${AGENT_TOOLING_DIR}/scaffold.sh" training auto-test trainer --target-dir "${TARGET}-auto" > /dev/null
echo "# DRIFT" >> "${TARGET}-auto/.claude/hooks/session-start.sh"
bash "${AGENT_TOOLING_DIR}/sync-agent-tooling.sh" \
  --home-repo "${TARGET}-auto" \
  --agent-tooling "${AGENT_TOOLING_DIR}" \
  --yes > /dev/null
assert "auto-detected role: session-start.sh restored" \
  "$(diff -q "${AGENT_TOOLING_DIR}/hooks/session-start.sh" "${TARGET}-auto/.claude/hooks/session-start.sh" > /dev/null 2>&1 && echo ok || echo fail)"
rm -rf "${TARGET}-auto"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed."
[[ $FAIL -eq 0 ]]
