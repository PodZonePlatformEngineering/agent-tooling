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

# 2. Trainer hook set (v2.1 substrate, no subagent chain)
HOOKS_DIR="${TARGET}/.claude/hooks"
assert "session-start.sh present"      "$([ -f "${HOOKS_DIR}/session-start.sh" ] && echo ok || echo fail)"
assert "user-prompt-submit.sh present" "$([ -f "${HOOKS_DIR}/user-prompt-submit.sh" ] && echo ok || echo fail)"
assert "pre-tool-use.sh NOT present (retired T-073)"  "$([ ! -f "${HOOKS_DIR}/pre-tool-use.sh" ] && echo ok || echo fail)"
assert "post-tool-use.sh NOT present (retired T-073)" "$([ ! -f "${HOOKS_DIR}/post-tool-use.sh" ] && echo ok || echo fail)"
assert "post-compact.sh present"       "$([ -f "${HOOKS_DIR}/post-compact.sh" ] && echo ok || echo fail)"
assert "stop.sh present"               "$([ -f "${HOOKS_DIR}/stop.sh" ] && echo ok || echo fail)"
assert "append-session-stop.py present" "$([ -f "${HOOKS_DIR}/append-session-stop.py" ] && echo ok || echo fail)"
assert "subagent-stop.sh NOT present"  "$([ ! -f "${HOOKS_DIR}/subagent-stop.sh" ] && echo ok || echo fail)"
assert "no stub startup.sh"            "$([ ! -f "${HOOKS_DIR}/startup.sh" ] && echo ok || echo fail)"
assert "no stub session-end.sh"        "$([ ! -f "${HOOKS_DIR}/session-end.sh" ] && echo ok || echo fail)"

# 2b. Resident deps (self-containment, v2.1)
assert ".claude/primitives/ resident"  "$([ -d "${TARGET}/.claude/primitives" ] && echo ok || echo fail)"
assert ".claude/lib/ resident"         "$([ -d "${TARGET}/.claude/lib" ] && echo ok || echo fail)"
assert "lib/qdrant_http.py resident"   "$([ -f "${TARGET}/.claude/lib/qdrant_http.py" ] && echo ok || echo fail)"
assert "primitives/qdrant resolves"    "$([ -f "${TARGET}/.claude/hooks/../primitives/qdrant/add-qdrant-point.sh" ] && echo ok || echo fail)"
assert "no AGENT_TOOLING_DIR in hooks"  "$(! grep -rq 'os.environ.*AGENT_TOOLING_DIR\|AGENT_TOOLING_DIR:-' "${HOOKS_DIR}" && echo ok || echo fail)"

# 3. settings.json: grouped substrate events; no SubagentStop for trainer
SETTINGS="${TARGET}/.claude/settings.json"
assert "settings.json valid JSON"       "$(python3 -c 'import json;json.load(open("'"$SETTINGS"'"))' 2>/dev/null && echo ok || echo fail)"
assert "settings.json has SessionStart" "$(grep -q 'SessionStart' "$SETTINGS" && echo ok || echo fail)"
assert "settings.json has UserPromptSubmit" "$(grep -q 'UserPromptSubmit' "$SETTINGS" && echo ok || echo fail)"
assert "settings.json no PostToolUse (retired T-073)" "$(! grep -q 'PostToolUse' "$SETTINGS" && echo ok || echo fail)"
assert "settings.json has Stop"         "$(grep -q '"Stop"'       "$SETTINGS" && echo ok || echo fail)"
assert "settings.json no SubagentStop"  "$(! grep -q 'SubagentStop' "$SETTINGS" && echo ok || echo fail)"
assert "settings.json no stub startup.sh" "$(! grep -q 'startup.sh' "$SETTINGS" && echo ok || echo fail)"

# 4. AGENTS.md contains agent and team
# PROJ-039/T-124: was `grep 'agent=alex'`. That literal has not been emitted since the
# AGENTS.md rewrite — the generated file carries a structured "## Agent" section, and
# runtime identity single-sources from workspaces/identity/*.identity.yaml (T-099).
# The property under test is unchanged: AGENTS.md names the agent it scaffolds for.
assert "AGENTS.md names the agent"     "$(grep -q '\*\*Name:\*\* Alex' "${TARGET}/AGENTS.md" && echo ok || echo fail)"
assert "AGENTS.md contains team=training" "$(grep -q 'training' "${TARGET}/AGENTS.md" && echo ok || echo fail)"

# 5. .gitignore contains .workspace/ and context/
assert ".gitignore has .workspace/"    "$(grep -q '\.workspace/' "${TARGET}/.gitignore" && echo ok || echo fail)"
# PROJ-039/T-124: was `.gitignore has context/`. `context/` was the pre-migration
# materialised-context directory (context/brief.md via work_items), dead since the
# .workspace/ materialise mechanism replaced it (CC-389 / T-079). The .workspace/
# assertion above is its successor; this slot now covers the other working-state
# ignore, logs/ (PROJ-039/T-093), which had no assertion at all.
assert ".gitignore has logs/"          "$(grep -q '^logs/' "${TARGET}/.gitignore" && echo ok || echo fail)"
assert ".gitignore has settings.local.json" "$(grep -q 'settings.local.json' "${TARGET}/.gitignore" && echo ok || echo fail)"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed."
[[ $FAIL -eq 0 ]]
