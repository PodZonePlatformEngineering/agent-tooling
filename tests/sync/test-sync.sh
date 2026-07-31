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

# 2b. Simulate a pre-T-062 stale .gitignore (still ignoring *.log)
GITIGNORE="${TARGET}/.gitignore"
printf '*.log\n' >> "$GITIGNORE"
assert "stale .gitignore ignores *.log before sync" \
  "$(grep -q '^\*\.log$' "$GITIGNORE" && echo ok || echo fail)"

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

# 4b-ii. .gitignore synced back to canonical (PROJ-039/T-062): *.log dropped
assert ".gitignore restored to source (no longer ignores *.log)" \
  "$(diff -q "${AGENT_TOOLING_DIR}/scaffold/gitignore.template" "$GITIGNORE" > /dev/null 2>&1 && echo ok || echo fail)"
assert "stale *.log entry removed from .gitignore" \
  "$(! grep -q '^\*\.log$' "$GITIGNORE" && echo ok || echo fail)"
assert ".gitignore still ignores .env" \
  "$(grep -q '^\.env$' "$GITIGNORE" && echo ok || echo fail)"
assert ".gitignore still ignores .claude/settings.local.json" \
  "$(grep -q '^\.claude/settings\.local\.json$' "$GITIGNORE" && echo ok || echo fail)"

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
assert "manifest root_files carries .gitignore sha256 matching source (T-062)" \
  "$(python3 -c "
import hashlib, json
d = json.load(open('${MANIFEST}'))
want = hashlib.sha256(open('${AGENT_TOOLING_DIR}/scaffold/gitignore.template', 'rb').read()).hexdigest()
exit(0 if d.get('root_files', {}).get('.gitignore') == want else 1)
" && echo ok || echo fail)"

# 4e. lifecycle_mode (PROJ-039/T-084): absent on a fresh sync (default "branch"
# is a reader default, not a written value); an operator-set flag survives a
# re-sync unchanged (sync must carry it forward, never derive/reset it).
assert "manifest carries no lifecycle_mode key on a fresh sync" \
  "$(python3 -c "import json; d=json.load(open('${MANIFEST}')); exit(0 if 'lifecycle_mode' not in d else 1)" && echo ok || echo fail)"
python3 -c "
import json
p = '${MANIFEST}'
d = json.load(open(p))
d['lifecycle_mode'] = 'trunk'
json.dump(d, open(p, 'w'), indent=2, sort_keys=True)
"
bash "${AGENT_TOOLING_DIR}/sync-agent-tooling.sh" \
  --role trainer \
  --home-repo "$TARGET" \
  --agent-tooling "${AGENT_TOOLING_DIR}" \
  --yes > /dev/null 2>&1
assert "lifecycle_mode: trunk survives a re-sync unchanged" \
  "$(python3 -c "import json; d=json.load(open('${MANIFEST}')); exit(0 if d.get('lifecycle_mode')=='trunk' else 1)" && echo ok || echo fail)"

# 5. Unchanged hooks not touched (stop.sh should still match)
assert "stop.sh still matches source" \
  "$(diff -q "${AGENT_TOOLING_DIR}/hooks/stop.sh" "${TARGET}/.claude/hooks/stop.sh" > /dev/null 2>&1 && echo ok || echo fail)"

# 5b. Team-lead operating manual (PROJ-039/T-081): rendered per-repo, drift
# restored on sync, in the manifest root_files, byte-identical across re-sync.
LEAD="/tmp/test-sync-lead-$$"
NO_TELEMETRY_BOOTSTRAP=1 bash "${AGENT_TOOLING_DIR}/scaffold.sh" training leadsync team-lead --target-dir "$LEAD" > /dev/null
LEAD_MANUAL="${LEAD}/OPERATING-MANUAL.md"
assert "team-lead scaffold emitted OPERATING-MANUAL.md" \
  "$([ -f "$LEAD_MANUAL" ] && echo ok || echo fail)"
echo "# HAND EDIT" >> "$LEAD_MANUAL"
bash "${AGENT_TOOLING_DIR}/sync-agent-tooling.sh" \
  --role team-lead --home-repo "$LEAD" --agent-tooling "${AGENT_TOOLING_DIR}" \
  --yes > /tmp/test-sync-lead-out-$$ 2>&1
assert "hand-edited manual restored by sync" \
  "$(! grep -q 'HAND EDIT' "$LEAD_MANUAL" && echo ok || echo fail)"
assert "restored manual carries this repo's substitutions" \
  "$(grep -q 'home-training-leadsync' "$LEAD_MANUAL" && echo ok || echo fail)"
assert "sync output shows manual restored" \
  "$(grep -q 'Updated: OPERATING-MANUAL.md' /tmp/test-sync-lead-out-$$ && echo ok || echo fail)"
