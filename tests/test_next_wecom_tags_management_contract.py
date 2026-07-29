from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_wecom_tag_management_js_keeps_api_and_actions_contract():
    source = (ROOT / "aicrm_next" / "crm" / "customer_tags" / "static" / "admin_console" / "wecom_tag_management.js").read_text(
        encoding="utf-8"
    )

    assert "/api/admin/wecom/tags" in source
    assert "/api/admin/wecom/tag-groups" in source
    assert 'data-form="create-group"' in source
    assert 'data-form="create-tag"' in source
    assert 'data-form="edit-group"' in source
    assert 'data-form="edit-tag"' in source
    assert "deleteCurrentGroup" in source
    assert "deleteTag" in source
    assert "copyTagId" in source
    assert "复制 tag_id" in source


def test_next_fixture_wecom_tags_api_returns_legacy_shape(monkeypatch):
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.delenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "next-wecom-tags-fixture-test")

    response = TestClient(create_app()).get("/api/admin/wecom/tags")
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert isinstance(payload["items"], list)
    assert isinstance(payload["groups"], list)
    assert payload["total_tags"] == len(payload["items"])
    assert payload["tag_limit"] == 1000
    assert payload["synced_at"]
    assert payload["source_status"] == "local_contract_probe"
    assert payload["read_model_status"] == "fixture"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
