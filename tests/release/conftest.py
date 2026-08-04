from __future__ import annotations

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from aicrm_next.platform.shared.postgres_test_guard import validate_postgres_test_database_url


@pytest.fixture(scope="session")
def release_database_url() -> str:
    raw_url = str(os.environ.get("AICRM_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    safe = validate_postgres_test_database_url(raw_url)
    os.environ["DATABASE_URL"] = safe.raw_url
    os.environ["AICRM_TEST_DATABASE_URL"] = safe.raw_url
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/ops/bootstrap_database.py",
            "--database-url",
            safe.raw_url,
        ],
        text=True,
        capture_output=True,
        timeout=240,
    )
    if completed.returncode:
        pytest.fail("\n".join(part for part in (completed.stdout, completed.stderr) if part))
    return safe.raw_url


@pytest.fixture(scope="session")
def release_app(release_database_url: str):
    os.environ.update(
        {
            "DATABASE_URL": release_database_url,
            "AICRM_TEST_DATABASE_URL": release_database_url,
            "AICRM_NEXT_ENV": "test",
            "AICRM_ROUTE_POLICY_ENFORCED": "false",
            "AICRM_ADMIN_AUTH_ENFORCED": "false",
            "SECRET_KEY": "current-release-test-secret",
            "WECHAT_SHOP_CALLBACK_TOKEN": "current-release-callback-token",
        }
    )
    from aicrm_next.main import create_app
    from aicrm_next.platform.shared.release import reset_release_sha_cache

    reset_release_sha_cache()
    return create_app()


@pytest.fixture(scope="session")
def release_client(release_app):
    with TestClient(release_app) as client:
        yield client
