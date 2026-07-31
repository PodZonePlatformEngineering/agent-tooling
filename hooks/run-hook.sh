#!/usr/bin/env bash
# run-hook.sh — the python3 entry shim for trainee hook commands
# (PROJ-011/T-128, CC-525; extends the T-121/T-125 guard from 1 command to all 11).
#
# Why this exists
# ---------------
# A trainee workstation with no `python3` (the fresh Windows 11 install Martin hit)
# cannot run ANY hook. T-125 put a shell guard on the preflight command only, so the
# trainee got one friendly message and then a raw `python3: command not found` on
# every prompt and every tool call for the rest of the session — worse, in feel, than
# the honest failure it replaced.
#
# The guard has to be at SHELL level (a Python preflight cannot catch its own
# interpreter being missing), and it has to be on all eleven invocations. Eleven
# inline guards would be eleven places to forget the next time a hook is added, in a
# settings.json that a single inline guard already blew up from 6 lines to 76. So the
# guard lives here, once, and every trainee hook command routes through it. A hook
# added later is covered by construction.
#
# Contract
# --------
#   bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/run-hook.sh [--announce] <path-under-.claude> [args...]
#
# * python3 present  -> `exec python3 <.claude/path> "$@"`. `exec` REPLACES this
#   shell, so the hook's exit code, stdout, stderr and stdin are the process's own,
#   byte-for-byte and code-for-code. That is what keeps the PreToolUse contract
#   (exit 2 + stderr = deny; exit 0 + JSON stdout = allow/context) and the
#   UserPromptSubmit contract (exit 0 + stdout = added context) intact. This shim
#   NEVER reads stdin — the hook payload JSON must arrive at the Python process
#   unconsumed.
# * python3 absent   -> print the plain-English message IF (and only if) --announce
#   was passed, then exit 0. Everything else stays silent, so the message is seen
#   ONCE per session (--announce is carried by exactly one command: the first
#   SessionStart hook) instead of on every prompt and every tool call.
#
# The missing-interpreter path is this shim's ONLY job. It never suppresses, retries
# or rewrites anything else: if python3 is there, the shim is a transparent exec.
#
# Cwd-independence (PROJ-039/T-050/T-055): the script resolves .claude/ from its OWN
# location, not from $PWD, and the caller locates the shim itself via
# $CLAUDE_PROJECT_DIR — so both legs stay cwd-independent, and the shim is findable
# with no Python involved.
#
# EXTERNAL-COMMAND-FREE on the failure path: everything below the python3 check uses
# bash builtins only (no `dirname`, no `basename`). A machine missing python3 may be
# missing more than python3, and the T-125 regression test runs this with an emptied
# PATH — an external command here would print its own `command not found` noise on
# exactly the path whose whole purpose is a clean message.

set -u

ANNOUNCE=0
if [ "${1:-}" = "--announce" ]; then
  ANNOUNCE=1
  shift
fi

if [ "$#" -lt 1 ]; then
  echo "run-hook.sh: usage: run-hook.sh [--announce] <path-under-.claude> [args...]" >&2
  exit 1
fi

SCRIPT_REL="$1"
shift

if ! command -v python3 >/dev/null 2>&1; then
  if [ "$ANNOUNCE" = "1" ]; then
    echo 'Python 3 is not installed on this machine, so none of this training repo automation can run (no session branch, no saved work, no progress records). Nothing here is broken and you can still talk to the trainee, but say so plainly at the start of the session and ask them to install Python 3 with their trainer before the next one - see docs/workstation-setup.md.'
  fi
  exit 0
fi

# .claude/ is the parent of hooks/, where this shim lives. Derived from $0 (builtin
# parameter expansion, not `dirname`) so the hook works from any working directory
# and needs no env var of its own.
SELF_DIR="${0%/*}"
[ "$SELF_DIR" = "$0" ] && SELF_DIR="."
CLAUDE_DIR="${SELF_DIR}/.."

exec python3 "${CLAUDE_DIR}/${SCRIPT_REL}" "$@"
