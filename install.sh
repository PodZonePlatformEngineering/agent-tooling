#!/usr/bin/env bash
# install.sh — install agent-tooling to the workstation.
# Pattern: trainingTeam/planning/projects/PROJ-007-trainee-onboarding/scripts/bootstrap.sh
set -euo pipefail

AGENT_TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="${HOME}/.claude/hooks"
PRIMITIVES_DIR="${HOME}/.claude/primitives"
WRAPPERS_DIR="${HOME}/.local/bin"

echo "==> Installing Python dependencies..."
pip3 install -r "${AGENT_TOOLING_DIR}/requirements.txt"

echo "==> Installing hooks..."
mkdir -p "${HOOKS_DIR}"
cp "${AGENT_TOOLING_DIR}/hooks/"*.py "${HOOKS_DIR}/"
cp "${AGENT_TOOLING_DIR}/hooks/"*.sh "${HOOKS_DIR}/"
chmod +x "${HOOKS_DIR}/"*.sh

echo "==> Installing primitives (for hook relative-path resolution)..."
cp -r "${AGENT_TOOLING_DIR}/primitives/." "${PRIMITIVES_DIR}/"
find "${PRIMITIVES_DIR}" -name "*.sh" -exec chmod +x {} \;

echo "==> Installing primitive wrappers..."
mkdir -p "${WRAPPERS_DIR}"
find "${AGENT_TOOLING_DIR}/primitives" -name "*.sh" | while read -r script; do
  name="$(basename "${script}" .sh)"
  wrapper="${WRAPPERS_DIR}/${name}"
  cat > "${wrapper}" <<WRAPPER
#!/usr/bin/env bash
exec "${script}" "\$@"
WRAPPER
  chmod +x "${wrapper}"
  echo "    Installed: ${wrapper}"
done

echo "==> Updating skillDirectories..."
echo "    Set claude.skillDirectories to: ${AGENT_TOOLING_DIR}/skills"
echo "    (add to ~/.claude/settings.json manually or via /update-config)"

echo "==> Setting up claude_session_telemetry collection..."
bash "${AGENT_TOOLING_DIR}/hooks/setup-collection.sh"

echo "==> Hook scripts installed to ${AGENT_TOOLING_DIR}/hooks/"
echo "    Add the settings.json snippet from hooks/settings-snippet.json to ~/.claude/settings.json"

echo "==> Running auth-check..."
python3 -c "
import urllib.request
try:
    urllib.request.urlopen('http://localhost:11434/api/tags', timeout=5)
    print('  Ollama: ok')
except Exception as e:
    print(f'  Ollama: NOT REACHABLE ({e})')
import os
print('  PODZONE_QDRANT_APIKEY:', 'set' if os.getenv('PODZONE_QDRANT_APIKEY') else 'NOT SET')
print('  PODZONE_TELEGRAM_TEST_BOT:', 'set' if os.getenv('PODZONE_TELEGRAM_TEST_BOT') else 'NOT SET')
import pathlib
token = pathlib.Path.home() / '.config/podzone/gmail-token.json'
print('  Gmail token:', 'present' if token.exists() else 'NOT FOUND')
"

echo ""
echo "Bootstrap complete."
