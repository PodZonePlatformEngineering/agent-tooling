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

# 2. Simulate drift in startup.sh
HOOK="${TARGET}/.claude/hooks/startup.sh"
echo "# DRIFTED CONTENT" >> "$HOOK"
DRIFTED_CONTENT="$(cat "$HOOK")"
assert "drift introduced" "$(grep -q 'DRIFTED CONTENT' "$HOOK" && echo ok || echo fail)"

# 3. Sync with --yes (non-interactive)
bash "${AGENT_TOOLING_DIR}/sync-agent-tooling.sh" \
  --role trainer \
  --home-repo "$TARGET" \
  --agent-tooling "${AGENT_TOOLING_DIR}" \
  --yes > /dev/null

# 4. Assert hook was restored to agent-tooling version
SOURCE_CONTENT="$(cat "${AGENT_TOOLING_DIR}/hooks/startup.sh")"
SYNCED_CONTENT="$(cat "$HOOK")"

assert "startup.sh restored to agent-tooling version" \
  "$([ "$SOURCE_CONTENT" = "$SYNCED_CONTENT" ] && echo ok || echo fail)"

assert "drifted content removed" \
  "$(! grep -q 'DRIFTED CONTENT' "$HOOK" && echo ok || echo fail)"

# 5. Unchanged hooks not touched (session-end.sh should still match)
SE_SRC="${AGENT_TOOLING_DIR}/hooks/session-end.sh"
SE_DST="${TARGET}/.claude/hooks/session-end.sh"
assert "session-end.sh still matches source" \
  "$(diff -q "$SE_SRC" "$SE_DST" > /dev/null 2>&1 && echo ok || echo fail)"

# 6. Auto-detect role from identity YAML (no --role flag)
bash "${AGENT_TOOLING_DIR}/scaffold.sh" training auto-test trainer --target-dir "${TARGET}-auto" > /dev/null
echo "# DRIFT" >> "${TARGET}-auto/.claude/hooks/startup.sh"
bash "${AGENT_TOOLING_DIR}/sync-agent-tooling.sh" \
  --home-repo "${TARGET}-auto" \
  --agent-tooling "${AGENT_TOOLING_DIR}" \
  --yes > /dev/null
assert "auto-detected role: startup.sh restored" \
  "$(diff -q "${AGENT_TOOLING_DIR}/hooks/startup.sh" "${TARGET}-auto/.claude/hooks/startup.sh" > /dev/null 2>&1 && echo ok || echo fail)"
rm -rf "${TARGET}-auto"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed."
[[ $FAIL -eq 0 ]]
