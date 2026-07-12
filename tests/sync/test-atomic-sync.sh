#!/usr/bin/env bash
# Test: atomic-rename self-update (PROJ-039/T-098, CC-408)
#
# Live incident (home-podzone-hermes 2026-07-11): TOOLING_UPDATE sync ran
# `cp src dst` over a session-start.sh that a concurrent SessionStart hook
# was EXECUTING. Plain cp rewrites dst's EXISTING inode, so the running
# interpreter's open fd saw the bytes shift mid-read → spurious syntax error.
#
# This test simulates an in-flight reader:
#   1. Control: plain `cp` over a file with an open fd — the reader sees
#      MIXED old/new bytes (red on plain cp; documents the bug class).
#   2. Real sync path, hook file: a reader holding an fd across a full
#      `sync-agent-tooling.sh --yes` run sees the OLD content to EOF while
#      the path serves the NEW content, and the inode has changed.
#   3. Real sync path, dependency-dir file (lib/): same guarantee through
#      the directory-swap variant.
set -euo pipefail

AGENT_TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="/tmp/test-atomic-sync-$$"
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

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT
mkdir -p "$WORK"

echo "=== test-atomic-sync.sh ==="

# The in-flight reader: opens <path>, reads a first chunk, signals ready,
# then blocks until <go> exists, reads the SAME fd to EOF, and writes
# everything it saw to <out>. Sequencing is strict (chunk → writer runs →
# rest), so the result is deterministic, not a timing race.
READER="${WORK}/inflight-reader.py"
cat > "$READER" <<'PYEOF'
import os, sys, time
path, ready, go, out = sys.argv[1:5]
with open(path, "rb") as fh:
    first = fh.read(64)
    with open(ready, "w") as r:
        r.write("1")
    while not os.path.exists(go):
        time.sleep(0.05)
    rest = fh.read()
with open(out, "wb") as o:
    o.write(first + rest)
PYEOF

# run_reader <path> <tag>: start the reader on <path>, wait until it holds
# the fd (ready flag), and export READER_PID/GO/OUT for the caller.
run_reader() {
  local path="$1" tag="$2"
  GO="${WORK}/${tag}.go"
  OUT="${WORK}/${tag}.out"
  local ready="${WORK}/${tag}.ready"
  rm -f "$GO" "$OUT" "$ready"
  python3 "$READER" "$path" "$ready" "$GO" "$OUT" &
  READER_PID=$!
  for _ in $(seq 1 100); do
    [[ -f "$ready" ]] && break
    sleep 0.05
  done
  [[ -f "$ready" ]]
}

finish_reader() {
  touch "$GO"
  wait "$READER_PID"
}

# --- 1. Control: plain cp corrupts an in-flight reader (the bug class) ---

OLD_FILE="${WORK}/control-old"
NEW_FILE="${WORK}/control-new"
DST="${WORK}/control-dst"
# Sized like the incident: old ~8KB, new bigger, visibly different bytes.
python3 -c "open('$OLD_FILE','w').writelines('OLD %04d\n' % i for i in range(1000))"
python3 -c "open('$NEW_FILE','w').writelines('NEW %04d\n' % i for i in range(1200))"
cp "$OLD_FILE" "$DST"

run_reader "$DST" control
cp "$NEW_FILE" "$DST"   # the pre-T-098 write: same inode, rewritten in place
finish_reader
assert "control: plain cp over an open fd corrupts the in-flight reader (mixed bytes)" \
  "$(! diff -q "$OLD_FILE" "${WORK}/control.out" > /dev/null 2>&1 && echo ok || echo fail)"
assert "control: corrupted read contains NEW bytes after the old prefix" \
  "$(grep -q 'NEW 0' "${WORK}/control.out" && echo ok || echo fail)"

# --- 2. Real sync path: hook file (atomic_install) ---

TARGET="${WORK}/home"
NO_TELEMETRY_BOOTSTRAP=1 bash "${AGENT_TOOLING_DIR}/scaffold.sh" training atomicsync trainer --target-dir "$TARGET" > /dev/null

