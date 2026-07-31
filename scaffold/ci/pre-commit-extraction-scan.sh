#!/usr/bin/env bash
# Extraction gate — pre-commit hook, ADVISORY (PROJ-011/T-126).
#
# Install:  ln -s ../../.agent-tooling/scaffold/ci/pre-commit-extraction-scan.sh \
#             .git/hooks/pre-commit
#   (or copy it; adjust AGENT_TOOLING below if the checkout lives elsewhere)
#
# Why advisory and not authoritative: this runs at the gate's actual firing moment
# (§1 — the commit into the destination, not the merge), which is the useful place
# for FEEDBACK. It is not the enforcement point, because it is bypassable with
# --no-verify and is not installed on every agent workstation. CI on the pull request
# is the enforcing instance.
#
# Exit behaviour: blocks the commit on tier-1/tier-2 findings. Bypass deliberately
# with `git commit --no-verify` — and if you do, the declaration you write is the
# only remaining record that you considered the gate at all.
set -euo pipefail

AGENT_TOOLING="${AGENT_TOOLING:-$HOME/workspace/agent-tooling}"
SCANNER="$AGENT_TOOLING/tools/extraction-scan.py"

if [[ ! -f "$SCANNER" ]]; then
  echo "extraction-scan: scanner not found at $SCANNER — skipping (set AGENT_TOOLING)" >&2
  exit 0
fi

staged=$(git diff --cached --name-only --diff-filter=ACMR)
[[ -z "$staged" ]] && exit 0

# shellcheck disable=SC2086
if python3 "$SCANNER" --paths $staged --repo "$(git rev-parse --show-toplevel)" \
     --fail-on both --quiet-warnings; then
  exit 0
fi

cat >&2 <<'EOF'

The extraction gate blocked this commit. The unit of enforcement is the commit into
the destination, not the merge: April 2026's leak WAS caught at PR review, and it
still cost a repo migration, because the data was already in history by then.

Fix the findings, or bypass with `git commit --no-verify` if they are wrong — and
say so in the declaration.
EOF
exit 1