LEAD_MANIFEST="${LEAD}/.claude/tooling-manifest.json"
assert "manifest root_files carries OPERATING-MANUAL.md sha256 matching shipped file" \
  "$(python3 -c "
import hashlib, json
d = json.load(open('${LEAD_MANIFEST}'))
want = hashlib.sha256(open('${LEAD_MANUAL}', 'rb').read()).hexdigest()
exit(0 if d.get('root_files', {}).get('OPERATING-MANUAL.md') == want else 1)
" && echo ok || echo fail)"
cp "$LEAD_MANUAL" /tmp/test-sync-lead-manual-$$
bash "${AGENT_TOOLING_DIR}/sync-agent-tooling.sh" \
  --role team-lead --home-repo "$LEAD" --agent-tooling "${AGENT_TOOLING_DIR}" \
  --yes > /tmp/test-sync-lead-out2-$$ 2>&1
assert "manual byte-identical across re-sync (idempotent)" \
  "$(diff -q /tmp/test-sync-lead-manual-$$ "$LEAD_MANUAL" > /dev/null 2>&1 && echo ok || echo fail)"
assert "re-sync reports manual unchanged" \
  "$(grep -q 'OK    OPERATING-MANUAL.md — unchanged' /tmp/test-sync-lead-out2-$$ && echo ok || echo fail)"
rm -rf "$LEAD" /tmp/test-sync-lead-out-$$ /tmp/test-sync-lead-out2-$$ /tmp/test-sync-lead-manual-$$

# 5c. Non-team-lead sync does not create a manual
assert "trainer repo has no OPERATING-MANUAL.md after sync" \
  "$([ ! -f "${TARGET}/OPERATING-MANUAL.md" ] && echo ok || echo fail)"

# 5d. Home-repo operator README (PROJ-039/T-095): rendered per-repo for every
# non-trainee role, drift restored on sync, in the manifest root_files,
# byte-identical across re-sync; trainee's own README is untouched.
README_TARGET="/tmp/test-sync-readme-$$"
NO_TELEMETRY_BOOTSTRAP=1 bash "${AGENT_TOOLING_DIR}/scaffold.sh" training readmesync trainer --target-dir "$README_TARGET" > /dev/null
README_DST="${README_TARGET}/README.md"
assert "trainer scaffold emitted README.md" \
  "$([ -f "$README_DST" ] && echo ok || echo fail)"
echo "# HAND EDIT" >> "$README_DST"
bash "${AGENT_TOOLING_DIR}/sync-agent-tooling.sh" \
  --role trainer --home-repo "$README_TARGET" --agent-tooling "${AGENT_TOOLING_DIR}" \
  --yes > /tmp/test-sync-readme-out-$$ 2>&1
assert "hand-edited README restored by sync" \
  "$(! grep -q 'HAND EDIT' "$README_DST" && echo ok || echo fail)"
assert "restored README carries this repo's substitutions" \
  "$(grep -q 'home-training-readmesync' "$README_DST" && echo ok || echo fail)"
assert "sync output shows README restored" \
  "$(grep -q 'Updated: README.md' /tmp/test-sync-readme-out-$$ && echo ok || echo fail)"
README_MANIFEST="${README_TARGET}/.claude/tooling-manifest.json"
assert "manifest root_files carries README.md sha256 matching shipped file" \
  "$(python3 -c "
import hashlib, json
d = json.load(open('${README_MANIFEST}'))
want = hashlib.sha256(open('${README_DST}', 'rb').read()).hexdigest()
exit(0 if d.get('root_files', {}).get('README.md') == want else 1)
" && echo ok || echo fail)"
cp "$README_DST" /tmp/test-sync-readme-copy-$$
bash "${AGENT_TOOLING_DIR}/sync-agent-tooling.sh" \
  --role trainer --home-repo "$README_TARGET" --agent-tooling "${AGENT_TOOLING_DIR}" \
  --yes > /tmp/test-sync-readme-out2-$$ 2>&1
assert "README byte-identical across re-sync (idempotent)" \
  "$(diff -q /tmp/test-sync-readme-copy-$$ "$README_DST" > /dev/null 2>&1 && echo ok || echo fail)"
assert "re-sync reports README unchanged" \
  "$(grep -q 'OK    README.md — unchanged' /tmp/test-sync-readme-out2-$$ && echo ok || echo fail)"
rm -rf "$README_TARGET" /tmp/test-sync-readme-out-$$ /tmp/test-sync-readme-out2-$$ /tmp/test-sync-readme-copy-$$

# 5e. Trainee sync never touches its own README (trainee-owned, byte-untouched)
TRAINEE_SYNC_TARGET="/tmp/test-sync-readme-trainee-$$"
NO_TELEMETRY_BOOTSTRAP=1 bash "${AGENT_TOOLING_DIR}/scaffold.sh" podzone readmesynctrainee trainee --target-dir "$TRAINEE_SYNC_TARGET" > /dev/null
TRAINEE_README_BEFORE="$(cat "${TRAINEE_SYNC_TARGET}/README.md")"
bash "${AGENT_TOOLING_DIR}/sync-agent-tooling.sh" \
  --role trainee --home-repo "$TRAINEE_SYNC_TARGET" --agent-tooling "${AGENT_TOOLING_DIR}" \
  --yes > /tmp/test-sync-readme-trainee-out-$$ 2>&1
