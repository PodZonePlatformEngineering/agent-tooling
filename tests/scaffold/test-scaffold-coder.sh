#!/usr/bin/env bash
# Test: scaffold.sh — coder role
# Asserts coder-specific hook set: task-event.sh and subagent-stop.sh present.
set -euo pipefail

AGENT_TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET="/tmp/test-home-podzone-dev-$$"
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

echo "=== test-scaffold-coder.sh (coder role) ==="
bash "${AGENT_TOOLING_DIR}/scaffold.sh" podzone dev coder --target-dir "$TARGET" > /dev/null

HOOKS_DIR="${TARGET}/.claude/hooks"
SETTINGS="${TARGET}/.claude/settings.json"

# 1. Base hooks present
assert "startup.sh present"       "$([ -f "${HOOKS_DIR}/startup.sh" ] && echo ok || echo fail)"
assert "session-end.sh present"   "$([ -f "${HOOKS_DIR}/session-end.sh" ] && echo ok || echo fail)"
assert "stop.sh present"          "$([ -f "${HOOKS_DIR}/stop.sh" ] && echo ok || echo fail)"

# 2. Coder-specific hooks present
assert "task-event.sh present"    "$([ -f "${HOOKS_DIR}/task-event.sh" ] && echo ok || echo fail)"
assert "subagent-stop.sh present" "$([ -f "${HOOKS_DIR}/subagent-stop.sh" ] && echo ok || echo fail)"

# 3. Archivist hook NOT present
assert "ingest-transcript.sh NOT present" "$([ ! -f "${HOOKS_DIR}/ingest-transcript.sh" ] && echo ok || echo fail)"

# 4. settings.json has PostToolUse and SubagentStop
assert "settings.json has PostToolUse"  "$(grep -q 'PostToolUse'  "$SETTINGS" && echo ok || echo fail)"
assert "settings.json has SubagentStop" "$(grep -q 'SubagentStop' "$SETTINGS" && echo ok || echo fail)"

# 5. settings.json has base events
assert "settings.json has SessionStart" "$(grep -q 'SessionStart' "$SETTINGS" && echo ok || echo fail)"
assert "settings.json has SessionEnd"   "$(grep -q 'SessionEnd'   "$SETTINGS" && echo ok || echo fail)"
assert "settings.json has Stop"         "$(grep -q '"Stop"'       "$SETTINGS" && echo ok || echo fail)"

# 6. Core structure present
assert "AGENTS.md exists"             "$([ -f "${TARGET}/AGENTS.md" ] && echo ok || echo fail)"
assert "workspace file exists"        "$([ -f "${TARGET}/home-podzone-dev.code-workspace" ] && echo ok || echo fail)"
assert "identity YAML exists"         "$([ -f "${TARGET}/workspaces/identity/martin-dev-coder.identity.yaml" ] && echo ok || echo fail)"
assert ".gitignore has .workspace/"   "$(grep -q '\.workspace/' "${TARGET}/.gitignore" && echo ok || echo fail)"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed."
[[ $FAIL -eq 0 ]]
