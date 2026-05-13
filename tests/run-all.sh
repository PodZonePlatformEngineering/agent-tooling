#!/usr/bin/env bash
# Run all primitive tests
set -euo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOTAL_FAIL=0

for test_script in "$TESTS_DIR"/*/*/test-*.sh "$TESTS_DIR"/*/test-*.sh; do
  [[ -f "$test_script" ]] || continue
  echo ""
  if bash "$test_script"; then
    :
  else
    ((TOTAL_FAIL++))
  fi
done

echo ""
if [[ $TOTAL_FAIL -eq 0 ]]; then
  echo "All tests passed."
else
  echo "$TOTAL_FAIL test script(s) failed."
  exit 1
fi
