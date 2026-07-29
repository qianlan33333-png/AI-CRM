from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.forms.questionnaire.admin_write import get_questionnaire_admin_write_side_effect_plans


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    return TestClient(create_app())


def test_questionnaire_admin_write_external_effects_create_plans_only(client: TestClient) -> None:
    create = client.post(
        "/api/admin/questionnaires",
        json={
            "title": "外部计划问卷",
            "external_push_config": {"enabled": True, "webhook_url": "https://example.invalid/hook"},
        },
    )
    assert create.status_code == 200
    assert create.json()["real_external_call_executed"] is False
    assert create.json()["side_effect_plan"]["effect_type"] == "questionnaire.external_push.configure"
    assert create.json()["side_effect_plan"]["adapter_mode"] == "real_blocked"

    publish = client.post(f"/api/admin/questionnaires/{create.json()['questionnaire_id']}/publish", json={})
    assert publish.status_code == 200
    assert publish.json()["side_effect_plan"]["effect_type"] == "questionnaire.public_projection.publish"
    assert publish.json()["side_effect_plan"]["real_external_call_executed"] is False

    plans = get_questionnaire_admin_write_side_effect_plans()
    assert [plan["effect_type"] for plan in plans] == [
        "questionnaire.external_push.configure",
        "questionnaire.public_projection.publish",
    ]
    assert all(plan["adapter_mode"] == "real_blocked" for plan in plans)
    assert all(plan["real_external_call_executed"] is False for plan in plans)


def test_questionnaire_admin_write_module_does_not_import_real_external_adapters() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [Path("aicrm_next/extensions/forms/questionnaire/admin_write.py"), Path("aicrm_next/extensions/forms/questionnaire/api.py")]
    )

    forbidden = [
        "create_questionnaire_in_legacy",
        "update_questionnaire_in_legacy",
        "delete_questionnaire_in_legacy",
        "set_questionnaire_enabled_in_legacy",
        "export_questionnaire_from_legacy",
        "X-AICRM-Compatibility-Facade",
        '"fallback_used": True',
        "'fallback_used': True",
        '"real_external_call_executed": True',
        "'real_external_call_executed': True",
        "requests.post(",
        "httpx.post(",
    ]
    assert [token for token in forbidden if token in combined] == []
