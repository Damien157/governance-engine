#!/usr/bin/env bash
# Local mirror of .github/workflows/ci.yml (ruff + mypy + all-works tests).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY="${PYTHON:-python}"
fi
if [[ -x "$ROOT/.venv/bin/ruff" ]]; then
  RUFF="$ROOT/.venv/bin/ruff"
else
  RUFF=ruff
fi
if [[ -x "$ROOT/.venv/bin/mypy" ]]; then
  MYPY="$ROOT/.venv/bin/mypy"
else
  MYPY=mypy
fi

echo "==> ruff check src/governed_stack"
"$RUFF" check src/governed_stack

echo "==> mypy src/governed_stack"
"$MYPY" src/governed_stack

echo "==> run_all_works_tests"
exec "$PY" scripts/run_all_works_tests.py

# Full bug-finding harness (unittest + pytest lie/safety maths):
#   "$PY" scripts/test_harness.py
