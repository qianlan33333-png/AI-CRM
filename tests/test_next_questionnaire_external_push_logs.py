from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.platform.platform_foundation.external_effects import (
    WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
    ExternalEffectService,
    reset_external_effect_fixture_state,
)
from aicrm_next.extensions.forms.questionnaire.repo import (
    build_questionnaire_repository,
    reset_questionnaire_fixture_state,
)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    reset_questionnaire_fixture_state()
    reset_external_effect_fixture_state()
    return TestClient(create_app())


def _seed_log(*, status: str = "failed", user_id: str = "user-failed") -> dict:
    repository = build_questionnaire_repository()
    row = {
        "id": len(repository._external_push_logs) + 1,  # type: ignore[attr-defined]
        "questionnaire_id": 1,
        "questionnaire_title_snapshot": "黄小璨激活问卷",
        "submission_record_id": 1001,
        "user_id": user_id,
        "target_url": "https://hooks.example.com/questionnaire",
        "request_payload": {"user_id": user_id, "questionnaire_title": "黄小璨激活问卷"},
        "response_status_code": 500 if status == "failed" else 200,
        "response_body": '{"error":"upstream"}' if status == "failed" else '{"ok":true}',
        "status": status,
        "failure_reason": "HTTP 500" if status == "failed" else "",
        "created_at": "2026-07-12T00:00:00Z",
        "updated_at": "2026-07-12T00:00:00Z",
    }
    repository._external_push_logs.append(row)  # type: ignore[attr-defined]
    return dict(row)


def test_legacy_external_push_log_pages_are_read_only(client: TestClient) -> None:
    _seed_log()

    global_response = client.get("/admin/questionnaires/external-push-logs?status=failed_current")
    scoped_response = client.get("/admin/questionnaires/1/external-push-logs?status=failed_current")

    for response in (global_response, scoped_response):
        assert response.status_code == 200
        assert "X-AICRM-Compatibility-Facade" not in response.headers
        assert "外部推送记录" in response.text
        assert "只读排查入口" in response.text
        assert "旧补发入口已退休" in response.text
        assert "批量补发已选失败项" not in response.text
        assert "formaction=" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/admin/questionnaires/external-push-logs/1/retry",
        "/admin/questionnaires/external-push-logs/retry-batch",
        "/admin/questionnaires/1/external-push-logs/1/retry",
        "/admin/questionnaires/1/external-push-logs/retry-batch",
    ],
)
def test_legacy_external_push_retry_routes_are_removed(client: TestClient, path: str) -> None:
    _seed_log()

    response = client.post(path, headers={"Accept": "application/json"})
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH})

    assert response.status_code in {404, 405}
    assert jobs == []
    assert total == 0
    assert len(build_questionnaire_repository()._external_push_logs) == 1  # type: ignore[attr-defined]


def test_retired_external_push_source_contains_no_retry_planner_or_status_writer() -> None:
    service_source = Path("aicrm_next/extensions/forms/questionnaire/external_push_logs.py").read_text(encoding="utf-8")
    route_source = Path("aicrm_next/extensions/forms/questionnaire/admin_pages.py").read_text(encoding="utf-8")

    for marker in (
        "QuestionnaireExternalPushRetryService",
        "QuestionnaireExternalPushRetryCommand",
        "ExternalPushDeliveryAdapter",
        "ExternalEffectService",
        "create_external_push_log(",
        "questionnaire.external_push.retry",
    ):
        assert marker not in service_source
    repository_source = Path("aicrm_next/extensions/forms/questionnaire/repo.py").read_text(encoding="utf-8")
    assert "INSERT INTO questionnaire_external_push_logs" not in repository_source
    assert "def create_external_push_log" not in repository_source
    assert "external-push-logs/retry-batch" not in route_source
    assert "external-push-logs/{push_log_id:int}/retry" not in route_source
