from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.crm.customer_tags.live_mutation import (
    get_wecom_tag_live_mutation_audit_events,
    get_wecom_tag_live_mutation_side_effect_plans,
)
from aicrm_next.main import create_app


def test_live_mutation_idempotency_key_reuses_command_result(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "wecom-live-mutation-idempotency")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    headers = {"Idempotency-Key": "same-live-mark-key"}
    payload = {"external_userid": "wx_ext_001", "tag_ids": ["tag_fixture_active"], "operator": "tester"}
    first = client.post("/api/admin/wecom/tags/live/mark", json=payload, headers=headers).json()
    second = client.post("/api/admin/wecom/tags/live/mark", json=payload, headers=headers).json()

    assert first["command_id"] == second["command_id"]
    assert first["side_effect_plan"]["side_effect_plan_id"] == second["side_effect_plan"]["side_effect_plan_id"]
    assert first["idempotency_key"] == "same-live-mark-key"
    assert len(get_wecom_tag_live_mutation_audit_events()) == 1
    assert len(get_wecom_tag_live_mutation_side_effect_plans()) == 1
