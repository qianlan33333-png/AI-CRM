from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.platform.shared.repository_provider import RepositoryProviderError
from tools import check_repository_provider_hardening as checker


def _production_env(monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://probe:probe@127.0.0.1:1/aicrm_probe")
    monkeypatch.delenv("AICRM_NEXT_ALLOW_FIXTURE_REPO_IN_PROD", raising=False)
    monkeypatch.setenv("SECRET_KEY", "repository-provider-hardening-test")


def test_create_app_does_not_reset_fixture_state_in_production(monkeypatch):
    _production_env(monkeypatch)
    module = importlib.import_module("aicrm_next.main")
    calls: list[str] = []
    names = [
        "reset_user_ops_fixture_state",
        "reset_questionnaire_fixture_state",
        "reset_automation_fixture_state",
        "reset_commerce_fixture_state",
        "reset_media_library_fixture_state",
        "reset_radar_links_fixture_state",
    ]
    old = {name: getattr(module, name) for name in names}
    for name in names:
        monkeypatch.setattr(module, name, lambda name=name: calls.append(name))

    module.create_app()

    assert calls == []
    for name, value in old.items():
        monkeypatch.setattr(module, name, value)


def test_fixture_repositories_allowed_in_fixture_mode(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)

    from aicrm_next.extensions.commerce.commerce.repo import build_commerce_repository
    from aicrm_next.engagement.media_library.repo import build_media_library_repository
    from aicrm_next.extensions.forms.questionnaire.repo import build_questionnaire_repository
    from aicrm_next.extensions.radar.radar_links.repo import build_radar_links_repository
    from aicrm_next.automation.automation_engine.repo import build_automation_repository
    from aicrm_next.insights.admin_read_model.repo import build_admin_read_repository

    assert build_commerce_repository().__class__.__name__.startswith("InMemory")
    assert build_media_library_repository().__class__.__name__.startswith("InMemory")
    assert build_questionnaire_repository().__class__.__name__.startswith("InMemory")
    assert build_radar_links_repository().__class__.__name__.startswith("InMemory")
    assert build_automation_repository().__class__.__name__.startswith("InMemory")
    assert build_admin_read_repository().__class__.__name__.startswith("LocalContract")


def test_production_data_ready_blocks_fixture_repository_builders(monkeypatch):
    _production_env(monkeypatch)

    from aicrm_next.extensions.commerce.commerce.repo import build_commerce_repository
    from aicrm_next.engagement.media_library.repo import build_media_library_repository
    from aicrm_next.extensions.forms.questionnaire.repo import build_questionnaire_repository
    from aicrm_next.extensions.radar.radar_links.repo import build_radar_links_repository
    from aicrm_next.automation.automation_engine.repo import build_automation_repository
    from aicrm_next.crm.customer_read_model.repo import build_customer_read_model_repository

    for builder in [
        build_commerce_repository,
        build_media_library_repository,
        build_questionnaire_repository,
        build_radar_links_repository,
        build_automation_repository,
        build_customer_read_model_repository,
    ]:
        try:
            repo = builder()
        except RepositoryProviderError:
            continue
        assert "InMemory" not in repo.__class__.__name__
        assert "Fixture" not in repo.__class__.__name__
        assert "LocalContract" not in repo.__class__.__name__


def test_allow_flag_does_not_make_checker_ok(monkeypatch):
    _production_env(monkeypatch)
    monkeypatch.setenv("AICRM_NEXT_ALLOW_FIXTURE_REPO_IN_PROD", "true")

    result = checker.run_check()

    assert result["ok"] is False
    assert "allow_fixture_repo_in_prod_enabled" in result["blockers"]


def test_production_fixture_route_does_not_return_fixture_success(monkeypatch):
    _production_env(monkeypatch)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/admin/wechat-pay/products")

    assert response.status_code != 200
    assert "course_masked_001" not in response.text
    assert "fixture" not in response.text.lower()
    payload = response.json()
    assert payload.get("ok") is False or payload.get("detail")


def test_repository_provider_checker_returns_ok(monkeypatch):
    monkeypatch.delenv("AICRM_NEXT_ALLOW_FIXTURE_REPO_IN_PROD", raising=False)

    result = checker.run_check()

    assert result["ok"] is True
    assert result["blockers"] == []
    assert "commerce" in result["capabilities"]
    assert "admin_read_model" in result["capabilities"]
