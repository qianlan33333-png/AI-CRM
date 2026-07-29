from __future__ import annotations

import pytest

from aicrm_next.automation.automation_engine.repo import reset_automation_fixture_state


@pytest.fixture()
def client(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from aicrm_next.main import create_app

    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    reset_automation_fixture_state()
    return TestClient(create_app(), raise_server_exceptions=False)


def _assert_removed(response) -> None:
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/admin/automation-conversion/tasks"),
        ("post", "/api/admin/automation-conversion/tasks"),
        ("get", "/api/admin/automation-conversion/tasks/1"),
        ("put", "/api/admin/automation-conversion/tasks/1"),
        ("put", "/api/admin/automation-conversion/tasks/1/send-strategy"),
        ("put", "/api/admin/automation-conversion/tasks/1/send-content/unified"),
        ("put", "/api/admin/automation-conversion/tasks/1/send-content/profile-segments/early_founder"),
        ("put", "/api/admin/automation-conversion/tasks/1/send-content/behavior-segments/lt_2"),
        ("put", "/api/admin/automation-conversion/tasks/1/send-content/agent-materials"),
    ],
)
def test_legacy_automation_task_authoring_routes_are_retired(client, method: str, path: str) -> None:
    if method == "get":
        response = client.get(path)
    else:
        response = getattr(client, method)(path, json={"content_package": {"content_text": "旧任务配置"}})

    _assert_removed(response)


def test_behavior_segment_rules_route_is_removed_with_task_authoring(client) -> None:
    response = client.get("/api/admin/automation-conversion/behavior-segment-rules")

    _assert_removed(response)


def test_task_runner_route_is_removed(client) -> None:
    response = client.post("/api/admin/automation-conversion/tasks/run-due")

    _assert_removed(response)