assert "trainee README unchanged by sync (byte-untouched)" \
  "$([ "$(cat "${TRAINEE_SYNC_TARGET}/README.md")" == "$TRAINEE_README_BEFORE" ] && echo ok || echo fail)"
assert "sync does not report a README sync step for trainee" \
  "$(! grep -q 'Syncing home-repo README' /tmp/test-sync-readme-trainee-out-$$ && echo ok || echo fail)"
rm -rf "$TRAINEE_SYNC_TARGET" /tmp/test-sync-readme-trainee-out-$$

# 5f. Pre-prune orphan-skill guard (PROJ-039/T-122, CC-520). The Athena case: a
# repo-local skill with NO canonical source in agent-tooling/skills/ must survive a
# --yes role-sync, be reported loudly, and NOT fail the invariant — while an
# out-of-subset skill that DOES have a canonical source is still pruned as before.
ORPH="/tmp/test-sync-orphan-$$"
NO_TELEMETRY_BOOTSTRAP=1 bash "${AGENT_TOOLING_DIR}/scaffold.sh" training orphansync trainer --target-dir "$ORPH" > /dev/null
mkdir -p "${ORPH}/.claude/skills/invented-locally"
echo "local work" > "${ORPH}/.claude/skills/invented-locally/SKILL.md"
# An out-of-subset skill that IS canonical (recoverable) — must still be pruned.
cp -R "${AGENT_TOOLING_DIR}/skills/session-end" "${ORPH}/.claude/skills/session-end"
ORPH_RC=0
bash "${AGENT_TOOLING_DIR}/sync-agent-tooling.sh" \
  --role trainer --home-repo "$ORPH" --agent-tooling "${AGENT_TOOLING_DIR}" \
  --yes > /tmp/test-sync-orphan-out-$$ 2>&1 || ORPH_RC=$?
assert "orphan skill (no canonical source) survives a --yes sync" \
  "$([ -f "${ORPH}/.claude/skills/invented-locally/SKILL.md" ] && echo ok || echo fail)"
assert "orphan skill content untouched" \
  "$(grep -q 'local work' "${ORPH}/.claude/skills/invented-locally/SKILL.md" && echo ok || echo fail)"
assert "sync reports the orphan loudly" \
  "$(grep -q 'ORPHAN SKILLS RETAINED' /tmp/test-sync-orphan-out-$$ && echo ok || echo fail)"
assert "orphan report names the skill" \
  "$(grep -q 'skills/invented-locally' /tmp/test-sync-orphan-out-$$ && echo ok || echo fail)"
assert "orphan report offers canonicalise + --prune-orphan-skills routes" \
  "$(grep -q -- '--prune-orphan-skills' /tmp/test-sync-orphan-out-$$ && echo ok || echo fail)"
assert "out-of-subset skill WITH a canonical source is still pruned" \
  "$([ ! -d "${ORPH}/.claude/skills/session-end" ] && echo ok || echo fail)"
assert "orphan does not fail the invariant (sync exit 0)" \
  "$([ "$ORPH_RC" -eq 0 ] && echo ok || echo fail)"
assert "invariant still PASSes with an orphan present" \
  "$(grep -q 'Byte-identity invariant: PASS' /tmp/test-sync-orphan-out-$$ && echo ok || echo fail)"
# Explicit opt-in deletes it.
bash "${AGENT_TOOLING_DIR}/sync-agent-tooling.sh" \
  --role trainer --home-repo "$ORPH" --agent-tooling "${AGENT_TOOLING_DIR}" \
  --yes --prune-orphan-skills > /tmp/test-sync-orphan-out2-$$ 2>&1
assert "--prune-orphan-skills deletes the orphan" \
  "$([ ! -d "${ORPH}/.claude/skills/invented-locally" ] && echo ok || echo fail)"
# T-122: the canonicalised trainer domain skill is delivered by the role sync.
assert "trainer sync delivers create-trainee-brief (canonicalised)" \
  "$(diff -rq "${AGENT_TOOLING_DIR}/skills/create-trainee-brief" "${ORPH}/.claude/skills/create-trainee-brief" > /dev/null 2>&1 && echo ok || echo fail)"
assert "domain skill is not a resident adjunct (.agents/skills/)" \
  "$([ ! -d "${ORPH}/.agents/skills/create-trainee-brief" ] && echo ok || echo fail)"
rm -rf "$ORPH" /tmp/test-sync-orphan-out-$$ /tmp/test-sync-orphan-out2-$$

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
