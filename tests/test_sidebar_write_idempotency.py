from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.crm.sidebar_write import get_sidebar_write_audit_events, get_sidebar_write_projection_events
from tests.sidebar_auth_test_helpers import install_sidebar_auth


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    return TestClient(create_app())


def test_sidebar_write_idempotency_reuses_command_result_without_duplicate_write(client: TestClient) -> None:
    headers = install_sidebar_auth(
        client,
        viewer_userid="LiuXiao",
        external_userid="wx_ext_002",
    )
    headers["Idempotency-Key"] = "sidebar-write-idempotent-bind"
    payload = {"external_userid": "wx_ext_002", "mobile": "13800138123"}

    first = client.post("/api/sidebar/bind-mobile", json=payload, headers=headers)
    second = client.post("/api/sidebar/bind-mobile", json=payload, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["command_id"] == first.json()["command_id"]
    assert second.json()["idempotency_key"] == "sidebar-write-idempotent-bind"

    projections = get_sidebar_write_projection_events()
    audit_events = get_sidebar_write_audit_events()
    assert [item["command_id"] for item in projections].count(first.json()["command_id"]) == 1
    assert [item["command_id"] for item in audit_events].count(first.json()["command_id"]) == 1
