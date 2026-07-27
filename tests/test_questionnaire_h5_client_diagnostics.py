from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.forms.questionnaire.h5_write import (
    get_questionnaire_h5_client_diagnostics,
    get_questionnaire_h5_write_audit_events,
)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    return TestClient(create_app())


def test_h5_client_diagnostics_executes_next_commandbus_and_audit_ledger(client: TestClient) -> None:
    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/client-diagnostics",
        json={
            "level": "error",
            "message": "unit diagnostic",
            "user_agent": "pytest",
            "identity": {"openid": "openid_diag_001"},
        },
        headers={"Idempotency-Key": "h5-diagnostics-command"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["command_name"] == "questionnaire.h5.client_diagnostics"
    assert body["diagnostic_id"]
    assert body["questionnaire_id"] == 1
    assert body["resolved"] is True
    assert body["unresolved_slug"] is False
    assert body["source_status"] == "next_command"
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False

    records = get_questionnaire_h5_client_diagnostics()
    assert any(item["diagnostic_id"] == body["diagnostic_id"] for item in records)
    audit_events = get_questionnaire_h5_write_audit_events()
    assert body["command_id"] in {event["command_id"] for event in audit_events}


def test_h5_client_diagnostics_records_unresolved_slug(client: TestClient) -> None:
    response = client.post(
        "/api/h5/questionnaires/missing-slug/client-diagnostics",
        json={"level": "error", "message": "missing slug diagnostic"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["questionnaire_id"] is None
    assert body["resolved"] is False
    assert body["unresolved_slug"] is True
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
