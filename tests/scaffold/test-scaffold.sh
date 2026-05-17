#!/usr/bin/env bash
# Test: scaffold.sh — trainer role
# Assertions per brief: directory structure, hook set, settings.json events,
# AGENTS.md content, .gitignore content.
set -euo pipefail

AGENT_TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET="/tmp/test-home-training-alex-$$"
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

echo "=== test-scaffold.sh (trainer role) ==="
bash "${AGENT_TOOLING_DIR}/scaffold.sh" training alex trainer --target-dir "$TARGET" > /dev/null

# 1. Required files and directories present
assert "AGENTS.md exists"              "$([ -f "${TARGET}/AGENTS.md" ] && echo ok || echo fail)"
assert "README.md exists"              "$([ -f "${TARGET}/README.md" ] && echo ok || echo fail)"
assert ".gitignore exists"             "$([ -f "${TARGET}/.gitignore" ] && echo ok || echo fail)"
assert ".claude/settings.json exists"  "$([ -f "${TARGET}/.claude/settings.json" ] && echo ok || echo fail)"
assert ".claude/instructions.md exists" "$([ -f "${TARGET}/.claude/instructions.md" ] && echo ok || echo fail)"
assert ".claude/guardrails.md exists"  "$([ -f "${TARGET}/.claude/guardrails.md" ] && echo ok || echo fail)"
assert ".claude/output-format.md exists" "$([ -f "${TARGET}/.claude/output-format.md" ] && echo ok || echo fail)"
assert "results/ exists"               "$([ -d "${TARGET}/results" ] && echo ok || echo fail)"
assert "session-reports/ exists"       "$([ -d "${TARGET}/session-reports" ] && echo ok || echo fail)"
assert "memory/ exists"                "$([ -d "${TARGET}/memory" ] && echo ok || echo fail)"
assert "memory/MEMORY.md exists"       "$([ -f "${TARGET}/memory/MEMORY.md" ] && echo ok || echo fail)"
assert "workspaces/identity/ exists"   "$([ -d "${TARGET}/workspaces/identity" ] && echo ok || echo fail)"
assert "workspace file exists"         "$([ -f "${TARGET}/home-training-alex.code-workspace" ] && echo ok || echo fail)"
assert "identity YAML exists"          "$([ -f "${TARGET}/workspaces/identity/martin-alex-trainer.identity.yaml" ] && echo ok || echo fail)"

# 2. Trainer hook set: exactly startup.sh, session-end.sh, stop.sh (no extras)
HOOKS_DIR="${TARGET}/.claude/hooks"
assert "startup.sh present"            "$([ -f "${HOOKS_DIR}/startup.sh" ] && echo ok || echo fail)"
assert "session-end.sh present"        "$([ -f "${HOOKS_DIR}/session-end.sh" ] && echo ok || echo fail)"
assert "stop.sh present"               "$([ -f "${HOOKS_DIR}/stop.sh" ] && echo ok || echo fail)"
assert "task-event.sh NOT present"     "$([ ! -f "${HOOKS_DIR}/task-event.sh" ] && echo ok || echo fail)"
assert "subagent-stop.sh NOT present"  "$([ ! -f "${HOOKS_DIR}/subagent-stop.sh" ] && echo ok || echo fail)"
assert "ingest-transcript.sh NOT present" "$([ ! -f "${HOOKS_DIR}/ingest-transcript.sh" ] && echo ok || echo fail)"

# 3. settings.json has exactly SessionStart, SessionEnd, Stop (no PostToolUse, no SubagentStop)
SETTINGS="${TARGET}/.claude/settings.json"
assert "settings.json has SessionStart" "$(grep -q 'SessionStart' "$SETTINGS" && echo ok || echo fail)"
assert "settings.json has SessionEnd"   "$(grep -q 'SessionEnd'   "$SETTINGS" && echo ok || echo fail)"
assert "settings.json has Stop"         "$(grep -q '"Stop"'       "$SETTINGS" && echo ok || echo fail)"
assert "settings.json no PostToolUse"   "$(! grep -q 'PostToolUse'  "$SETTINGS" && echo ok || echo fail)"
assert "settings.json no SubagentStop"  "$(! grep -q 'SubagentStop' "$SETTINGS" && echo ok || echo fail)"

# 4. AGENTS.md contains agent and team
assert "AGENTS.md contains agent=alex" "$(grep -q 'agent=alex' "${TARGET}/AGENTS.md" && echo ok || echo fail)"
assert "AGENTS.md contains team=training" "$(grep -q 'training' "${TARGET}/AGENTS.md" && echo ok || echo fail)"

# 5. .gitignore contains .workspace/ and context/
assert ".gitignore has .workspace/"    "$(grep -q '\.workspace/' "${TARGET}/.gitignore" && echo ok || echo fail)"
assert ".gitignore has context/"       "$(grep -q 'context/' "${TARGET}/.gitignore" && echo ok || echo fail)"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed."
[[ $FAIL -eq 0 ]]
