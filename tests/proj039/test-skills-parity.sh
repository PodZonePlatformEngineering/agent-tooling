#!/usr/bin/env bash
# test-skills-parity.sh — run-all.sh wrapper for the PROJ-039/T-034 skills parity guard.
# Runs the hermetic + real-repo unittest, then the sync-skills.sh --check invariant
# (the pre-consolidate-tasks guard) so both layers are exercised by ./tests/run-all.sh.
set -euo pipefail

AGENT_TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "=== test-skills-parity.sh ==="

echo "--- unittest: tests.proj039.test_skills_parity ---"
( cd "$AGENT_TOOLING_DIR" && python3 -m unittest tests.proj039.test_skills_parity )

echo "--- sync-skills.sh --check (byte-identity invariant) ---"
bash "${AGENT_TOOLING_DIR}/sync-skills.sh" --check

echo "PASS: skills parity"
