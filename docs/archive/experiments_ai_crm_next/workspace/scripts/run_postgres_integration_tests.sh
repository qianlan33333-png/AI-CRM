#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python}"
fi
export PYTHONPATH="${PYTHONPATH:-src}"

if [[ -z "${AICRM_NEXT_TEST_DATABASE_URL:-}" ]]; then
  cat >&2 <<'EOF'
AICRM_NEXT_TEST_DATABASE_URL is required.

Example:
  AICRM_NEXT_TEST_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/aicrm_next_test \
    scripts/run_postgres_integration_tests.sh

Safety rules:
  - use a local PostgreSQL test database only
  - database name must contain "test"
  - never point this at production
EOF
  exit 2
fi

"$PYTHON_BIN" - <<'PY'
import os
from aicrm_next.platform.shared.postgres_test_guard import validate_postgres_test_database_url

safe = validate_postgres_test_database_url(os.environ["AICRM_NEXT_TEST_DATABASE_URL"])
print(f"PostgreSQL integration target: host={safe.host} database={safe.database} url={safe.redacted_url}")
PY

"$PYTHON_BIN" -m pytest -q -m postgres_integration
