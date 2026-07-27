from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_write import get_campaign_write_side_effect_plans, reset_campaign_write_fixture_state
from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]
WRITE_MODEL = ROOT / "aicrm_next/extensions/growth/cloud_orchestrator/campaigns_write.py"


def test_campaign_write_model_has_no_real_send_runtime_or_http_clients():
    source = WRITE_MODEL.read_text(encoding="utf-8")
    forbidden = [
        "process" + "_due_campaign_members",
        "dispatch" + "_wecom_task",
        "upload" + "_media",
        "media" + "/upload",
        "requests.",
        "http" + "x",
        "campaign_execute_executed=True",
        "wecom_send_executed=True",
        "real_external_call_executed=True",
        "payment",
        "OpenClaw",
    ]
    for marker in forbidden:
        assert marker not in source


def test_start_and_batch_start_do_not_execute_send_or_runtime(monkeypatch):
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    reset_campaign_read_fixture_state()
    reset_campaign_write_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)

    for path, payload, key in [
        ("/api/admin/cloud-orchestrator/campaigns/camp_next_read_fixture/start", {}, "no-real-start"),
        ("/api/admin/cloud-orchestrator/campaigns/batch-start", {"campaign_codes": ["camp_next_read_fixture"]}, "no-real-batch"),
    ]:
        response = client.post(path, json=payload, headers={"Idempotency-Key": key})
        assert response.status_code == 200
        body = response.json()
        assert body["real_external_call_executed"] is False
        assert body["campaign_execute_executed"] is False
        assert body["wecom_send_executed"] is False
        assert body["side_effect_plan"]["adapter_mode"] == "real_blocked"
        assert body["side_effect_plan"]["campaign_execute_executed"] is False
        assert body["side_effect_plan"]["wecom_send_executed"] is False

    plans = get_campaign_write_side_effect_plans()
    assert len(plans) == 2
    assert all(plan["adapter_mode"] == "real_blocked" for plan in plans)