HOOK="${TARGET}/.claude/hooks/session-start.sh"
# Drift the hook so the sync must rewrite it; the drifted bytes play the role
# of the resident OLD version a running interpreter is mid-way through.
echo "# DRIFTED — resident old version an interpreter is executing" >> "$HOOK"
cp "$HOOK" "${WORK}/hook-old-snapshot"
INODE_BEFORE="$(ls -i "$HOOK" | awk '{print $1}')"

# Also drift a lib dependency file for part 3 — same sync run covers both.
LIBMOD="${TARGET}/.claude/lib/qdrant_http.py"
echo "# DRIFTED LIB — resident old version with a reader mid-stream" >> "$LIBMOD"
cp "$LIBMOD" "${WORK}/lib-old-snapshot"
LIB_INODE_BEFORE="$(ls -i "$LIBMOD" | awk '{print $1}')"

run_reader "$HOOK" hook
HOOK_GO="$GO"; HOOK_OUT="$OUT"; HOOK_PID="$READER_PID"
run_reader "$LIBMOD" lib
LIB_GO="$GO"; LIB_OUT="$OUT"; LIB_PID="$READER_PID"

SYNC_RC=0
bash "${AGENT_TOOLING_DIR}/sync-agent-tooling.sh" \
  --role trainer \
  --home-repo "$TARGET" \
  --agent-tooling "${AGENT_TOOLING_DIR}" \
  --yes > "${WORK}/sync-out" 2>&1 || SYNC_RC=$?
assert "sync run over held-open files exits 0" "$([ "$SYNC_RC" -eq 0 ] && echo ok || echo fail)"

touch "$HOOK_GO"; wait "$HOOK_PID"
touch "$LIB_GO";  wait "$LIB_PID"

assert "in-flight reader saw the OLD hook content to EOF, byte-identical" \
  "$(diff -q "${WORK}/hook-old-snapshot" "$HOOK_OUT" > /dev/null 2>&1 && echo ok || echo fail)"
assert "hook path now serves the NEW (source) content" \
  "$(diff -q "${AGENT_TOOLING_DIR}/hooks/session-start.sh" "$HOOK" > /dev/null 2>&1 && echo ok || echo fail)"
assert "hook inode changed (rename swapped in a new inode)" \
  "$([ "$(ls -i "$HOOK" | awk '{print $1}')" != "$INODE_BEFORE" ] && echo ok || echo fail)"
assert "renamed hook is executable (mode preserved through the temp)" \
  "$([ -x "$HOOK" ] && echo ok || echo fail)"

# --- 3. Real sync path: dependency dir (atomic_install_dir) ---

assert "in-flight reader saw the OLD lib content to EOF, byte-identical" \
  "$(diff -q "${WORK}/lib-old-snapshot" "$LIB_OUT" > /dev/null 2>&1 && echo ok || echo fail)"
assert "lib path now serves the NEW (source) content" \
  "$(diff -q "${AGENT_TOOLING_DIR}/lib/qdrant_http.py" "$LIBMOD" > /dev/null 2>&1 && echo ok || echo fail)"
assert "lib inode changed (dir swap delivered a new inode)" \
  "$([ "$(ls -i "$LIBMOD" | awk '{print $1}')" != "$LIB_INODE_BEFORE" ] && echo ok || echo fail)"

# --- 4. Stale staging debris swept + gitignored (T-098 hardening) ---

STALE="${TARGET}/.claude/hooks/session-start.sh.sync-tmp.99999"
echo "debris from a crashed sync" > "$STALE"
bash "${AGENT_TOOLING_DIR}/sync-agent-tooling.sh" \
  --role trainer --home-repo "$TARGET" --agent-tooling "${AGENT_TOOLING_DIR}" \
  --yes > /dev/null 2>&1
assert "stale *.sync-tmp* debris swept at sync start" \
  "$([ ! -e "$STALE" ] && echo ok || echo fail)"
assert "synced .gitignore covers *.sync-tmp*" \
  "$(grep -q '^\*\.sync-tmp\*$' "${TARGET}/.gitignore" && echo ok || echo fail)"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed."
[[ $FAIL -eq 0 ]]
