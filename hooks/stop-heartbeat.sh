#!/usr/bin/env bash
# Non-blocking — hooks must never fail Claude Code
if [ -n "$PODZONE_QDRANT_APIKEY" ]; then
  python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/stop-heartbeat.py"
else
  secretctl run -k podzone_qdrant_apikey -- \
    python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/stop-heartbeat.py"
fi
exit 0