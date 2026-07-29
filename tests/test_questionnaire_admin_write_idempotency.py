from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.forms.questionnaire.admin_write import get_questionnaire_admin_write_audit_events


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    return TestClient(create_app())


def test_questionnaire_admin_write_idempotency_reuses_command_result(client: TestClient) -> None:
    headers = {"Idempotency-Key": "questionnaire-write-idempotent-create"}
    payload = {"title": "幂等问卷", "description": "same request"}

    first = client.post("/api/admin/questionnaires", json=payload, headers=headers)
    second = client.post("/api/admin/questionnaires", json=payload, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["command_id"] == first.json()["command_id"]
    assert second.json()["questionnaire_id"] == first.json()["questionnaire_id"]
    assert second.json()["idempotency_key"] == headers["Idempotency-Key"]

    audit_events = get_questionnaire_admin_write_audit_events()
    assert [event["command_id"] for event in audit_events].count(first.json()["command_id"]) == 1
