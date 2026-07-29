from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from wechat_identity_test_support import authorize_wechat_client


def test_h5_submit_production_unavailable_does_not_use_fixture_or_legacy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://questionnaire-h5:questionnaire-h5@127.0.0.1:1/aicrm")
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("AICRM_NEXT_ACTION_TOKEN_SECRET", "questionnaire-production-unavailable-test")

    client = TestClient(create_app())
    authorize_wechat_client(client)
    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={"answers": {"q_activation": "activated"}},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["source_status"] == "production_unavailable"
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
    assert "X-AICRM-Compatibility-Facade" not in response.headers


def test_h5_diagnostics_production_unavailable_does_not_use_fixture_or_legacy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://questionnaire-h5:questionnaire-h5@127.0.0.1:1/aicrm")
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")

    response = TestClient(create_app()).post(
        "/api/h5/questionnaires/hxc-activation-v1/client-diagnostics",
        json={"level": "error", "message": "production unavailable"},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["source_status"] == "production_unavailable"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
    assert "X-AICRM-Compatibility-Facade" not in response.headers
