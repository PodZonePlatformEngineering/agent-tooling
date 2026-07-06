#!/usr/bin/env bash
# Tests for the --out-dir flag on the three reporting tools
# (usage-report.py, efficiency-report.py, rollup-report.py).
# Verifies:
#   - default output dir preserved when --out-dir is not given
#   - --out-dir redirects output to the chosen directory
#   - filename derivation logic is unchanged
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="${SCRIPT_DIR}/../../tools"

PASS=0; FAIL=0
ok()   { echo "  PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

TMPROOT="$(mktemp -d)"
trap 'rm -rf "${TMPROOT}"' EXIT

run_py() {
  local tool="$1"; shift
  python3 - "$tool" "$@" <<'PY'
import sys, importlib.util
from pathlib import Path

tool_path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("tool_mod", tool_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

mode = sys.argv[2]            # "default" or "out-dir"
target = Path(sys.argv[3])    # tmp dir to assert against / pass

# Override the real default dir to a sandbox so the test doesn't touch
# the real podzoneTeam/team/hermes/outgoing/usage-reports/.
default_sandbox = target / "_default_sandbox"
mod.DEFAULT_OUTPUT_DIR = default_sandbox

# Common kwargs: inject empty payloads to avoid hitting Qdrant.
kw = {"days": 7, "stdout_only": False, "dry_run": False}

# usage-report uses `points=`; the others use `payloads=`.
import inspect
sig = inspect.signature(mod.run)
if "points" in sig.parameters:
    kw["points"] = []
    kw["no_cleanup"] = True
else:
    kw["payloads"] = []

if mode == "out-dir":
    kw["out_dir"] = target / "explicit"

result = mod.run(**kw)
report_path = result["report_path"]
assert report_path is not None, "expected a report_path"

if mode == "default":
    assert default_sandbox in report_path.parents, (
        f"default mode: expected report under {default_sandbox}, got {report_path}"
    )
else:
    assert (target / "explicit") in report_path.parents, (
        f"out-dir mode: expected report under {target/'explicit'}, got {report_path}"
    )

# Filename derivation must be unchanged: still ends with the tool's suffix.
assert report_path.exists(), f"report not written: {report_path}"
assert report_path.suffix == ".md", f"unexpected suffix: {report_path}"
print(str(report_path))
PY
}

for tool in usage-report.py efficiency-report.py rollup-report.py; do
  echo "=== ${tool} ==="
  case "${tool}" in
    usage-report.py)      suffix="-usage-summary.md" ;;
    efficiency-report.py) suffix="-model-efficiency.md" ;;
    rollup-report.py)     suffix="-project-rollup.md" ;;
  esac

  TARGET="${TMPROOT}/${tool%.py}"
  mkdir -p "${TARGET}"

  if out=$(run_py "${TOOLS_DIR}/${tool}" "default" "${TARGET}" 2>&1); then
    if [[ "${out}" == *"${suffix}" ]]; then
      ok "${tool}: default path used, filename suffix preserved"
    else
      fail "${tool}: default path used but unexpected filename: ${out}"
    fi
  else
    fail "${tool}: default-mode invocation failed"; echo "${out}"
  fi

  if out=$(run_py "${TOOLS_DIR}/${tool}" "out-dir" "${TARGET}" 2>&1); then
    if [[ "${out}" == *"/explicit/"*"${suffix}" ]]; then
      ok "${tool}: --out-dir redirects output, filename suffix preserved"
    else
      fail "${tool}: --out-dir did not redirect as expected: ${out}"
    fi
  else
    fail "${tool}: --out-dir invocation failed"; echo "${out}"
  fi

  # Help text must mention the flag.
  if "${TOOLS_DIR}/${tool}" --help 2>&1 | grep -q -- "--out-dir"; then
    ok "${tool}: --help advertises --out-dir"
  else
    fail "${tool}: --help missing --out-dir"
  fi
done

echo ""
echo "  Results: ${PASS} passed, ${FAIL} failed"
[[ ${FAIL} -eq 0 ]]
