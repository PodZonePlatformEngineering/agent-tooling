#!/usr/bin/env bash
# Non-blocking — hooks must never fail Claude Code
# Uses absolute path so this works both as project-level and user-level hook.
if [ -n "$PODZONE_QDRANT_APIKEY" ]; then
  python3 "${HOME}/.claude/hooks/ingest-transcript.py"
else
  secretctl run -k podzone_qdrant_apikey -- \
    python3 "${HOME}/.claude/hooks/ingest-transcript.py"
fi
exit 0