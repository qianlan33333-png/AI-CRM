from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from wechat_identity_test_support import authorize_wechat_client


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    client = TestClient(create_app())
    authorize_wechat_client(client, {"openid": "openid_001", "unionid": "unionid_001", "external_userid": "wx_ext_001"})
    return client


def test_h5_submit_idempotency_reuses_first_command_result(client: TestClient) -> None:
    payload = {"answers": {"q_activation": "activated"}, "identity": {"mobile": "13800138000"}}

    first = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json=payload,
        headers={"Idempotency-Key": "h5-submit-idempotent"},
    )
    second = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={**payload, "answers": {"q_activation": "not_activated"}},
        headers={"Idempotency-Key": "h5-submit-idempotent"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["command_id"] == first.json()["command_id"]
    assert second.json()["submission_id"] == first.json()["submission_id"]
    assert second.json()["result"]["score"] == first.json()["result"]["score"]


def test_h5_diagnostics_idempotency_reuses_first_command_result(client: TestClient) -> None:
    first = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/client-diagnostics",
        json={"level": "error", "message": "first"},
        headers={"Idempotency-Key": "h5-diagnostics-idempotent"},
    )
    second = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/client-diagnostics",
        json={"level": "warning", "message": "second"},
        headers={"Idempotency-Key": "h5-diagnostics-idempotent"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["command_id"] == first.json()["command_id"]
    assert second.json()["diagnostic_id"] == first.json()["diagnostic_id"]
