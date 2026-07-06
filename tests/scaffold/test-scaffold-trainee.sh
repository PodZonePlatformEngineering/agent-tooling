#!/usr/bin/env bash
# Test: scaffold.sh — trainee role (PROJ-011/T-025 R-5..R-14)
# Asserts the trainee-repo structure + artifact hygiene + env slimming produced by the
# trainee-conditional scaffold path, and the R-11 hygiene gate + R-8 personalisation.
set -euo pipefail

AGENT_TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET="/tmp/test-trainee-template-$$"
CLONE="/tmp/test-podzone-training-testuser-$$"
PASS=0
FAIL=0

assert() {
  local desc="$1" result="$2"
  if [[ "$result" == "ok" ]]; then echo "  PASS: $desc"; PASS=$((PASS + 1))
  else echo "  FAIL: $desc"; FAIL=$((FAIL + 1)); fi
}
has()   { [ -e "$1" ] && echo ok || echo fail; }
hasnt() { [ ! -e "$1" ] && echo ok || echo fail; }
grep_q() { grep -q "$1" "$2" 2>/dev/null && echo ok || echo fail; }
grep_absent() { grep -q "$1" "$2" 2>/dev/null && echo fail || echo ok; }

cleanup() { rm -rf "$TARGET" "$CLONE"; }
trap cleanup EXIT

echo "=== test-scaffold-trainee.sh (trainee role) ==="
bash "${AGENT_TOOLING_DIR}/scaffold.sh" training template trainee --target-dir "$TARGET" > /dev/null

# R-5: trainee root dir + the academy-equivalent structure
assert "R-5 Trainee/ dir"                 "$(has "${TARGET}/Trainee")"
assert "R-5 Trainee/sourceDocs"           "$(has "${TARGET}/Trainee/sourceDocs")"
assert "R-5 Trainee/inputDocs"            "$(has "${TARGET}/Trainee/inputDocs")"
assert "R-5 Trainee/outputDocs"           "$(has "${TARGET}/Trainee/outputDocs")"
assert "R-5 Trainee/sessions"            "$(has "${TARGET}/Trainee/sessions")"
assert "R-5 trainee-profile.md"          "$(has "${TARGET}/Trainee/trainee-profile.md")"
assert "R-5 training-state.md"           "$(has "${TARGET}/Trainee/training-state.md")"
assert "R-5 feedback.md"                 "$(has "${TARGET}/Trainee/feedback.md")"
assert "R-5 improvement-recommendations" "$(has "${TARGET}/Trainee/improvement-recommendations.md")"

# R-6: logs/ present; .gitignore no longer ignores *.log (committed logs)
assert "R-6 logs/ dir"                    "$(has "${TARGET}/logs")"
assert "R-6 .gitignore drops *.log"       "$(grep_absent '^\*\.log$' "${TARGET}/.gitignore")"
assert "R-6 .gitignore keeps .workspace/" "$(grep_q '\.workspace/' "${TARGET}/.gitignore")"

# R-7: README/AGENTS are operating-manual only — no provenance markers
assert "R-7 README no R-numbers"          "$(grep_absent 'R-[0-9]' "${TARGET}/README.md")"
assert "R-7 AGENTS no headless model"     "$(grep_absent 'Headless operating model' "${TARGET}/AGENTS.md")"
assert "R-7 AGENTS links setup guide"     "$(grep_q 'workstation-setup.md' "${TARGET}/AGENTS.md")"

# R-8: no template-named artifacts; trainee-named identity; no FILL-IN; hooks memory
assert "R-8 no .code-workspace"           "$(hasnt "${TARGET}/home-training-template.code-workspace")"
assert "R-8 no martin-template identity"  "$(hasnt "${TARGET}/workspaces/identity/martin-template-coder.identity.yaml")"
assert "R-8 trainee identity present"     "$(has "${TARGET}/workspaces/identity/trainee.identity.yaml")"
assert "R-8 identity no FILL_IN"          "$(grep_absent 'FILL_IN' "${TARGET}/workspaces/identity/trainee.identity.yaml")"
assert "R-8 instructions no FILL IN"      "$(grep_absent 'FILL IN' "${TARGET}/.claude/instructions.md")"
assert "R-8 hooks-troubleshooting memory" "$(has "${TARGET}/memory/hooks-troubleshooting.md")"

# R-9: read guard hook present
assert "R-9 trainee-read-guard.py"        "$(has "${TARGET}/.claude/hooks/trainee-read-guard.py")"
assert "R-9 guardrails containment"       "$(grep_q 'sourceDocs' "${TARGET}/.claude/guardrails.md")"

# R-10: profile template canonical home in docs/ + seeded into the trainee dir
assert "R-10 docs/trainee-profile-template" "$(has "${TARGET}/docs/trainee-profile-template.md")"

# R-12: training output format (not podzoneTeam), operator = Trainee
assert "R-12 output-format training"      "$(grep_q 'outputDocs' "${TARGET}/.claude/output-format.md")"
assert "R-12 instructions operator Trainee" "$(grep_q 'Trainee' "${TARGET}/.claude/instructions.md")"

# R-13: dependency analysis + setup guide + preflight hook
assert "R-13 dependency-analysis.md"      "$(has "${TARGET}/docs/dependency-analysis.md")"
assert "R-13 workstation-setup.md"        "$(has "${TARGET}/docs/workstation-setup.md")"
assert "R-13 trainee-preflight.py"        "$(has "${TARGET}/.claude/hooks/trainee-preflight.py")"

# R-14: env slimmed — no telemetry remote / apex repo in settings
assert "R-14 no PODZONE_TELEMETRY_REMOTE" "$(grep_absent 'PODZONE_TELEMETRY_REMOTE' "${TARGET}/.claude/settings.json")"
assert "R-14 no PODZONEAGENTTEAM_REPO"    "$(grep_absent 'PODZONEAGENTTEAM_REPO' "${TARGET}/.claude/settings.json")"
assert "R-14 TRAINEE_RUNTIME set"         "$(grep_q 'TRAINEE_RUNTIME' "${TARGET}/.claude/settings.json")"

# settings.json valid JSON
assert "settings.json valid JSON"         "$(python3 -c "import json;json.load(open('${TARGET}/.claude/settings.json'))" 2>/dev/null && echo ok || echo fail)"

# R-11: artifact-hygiene gate passes on the clean generation
assert "R-11 hygiene gate PASS" "$(python3 "${AGENT_TOOLING_DIR}/tools/qa-snapshot.py" --source "$TARGET" --check-only >/dev/null 2>&1 && echo ok || echo fail)"

# R-8 personalisation: clone -> personalise -> Trainee/ becomes the handle, hygiene still PASS
cp -R "$TARGET" "$CLONE"
python3 "${AGENT_TOOLING_DIR}/tools/personalise-trainee.py" --repo "$CLONE" --handle testuser >/dev/null
assert "R-8 personalise renames dir"   "$(has "${CLONE}/Testuser")"
assert "R-8 personalise removes placeholder dir" "$(hasnt "${CLONE}/Trainee")"
assert "R-8 personalise identity file" "$(has "${CLONE}/workspaces/identity/testuser.identity.yaml")"
assert "R-8 personalised hygiene PASS"  "$(python3 "${AGENT_TOOLING_DIR}/tools/qa-snapshot.py" --source "$CLONE" --check-only >/dev/null 2>&1 && echo ok || echo fail)"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed."
[ "$FAIL" -eq 0 ]
