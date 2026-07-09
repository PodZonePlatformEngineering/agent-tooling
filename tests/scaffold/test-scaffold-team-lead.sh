#!/usr/bin/env bash
# Test: scaffold.sh — team-lead role (PROJ-039/T-038 skills subset + T-081 manual)
# Assertions: coordination skill subset present + byte-identical, no out-of-subset
# skill, OPERATING-MANUAL.md rendered with substitutions (no placeholder survives),
# and NOT emitted for a non-team-lead role.
set -euo pipefail

AGENT_TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET="/tmp/test-home-training-leadtest-$$"
TARGET_CODER="/tmp/test-home-podzone-codertest-$$"
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

cleanup() { rm -rf "$TARGET" "$TARGET_CODER"; }
trap cleanup EXIT

echo "=== test-scaffold-team-lead.sh (team-lead role) ==="
NO_TELEMETRY_BOOTSTRAP=1 bash "${AGENT_TOOLING_DIR}/scaffold.sh" training leadtest team-lead --target-dir "$TARGET" > /dev/null

# 1. Coordination skill subset (team-lead-skills.manifest) present + byte-identical
while IFS= read -r line || [[ -n "$line" ]]; do
  entry="${line%%#*}"; entry="${entry//[[:space:]]/}"
  [[ -z "$entry" ]] && continue
  assert "skills/${entry} present + byte-identical" \
    "$(diff -rq -x '__pycache__' -x '*.pyc' "${AGENT_TOOLING_DIR}/skills/${entry}" "${TARGET}/.claude/skills/${entry}" > /dev/null 2>&1 && echo ok || echo fail)"
done < "${AGENT_TOOLING_DIR}/scaffold/team-lead-skills.manifest"
assert "no out-of-subset skill (session-end absent)" \
  "$([ ! -d "${TARGET}/.claude/skills/session-end" ] && echo ok || echo fail)"

# 2. Operating manual (PROJ-039/T-081) rendered at repo root
MANUAL="${TARGET}/OPERATING-MANUAL.md"
assert "OPERATING-MANUAL.md exists"           "$([ -f "$MANUAL" ] && echo ok || echo fail)"
assert "manual title carries capitalised agent" \
  "$(head -1 "$MANUAL" | grep -q 'Leadtest — Team Lead Operating Manual' && echo ok || echo fail)"
assert "manual carries resolved team repo (trainingTeam)" \
  "$(grep -q 'trainingTeam' "$MANUAL" && echo ok || echo fail)"
assert "manual carries repo name (home-training-leadtest)" \
  "$(grep -q 'home-training-leadtest' "$MANUAL" && echo ok || echo fail)"
assert "no unsubstituted placeholder (__AGENT__/__TEAM__/__REPO_NAME__)" \
  "$(! grep -qE '__(AGENT|AGENT_LC|TEAM|TEAM_REPO|REPO_NAME)__' "$MANUAL" && echo ok || echo fail)"
assert "manual ≤ 400 lines (size discipline)" \
  "$([ "$(wc -l < "$MANUAL")" -le 400 ] && echo ok || echo fail)"

# 3. Current-reality guards: no dead mechanisms in the shipped manual
assert "manual has no agents/{name}/incoming/ reference" \
  "$(! grep -q 'agents/{name}/incoming' "$MANUAL" && echo ok || echo fail)"
assert "manual has no context/brief.md (dead materialise mechanism)" \
  "$(! grep -q 'context/brief\.md' "$MANUAL" && echo ok || echo fail)"

# 4. Not emitted for other roles
NO_TELEMETRY_BOOTSTRAP=1 bash "${AGENT_TOOLING_DIR}/scaffold.sh" podzone codertest coder --target-dir "$TARGET_CODER" > /dev/null
assert "coder role emits NO OPERATING-MANUAL.md" \
  "$([ ! -f "${TARGET_CODER}/OPERATING-MANUAL.md" ] && echo ok || echo fail)"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed."
[[ $FAIL -eq 0 ]]
