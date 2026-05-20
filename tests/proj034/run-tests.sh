#!/usr/bin/env bash
# Run PROJ-034 foundation unit tests (T-002 resolver + T-003 scraper).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${DIR}/../.." && pwd)"
cd "${REPO_ROOT}"
python3 -m unittest discover -s tests/proj034 -p 'test_*.py' -v
