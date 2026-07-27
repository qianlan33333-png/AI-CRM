from __future__ import annotations

from aicrm_next.crm.customer_read_model.application import ListCustomersQuery
from aicrm_next.crm.customer_read_model.dto import ListCustomersRequest


def test_production_mode_does_not_return_fixture_customer_success(monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://customer:customer@127.0.0.1:1/aicrm_customer")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("CUSTOMER_READ_MODEL_REPO_BACKEND", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ALLOW_FIXTURE_REPO_IN_PROD", raising=False)

    payload = ListCustomersQuery()(ListCustomersRequest(limit=10))

    assert payload["ok"] is False
    assert payload["source_status"] == "production_unavailable"
    assert payload["read_model_status"] == "unavailable"
    assert payload["customers"] == []
    assert "FixtureCustomerReadRepository" not in str(payload)
    assert "fixture repository" not in str(payload).lower()
    assert "local_contract_probe" not in str(payload)
    assert "张小蓝" not in str(payload)


def test_production_repo_uses_runtime_database_url(monkeypatch):
    from aicrm_next.crm.customer_read_model import repo as repo_module

    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgres://prod_user:prod_pass@db.internal:5432/prod_crm")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("CUSTOMER_READ_MODEL_REPO_BACKEND", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ALLOW_FIXTURE_REPO_IN_PROD", raising=False)

    dummy_session = object()
    factory_calls: list[object] = []

    def fake_get_session_factory(*, settings):
        factory_calls.append(settings)
        return lambda: dummy_session

    monkeypatch.setattr(repo_module, "get_session_factory", fake_get_session_factory)

    repository = repo_module.build_customer_read_model_repository()

    assert repository.__class__.__name__ == "SqlAlchemyCustomerReadModelRepository"
    assert factory_calls
