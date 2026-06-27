#!/usr/bin/env bash
# Non-blocking — hooks must never fail Claude Code
# Resolve ingest-transcript.py next to THIS script (SCRIPT_DIR), so the hook works
# both workstation-global (~/.claude/hooks/) and home-repo-resident (.claude/hooks/)
# — self-containment per PROJ-039/T-011 C2b (no hardcoded $HOME path). Mirrors the
# other substrate hooks' SCRIPT_DIR idiom.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "$PODZONE_QDRANT_APIKEY" ]; then
  python3 "${SCRIPT_DIR}/ingest-transcript.py"
else
  secretctl run -k podzone_qdrant_apikey -- \
    python3 "${SCRIPT_DIR}/ingest-transcript.py"
fi
exit 0