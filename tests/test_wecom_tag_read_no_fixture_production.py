from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.crm.customer_tags.read_model import TagCatalog, TagCatalogRepository
from aicrm_next.main import create_app


def test_wecom_tag_read_returns_degraded_empty_payload_without_production_projection(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "wecom-tag-read-production-unavailable-test")

    client = TestClient(create_app(), raise_server_exceptions=False)
    for path in ["/api/admin/wecom/tags", "/api/admin/wecom/tag-groups"]:
        response = client.get(path)
        payload = response.json()

        assert response.status_code == 200
        assert payload["ok"] is True
        assert payload["degraded"] is True
        assert payload["error_code"] == "production_read_unavailable"
        assert payload["source_status"] == "production_unavailable"
        assert payload["read_model_status"] == "unavailable"
        assert payload["route_owner"] == "ai_crm_next"
        assert payload["fallback_used"] is False
        assert payload["real_external_call_executed"] is False
        assert payload["sync_executed"] is False
        assert payload["fixture_used"] is False
        assert payload["items"] == []
        assert payload["groups"] == []
        assert payload["page_error"] == "当前未获取到企微标签，可手工填写 tag_id"
        assert "X-AICRM-Compatibility-Facade" not in response.headers


def test_empty_production_projection_is_controlled_empty_state() -> None:
    class EmptyProductionTagCatalogRepository(TagCatalogRepository):
        source_status = "production_postgres_tag_catalog"
        read_model_status = "primary"

        def list_catalog(self) -> TagCatalog:
            return TagCatalog(groups=[], tags=[], source_status=self.source_status, read_model_status=self.read_model_status)

    payload = EmptyProductionTagCatalogRepository().list_catalog().to_payload()

    assert payload["ok"] is True
    assert payload["source_status"] == "production_postgres_tag_catalog"
    assert payload["read_model_status"] == "primary"
    assert payload["fixture_used"] is False
    assert payload["items"] == []
