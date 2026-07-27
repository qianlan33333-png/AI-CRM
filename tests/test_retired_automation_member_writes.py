from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.channels.integration_gateway.questionnaire_adapters import QuestionnaireSubmitSideEffectGateway
from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_singular_activation_webhook_is_retired_without_automation_member_write(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.post("/api/customer-automation/activation-webhook", json={"mobile": "13800000000", "source": "legacy-singular"})

    assert response.status_code == 410
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"] == "legacy_customer_automation_retired"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["real_external_call_executed"] is False
    assert payload["automation_runtime_executed"] is False
    assert payload["outbound_webhook_executed"] is False


def test_customer_webhook_implementation_module_is_removed() -> None:
    assert not (ROOT / "aicrm_next/automation/automation_engine/customer_webhooks.py").exists()


def test_questionnaire_adapter_noops_retired_automation_member_projection() -> None:
    gateway = QuestionnaireSubmitSideEffectGateway()

    result = gateway.emit_automation_questionnaire_result(
        questionnaire={"id": 21},
        submission={"submission_id": "sub_001", "external_userid": "wm_test"},
        final_tags=["tag_interest_ai_tools"],
    )

    assert result["ok"] is True
    assert result["operation"] == "emit_automation_questionnaire_result"
    assert result["result"]["source_status"] == "retired_automation_member_noop"
    assert result["result"]["retired"] is True
    assert result["result"]["member_id"] == ""


def test_questionnaire_adapter_does_not_import_retired_automation_member_command() -> None:
    source = (ROOT / "aicrm_next/channels/integration_gateway/questionnaire_adapters.py").read_text(encoding="utf-8")

    assert "ApplyQuestionnaireResultCommand" not in source
    assert "ApplyQuestionnaireResultRequest" not in source
    assert "create_member_from_questionnaire" not in source
