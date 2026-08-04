#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

/usr/local/bin/python3.10 "$ROOT_DIR/scripts/ci/bootstrap_test_env.py"
"$ROOT_DIR/.venv/bin/python" -m pytest -q "$@"
