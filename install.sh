#!/usr/bin/env bash
# Installs agent-tooling hooks and primitives to the workstation.
set -euo pipefail

AGENT_TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="$HOME/.claude/hooks"

mkdir -p "$HOOKS_DIR"
cp "$AGENT_TOOLING_DIR/hooks/"*.py "$HOOKS_DIR/"
cp "$AGENT_TOOLING_DIR/hooks/"*.sh "$HOOKS_DIR/"
chmod +x "$HOOKS_DIR/"*.sh

echo "agent-tooling installed: hooks → $HOOKS_DIR"
echo "Update skillDirectories in ~/.claude/settings.json to: $AGENT_TOOLING_DIR/skills"
