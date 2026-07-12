#!/usr/bin/env bash
# Offline unit tests for the PROJ-011/T-031 training substrate:
# lib/training_substrate helpers + tools/training-jwt.py claim construction.
set -euo pipefail

AGENT_TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "== proj011: training substrate + JWT tool (offline) =="
( cd "$AGENT_TOOLING_DIR" && python3 -m unittest \
    tests.proj011.test_training_substrate \
    tests.proj011.test_training_jwt )
