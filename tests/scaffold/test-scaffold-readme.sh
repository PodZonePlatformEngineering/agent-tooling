#!/usr/bin/env bash
# Test: scaffold.sh — home-repo operator README (PROJ-039/T-095)
# Assertions: emitted for non-trainee roles with substitutions applied, no placeholder
# survives, trainee gets its own byte-untouched README instead, team-lead role emits
# BOTH the manual and the README.
set -euo pipefail

AGENT_TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET_CODER="/tmp/test-home-podzone-readmecoder-$$"
TARGET_LEAD="/tmp/test-home-training-readmelead-$$"
TARGET_TRAINEE="/tmp/test-home-podzone-readmetrainee-$$"
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

cleanup() { rm -rf "$TARGET_CODER" "$TARGET_LEAD" "$TARGET_TRAINEE"; }
trap cleanup EXIT

echo "=== test-scaffold-readme.sh (operator README) ==="

# 1. Build-agent role (coder): README emitted with substitutions applied
NO_TELEMETRY_BOOTSTRAP=1 bash "${AGENT_TOOLING_DIR}/scaffold.sh" podzone readmecoder coder --target-dir "$TARGET_CODER" > /dev/null
CODER_README="${TARGET_CODER}/README.md"
assert "coder README.md exists"           "$([ -f "$CODER_README" ] && echo ok || echo fail)"
assert "coder README carries repo name (home-podzone-readmecoder)" \
  "$(grep -q 'home-podzone-readmecoder' "$CODER_README" && echo ok || echo fail)"
assert "coder README carries role title (Coder)" \
  "$(grep -q 'Coder' "$CODER_README" && echo ok || echo fail)"
assert "coder README carries change-visibility policy" \
  "$(grep -q 'change-visibility policy' "$CODER_README" && echo ok || echo fail)"
assert "no unsubstituted placeholder (__AGENT__/__TEAM__/__REPO_NAME__/__ROLE_TITLE__)" \
  "$(! grep -qE '__(AGENT|AGENT_LC|TEAM|TEAM_REPO|REPO_NAME|ROLE_TITLE)__' "$CODER_README" && echo ok || echo fail)"
assert "coder README ~150 lines (size discipline)" \
  "$([ "$(wc -l < "$CODER_README")" -le 160 ] && echo ok || echo fail)"

# 2. Team-lead role: BOTH the manual AND the README are emitted
NO_TELEMETRY_BOOTSTRAP=1 bash "${AGENT_TOOLING_DIR}/scaffold.sh" training readmelead team-lead --target-dir "$TARGET_LEAD" > /dev/null
LEAD_README="${TARGET_LEAD}/README.md"
LEAD_MANUAL="${TARGET_LEAD}/OPERATING-MANUAL.md"
assert "team-lead README.md exists"       "$([ -f "$LEAD_README" ] && echo ok || echo fail)"
assert "team-lead OPERATING-MANUAL.md ALSO exists" \
  "$([ -f "$LEAD_MANUAL" ] && echo ok || echo fail)"
assert "team-lead README carries role title (Team Lead)" \
  "$(grep -q 'Team Lead' "$LEAD_README" && echo ok || echo fail)"
assert "team-lead README carries resolved team repo (trainingTeam)" \
  "$(grep -q 'trainingTeam' "$LEAD_README" && echo ok || echo fail)"
assert "no unsubstituted placeholder in team-lead README" \
  "$(! grep -qE '__(AGENT|AGENT_LC|TEAM|TEAM_REPO|REPO_NAME|ROLE_TITLE)__' "$LEAD_README" && echo ok || echo fail)"

# 3. Trainee: byte-untouched trainee README, NOT the operator template
NO_TELEMETRY_BOOTSTRAP=1 bash "${AGENT_TOOLING_DIR}/scaffold.sh" podzone readmetrainee trainee --target-dir "$TARGET_TRAINEE" > /dev/null
TRAINEE_README="${TARGET_TRAINEE}/README.md"
assert "trainee README.md exists"         "$([ -f "$TRAINEE_README" ] && echo ok || echo fail)"
assert "trainee README is NOT the operator template (no change-visibility policy section)" \
  "$(! grep -q 'change-visibility policy' "$TRAINEE_README" && echo ok || echo fail)"
assert "trainee README matches freshly-rendered trainee template" \
  "$(diff -q "$TRAINEE_README" <(sed -e 's|__TRAINEE__|Trainee|g' -e 's|__REPO_NAME__|home-podzone-readmetrainee|g' "${AGENT_TOOLING_DIR}/scaffold/trainee/README.template") > /dev/null 2>&1 && echo ok || echo fail)"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed."
[[ $FAIL -eq 0 ]]
