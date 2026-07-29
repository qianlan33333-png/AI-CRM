from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.extensions.growth.cloud_orchestrator.run_due import reset_run_due_fixture_state
from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]
RUN_DUE = ROOT / "aicrm_next/extensions/growth/cloud_orchestrator/run_due.py"


def test_run_due_module_has_no_legacy_scheduler_send_or_http_clients():
    source = RUN_DUE.read_text(encoding="utf-8")
    forbidden = [
        "process" + "_due_campaign_members",
        "WeComClient" + ".from_app",
        "send" + "_message",
        "dispatch" + "_wecom_task",
        "request" + "s.",
        "http" + "x",
        "access" + "_token",
        "real_external_call_executed=True",
        '"real_external_call_executed": True',
        "campaign_runtime_executed=True",
        '"campaign_runtime_executed": True',
        "automation_runtime_executed=True",
        '"automation_runtime_executed": True',
        "wecom_send_executed=True",
        '"wecom_send_executed": True',
        "real_" + "enabled " + "def" + "ault",
        "def" + "ault " + "real_" + "enabled",
    ]
    for marker in forbidden:
        assert marker not in source


def test_run_due_routes_do_not_forward_to_legacy_or_execute_runtime(monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    reset_campaign_read_fixture_state()
    reset_run_due_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)
    for path in [
        "/api/admin/cloud-orchestrator/campaigns/run-due/preview",
        "/api/admin/cloud-orchestrator/campaigns/run-due",
    ]:
        idempotency_key = "run-due-no-legacy-preview" if path.endswith("/preview") else "run-due-no-legacy-plan"
        headers = {"Authorization": "Bearer test-token", "Idempotency-Key": idempotency_key}
        response = client.post(path, json={"batch_size": 10, "dry_run": True}, headers=headers)
        assert response.status_code == 200
        assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
        assert response.headers["X-AICRM-Fallback-Used"] == "false"
        assert "X-AICRM-Compatibility-Facade" not in response.headers
        body = response.json()
        assert body["route_owner"] == "ai_crm_next"
        assert body["fallback_used"] is False
        assert body["real_external_call_executed"] is False
        assert body["campaign_runtime_executed"] is False
        assert body["automation_runtime_executed"] is False
        assert body["wecom_send_executed"] is False
