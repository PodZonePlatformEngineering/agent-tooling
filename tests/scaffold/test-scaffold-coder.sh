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

# Resident lib is the slim runtime closure (home-runtime-lib.manifest), NOT the
# whole agent-tooling lib/ (PROJ-039/T-011 C2-v2.1b). Assert each manifest module
# is byte-identical to source, that the home repo carries no out-of-closure module,
# and the canonical over-copy (decay/) is absent.
LIB_MANIFEST="${AGENT_TOOLING_DIR}/hooks/home-runtime-lib.manifest"
LIB_OK=ok
while IFS= read -r line; do
  entry="${line%%#*}"; entry="${entry//[[:space:]]/}"
  [[ -z "$entry" ]] && continue
  diff -q "${AGENT_TOOLING_DIR}/lib/${entry}" "${TARGET}/.claude/lib/${entry}" >/dev/null 2>&1 || LIB_OK=fail
done < "$LIB_MANIFEST"
assert "lib closure byte-identical to source" "$LIB_OK"
# No file under .claude/lib outside the manifest set.
EXTRA="$(comm -23 \
  <(find "${TARGET}/.claude/lib" -type f ! -path '*__pycache__*' | sed "s|${TARGET}/.claude/lib/||" | sort) \
  <(grep -vE '^\s*(#|$)' "$LIB_MANIFEST" | sed 's/#.*//; s/[[:space:]]//g' | sort))"
assert "no out-of-closure lib modules" "$([ -z "$EXTRA" ] && echo ok || echo fail)"
assert "no decay/ over-copy"           "$([ ! -e "${TARGET}/.claude/lib/decay" ] && echo ok || echo fail)"

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
