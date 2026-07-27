from __future__ import annotations

import pytest


def require_fastapi():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from aicrm_next.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture()
def group_ops_api_client(monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    from aicrm_next.automation_engine.group_ops.repo import reset_group_ops_fixture_state
    from aicrm_next.platform.platform_foundation.external_effects import reset_external_effect_fixture_state

    reset_group_ops_fixture_state()
    reset_external_effect_fixture_state()
    return require_fastapi()


@pytest.fixture()
def group_ops_repo(monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    from aicrm_next.automation_engine.group_ops.repo import InMemoryGroupOpsRepository

    return InMemoryGroupOpsRepository()


def error_code(response) -> str:
    body = response.json()
    detail = body.get("detail") if isinstance(body, dict) else {}
    if isinstance(detail, dict):
        return str(detail.get("error_code") or "")
    return ""
