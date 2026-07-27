from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.automation.ops_enrollment.application import reset_user_ops_fixture_state


def test_marketing_broadcast_preview_uses_next_user_ops_plan(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    reset_user_ops_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)
    first = client.get("/api/admin/user-ops/customers?limit=1").json()["items"][0]

    response = client.post(
        "/api/admin/user-ops/broadcast/preview",
        json={"message": {"text": "hello"}, "selection_mode": "manual", "selected_ids": [first["id"]]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
    assert body["side_effect_plan"]["real_external_call_executed"] is False
    assert body["side_effect_plan"]["adapter_mode"] == "real_blocked"
