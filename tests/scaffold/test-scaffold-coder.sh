#!/usr/bin/env bash
# Test: scaffold.sh — coder role (template v2.1)
# Asserts the real substrate hook set + subagent chain, resident deps, grouped settings.
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

# 1. Substrate base hooks present (v2.1)
assert "session-start.sh present" "$([ -f "${HOOKS_DIR}/session-start.sh" ] && echo ok || echo fail)"
assert "post-tool-use.sh present" "$([ -f "${HOOKS_DIR}/post-tool-use.sh" ] && echo ok || echo fail)"
assert "stop.sh present"          "$([ -f "${HOOKS_DIR}/stop.sh" ] && echo ok || echo fail)"
assert "append-session-stop.py present" "$([ -f "${HOOKS_DIR}/append-session-stop.py" ] && echo ok || echo fail)"

# 2. Coder-specific subagent chain present
assert "subagent-stop.sh present" "$([ -f "${HOOKS_DIR}/subagent-stop.sh" ] && echo ok || echo fail)"
assert "subagent-stop.py present" "$([ -f "${HOOKS_DIR}/subagent-stop.py" ] && echo ok || echo fail)"

# 3. No stub hooks
assert "no stub startup.sh"        "$([ ! -f "${HOOKS_DIR}/startup.sh" ] && echo ok || echo fail)"
assert "no stub task-event.sh"     "$([ ! -f "${HOOKS_DIR}/task-event.sh" ] && echo ok || echo fail)"

# 3b. Resident deps + self-containment
assert ".claude/primitives/ resident" "$([ -d "${TARGET}/.claude/primitives" ] && echo ok || echo fail)"
assert ".claude/lib/ resident"        "$([ -d "${TARGET}/.claude/lib" ] && echo ok || echo fail)"
assert "byte-identical lib vs source" "$(diff -rq -x __pycache__ -x '*.pyc' "${AGENT_TOOLING_DIR}/lib" "${TARGET}/.claude/lib" >/dev/null 2>&1 && echo ok || echo fail)"

# 4. settings.json: valid grouped JSON with substrate + SubagentStop
assert "settings.json valid JSON"       "$(python3 -c 'import json;json.load(open("'"$SETTINGS"'"))' 2>/dev/null && echo ok || echo fail)"
assert "settings.json has PostToolUse"  "$(grep -q 'PostToolUse'  "$SETTINGS" && echo ok || echo fail)"
assert "settings.json has SubagentStop" "$(grep -q 'SubagentStop' "$SETTINGS" && echo ok || echo fail)"
assert "settings.json has SessionStart" "$(grep -q 'SessionStart' "$SETTINGS" && echo ok || echo fail)"
assert "settings.json has Stop"         "$(grep -q '"Stop"'       "$SETTINGS" && echo ok || echo fail)"
assert "settings.json no stub session-end" "$(! grep -q 'session-end.sh' "$SETTINGS" && echo ok || echo fail)"

# 6. Core structure present
assert "AGENTS.md exists"             "$([ -f "${TARGET}/AGENTS.md" ] && echo ok || echo fail)"
assert "workspace file exists"        "$([ -f "${TARGET}/home-podzone-dev.code-workspace" ] && echo ok || echo fail)"
assert "identity YAML exists"         "$([ -f "${TARGET}/workspaces/identity/martin-dev-coder.identity.yaml" ] && echo ok || echo fail)"
assert ".gitignore has .workspace/"   "$(grep -q '\.workspace/' "${TARGET}/.gitignore" && echo ok || echo fail)"
assert ".gitignore has settings.local.json" "$(grep -q 'settings.local.json' "${TARGET}/.gitignore" && echo ok || echo fail)"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed."
[[ $FAIL -eq 0 ]]
