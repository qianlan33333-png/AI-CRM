from __future__ import annotations

from aicrm_next.external_effect_composition import build_external_effect_adapter_registry
import json
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from aicrm_next.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.cloud_orchestrator.run_due import (
    PlanCloudCampaignRunDueCommand,
    PreviewCloudCampaignRunDueCommand,
    execute_cloud_campaign_run_due_command,
    reset_run_due_fixture_state,
)
from aicrm_next.platform_foundation.external_effects import (
    AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK,
    WEBHOOK_ORDER_PAID_PUSH,
    WECOM_MESSAGE_PRIVATE_SEND,
    ExternalEffectService,
    reset_external_effect_fixture_state,
)
from aicrm_next.platform_foundation.external_effects.adapters import DEFAULT_ADAPTER_REGISTRY, WebhookAdapter
from aicrm_next.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.platform_foundation.auth_platform.webhook_hmac import WebhookHmacSigner
from tests.webhook_hmac_test_helpers import install_webhook_hmac_client


def _reset_fixture_state() -> None:
    reset_campaign_read_fixture_state()
    reset_run_due_fixture_state()
    reset_external_effect_fixture_state()


def _install_loopback_http_adapter(monkeypatch, client: TestClient) -> list[dict]:
    calls: list[dict] = []
    credentials = install_webhook_hmac_client(
        client,
        capability="external_effect_receipt_receive",
        client_id="pytest-aicrm-outbound",
    )
    signer = WebhookHmacSigner(client_id=credentials.client_id, secret=credentials.secret)

    def loopback_post(url, *, data, headers, timeout):
        calls.append({"url": url, "json": json.loads(data.decode("utf-8")), "headers": headers, "timeout": timeout})
        parsed = urlparse(url)
        with monkeypatch.context() as route_policy:
            route_policy.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
            return client.post(parsed.path, content=data, headers=headers)

    monkeypatch.setitem(DEFAULT_ADAPTER_REGISTRY._adapters, "outbound_webhook", WebhookAdapter(http_post=loopback_post, signer=signer))  # type: ignore[attr-defined]
    monkeypatch.setitem(DEFAULT_ADAPTER_REGISTRY._adapters, "webhook", WebhookAdapter(http_post=loopback_post, signer=signer))  # type: ignore[attr-defined]
    return calls


def test_ai_assist_campaign_run_due_preview_lists_candidates_without_external_effect_jobs() -> None:
    _reset_fixture_state()

    result = execute_cloud_campaign_run_due_command(
        PreviewCloudCampaignRunDueCommand(
            batch_size=1,
            source_route="/api/admin/cloud-orchestrator/campaigns/run-due/preview",
        )
    )
    items, total = ExternalEffectService().list_jobs({"effect_type": AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK})

    assert result["ok"] is True
    assert result["source_status"] == "next_run_due_preview"
    assert result["candidate_count"] == 1
    assert result["external_effect_job_ids"] == []
    assert result["planned_count"] == 0
    assert result["real_external_call_executed"] is False
    assert result["wecom_send_executed"] is False
    assert total == 0
    assert items == []


def test_ai_assist_campaign_run_due_plan_creates_shadow_external_effect_jobs_without_wecom_send() -> None:
    _reset_fixture_state()

    result = execute_cloud_campaign_run_due_command(
        PlanCloudCampaignRunDueCommand(
            batch_size=2,
            source_route="/api/admin/cloud-orchestrator/campaigns/run-due",
            idempotency_key="ai-assist-run-due-shadow-plan",
            trace_id="trace_ai_assist_shadow_plan",
        )
    )
    service = ExternalEffectService()
    items, total = service.list_jobs({"effect_type": AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK, "business_type": "ai_assist_campaign"})

    assert result["ok"] is True
    assert result["source_status"] == "next_run_due_plan"
    assert result["candidate_count"] == 2
    assert result["planned_count"] == 2
    assert result["external_effect_planned_count"] == 2
    assert len(result["external_effect_job_ids"]) == 2
    assert result["real_external_call_executed"] is False
    assert result["wecom_send_executed"] is False
    assert total == 2
    assert {item.status for item in items} == {"planned"}
    assert {item.execution_mode for item in items} == {"shadow"}
    assert all(item.adapter_name == "outbound_webhook" for item in items)
    assert all(item.target_type == "campaign_member" for item in items)
    assert all(item.payload_summary_json["webhook_url_present"] is False for item in items)


def test_ai_assist_campaign_loopback_execute_succeeds_with_test_receiver(next_client: TestClient, monkeypatch) -> None:
    _reset_fixture_state()
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_TEST_RECEIVER_ENABLED", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK)
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_SIGNING_SECRET", "ai-assist-loopback-secret")
    calls = _install_loopback_http_adapter(monkeypatch, next_client)

    plan = execute_cloud_campaign_run_due_command(
        PlanCloudCampaignRunDueCommand(
            batch_size=1,
            source_route="/api/admin/cloud-orchestrator/campaigns/run-due",
            idempotency_key="ai-assist-loopback-plan",
            trace_id="trace_ai_assist_loopback",
            test_only=True,
            test_receiver_base_url="https://crm.example.test",
            receiver_response_status=200,
        )
    )
    job_id = plan["external_effect_job_ids"][0]
    service = ExternalEffectService()
    job = service.get(job_id)
    assert job is not None
    assert job.status == "queued"
    assert job.execution_mode == "execute"
    assert job.payload_json["execution_scope"] == "test_loopback"
    assert job.payload_json["webhook_url"] == "https://crm.example.test/api/external-effects/test-receiver"

    preview = ExternalEffectWorker().preview_due(batch_size=1, effect_types=[AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK], test_only=True)
    dry_run = ExternalEffectWorker().run_due(batch_size=1, dry_run=True, effect_types=[AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK], test_only=True)
    receipts_before, total_before = service.list_test_receipts({"job_id": job_id}, limit=10)
    executed = ExternalEffectWorker().run_due(batch_size=1, dry_run=False, effect_types=[AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK], test_only=True)
    updated = service.get(job_id)
    attempts = service.list_attempts(job_id)
    receipts, total = service.list_test_receipts({"job_id": job_id}, limit=10)

    assert preview["counts"]["candidate_count"] == 1
    assert dry_run["real_external_call_executed"] is False
    assert receipts_before == []
    assert total_before == 0
    assert executed["real_external_call_executed"] is True
    assert updated is not None
    assert updated.status == "succeeded"
    assert attempts[0].status == "succeeded"
    assert attempts[0].response_summary_json["real_external_call_executed"] is True
    assert len(calls) == 1
    assert total == 1
    assert receipts[0].effect_type == AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK
    assert receipts[0].trace_id == job.trace_id
    assert receipts[0].signature_valid is True
    assert receipts[0].payload_hash == job.payload_json["expected_payload_hash"]


def test_ai_assist_campaign_run_due_api_injects_current_host_loopback_url(next_client: TestClient, monkeypatch) -> None:
    _reset_fixture_state()
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_TEST_RECEIVER_ENABLED", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK)

    response = next_client.post(
        "/api/admin/cloud-orchestrator/campaigns/run-due",
        headers={
            "Authorization": "Bearer timer-token",
            "Idempotency-Key": "ai-assist-api-loopback-host",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "crm.example.test",
        },
        json={
            "batch_size": 1,
            "test_only": True,
            "webhook_url": "https://attacker.example.com/should-not-be-used",
        },
    )
    body = response.json()
    job = ExternalEffectService().get(body["external_effect_job_ids"][0])

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["real_external_call_executed"] is False
    assert body["wecom_send_executed"] is False
    assert job is not None
    assert job.payload_json["webhook_url"] == "https://crm.example.test/api/external-effects/test-receiver"
    assert "attacker.example.com" not in job.payload_json["webhook_url"]


def test_ai_assist_campaign_run_due_api_env_test_mode_injects_loopback_url(next_client: TestClient, monkeypatch) -> None:
    _reset_fixture_state()
    monkeypatch.setenv("AI_ASSIST_EXTERNAL_EFFECT_TEST_MODE", "1")

    response = next_client.post(
        "/api/admin/cloud-orchestrator/campaigns/run-due",
        headers={
            "Authorization": "Bearer timer-token",
            "Idempotency-Key": "ai-assist-api-env-loopback",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "crm.example.test",
        },
        json={"batch_size": 1},
    )
    body = response.json()
    job = ExternalEffectService().get(body["external_effect_job_ids"][0])

    assert response.status_code == 200
    assert body["real_external_call_executed"] is False
    assert body["wecom_send_executed"] is False
    assert job is not None
    assert job.status == "queued"
    assert job.execution_mode == "execute"
    assert job.payload_json["execution_scope"] == "test_loopback"
    assert job.payload_json["webhook_url"] == "https://crm.example.test/api/external-effects/test-receiver"


def test_ai_assist_campaign_loopback_allowlist_miss_blocks_without_receipt(next_client: TestClient, monkeypatch) -> None:
    _reset_fixture_state()
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_TEST_RECEIVER_ENABLED", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", WEBHOOK_ORDER_PAID_PUSH)
    calls = _install_loopback_http_adapter(monkeypatch, next_client)

    plan = execute_cloud_campaign_run_due_command(
        PlanCloudCampaignRunDueCommand(
            batch_size=1,
            source_route="/api/admin/cloud-orchestrator/campaigns/run-due",
            idempotency_key="ai-assist-loopback-allowlist-miss",
            test_only=True,
            test_receiver_base_url="https://crm.example.test",
        )
    )
    job_id = plan["external_effect_job_ids"][0]
    blocked = ExternalEffectWorker().run_due(batch_size=1, dry_run=False, effect_types=[AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK], test_only=True)
    service = ExternalEffectService()
    updated = service.get(job_id)
    attempts = service.list_attempts(job_id)
    receipts, total = service.list_test_receipts({"job_id": job_id}, limit=10)

    assert blocked["counts"]["blocked_count"] == 1
    assert blocked["real_external_call_executed"] is False
    assert updated is not None
    assert updated.status == "blocked"
    assert attempts[0].error_code == "effect_type_not_allowed"
    assert calls == []
    assert receipts == []
    assert total == 0


def test_ai_assist_campaign_run_due_wecom_private_mode_creates_approval_gated_external_effect_job(monkeypatch) -> None:
    _reset_fixture_state()
    monkeypatch.setenv("AI_ASSIST_EXTERNAL_EFFECT_SEND_MODE", "wecom_private")

    result = execute_cloud_campaign_run_due_command(
        PlanCloudCampaignRunDueCommand(
            batch_size=1,
            source_route="/api/admin/cloud-orchestrator/campaigns/run-due",
            idempotency_key="ai-assist-wecom-private-plan",
            trace_id="trace_ai_assist_wecom_private_plan",
        )
    )
    service = ExternalEffectService()
    items, total = service.list_jobs({"effect_type": WECOM_MESSAGE_PRIVATE_SEND, "business_type": "ai_assist_campaign"})
    job = items[0]

    assert result["ok"] is True
    assert result["external_effect_job_ids"] == [job.id]
    assert result["real_external_call_executed"] is False
    assert result["wecom_send_executed"] is False
    assert total == 1
    assert job.status == "planned"
    assert job.requires_approval is True
    assert job.execution_mode == "execute"
    assert job.adapter_name == "wecom_private_message"
    assert job.target_type == "unionid"
    assert job.target_id == "union_fixture_a"
    assert job.payload_json["owner_userid"] == "owner_fixture"
    assert job.payload_json["target_unionid"] == "union_fixture_a"
    assert job.payload_json["external_userids"] == ["wm_fixture_a"]
    assert job.payload_json["content_text"] == "fixture hello"


def test_wecom_private_external_effect_default_is_blocked_without_typed_gate(monkeypatch) -> None:
    _reset_fixture_state()
    monkeypatch.setenv("AI_ASSIST_EXTERNAL_EFFECT_SEND_MODE", "wecom_private")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS", "HuangYouCan")
    calls: list[dict] = []

    class _Adapter:
        def create_private_message_task(self, payload, *, idempotency_key=""):
            calls.append({"payload": payload, "idempotency_key": idempotency_key})
            return {
                "ok": True,
                "side_effect_executed": True,
                "exact_target_verified": True,
                "requested_external_userids": ["wm_fixture_a"],
                "wecom_msgid": "msg-default-direct-send",
            }

    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: _Adapter())
    plan = execute_cloud_campaign_run_due_command(
        PlanCloudCampaignRunDueCommand(
            batch_size=1,
            source_route="/api/admin/cloud-orchestrator/campaigns/run-due",
            idempotency_key="ai-assist-wecom-private-disabled",
        )
    )
    job_id = plan["external_effect_job_ids"][0]
    ExternalEffectService().approve(job_id)

    result = ExternalEffectWorker().run_due(batch_size=1, dry_run=False, effect_types=[WECOM_MESSAGE_PRIVATE_SEND], test_only=False)
    updated = ExternalEffectService().get(job_id)
    attempts = ExternalEffectService().list_attempts(job_id)

    assert result["counts"]["blocked_count"] == 1
    assert result["real_external_call_executed"] is False
    assert calls == []
    assert updated is not None
    assert updated.status == "blocked"
    assert attempts[0].status == "blocked"
    assert attempts[0].error_code == "wecom_execution_disabled"


def test_wecom_private_external_effect_allowlisted_execute_succeeds(monkeypatch) -> None:
    _reset_fixture_state()
    monkeypatch.setenv("AI_ASSIST_EXTERNAL_EFFECT_SEND_MODE", "wecom_private")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", WECOM_MESSAGE_PRIVATE_SEND)
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TARGET_EXTERNAL_USERIDS", "wm_fixture_a")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS", "owner_fixture")
    monkeypatch.setenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", "allowlisted_canary")
    calls: list[dict] = []

    class _Adapter:
        def create_private_message_task(self, payload, *, idempotency_key=""):
            calls.append({"payload": payload, "idempotency_key": idempotency_key})
            return {
                "ok": True,
                "mode": "production",
                "side_effect_executed": True,
                "exact_target_verified": True,
                "requested_external_userids": ["wm_fixture_a"],
                "wecom_msgid": "msg-ai-assist-real-queue",
                "error_code": "",
                "error_message": "",
            }

    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: _Adapter())
    plan = execute_cloud_campaign_run_due_command(
        PlanCloudCampaignRunDueCommand(
            batch_size=1,
            source_route="/api/admin/cloud-orchestrator/campaigns/run-due",
            idempotency_key="ai-assist-wecom-private-success",
        )
    )
    job_id = plan["external_effect_job_ids"][0]
    service = ExternalEffectService()
    pending = service.get(job_id)
    assert pending is not None
    authorized = service.authorize_allowlisted_canary(
        job_id,
        actor="pytest",
        reason="explicit AI assist canary target confirmation",
        expected_version=pending.row_version,
    )
    assert authorized is not None
    service.approve(job_id)

    worker = ExternalEffectWorker(adapter_registry=build_external_effect_adapter_registry())
    preview = worker.preview_due(batch_size=1, effect_types=[WECOM_MESSAGE_PRIVATE_SEND], test_only=False)
    dry_run = worker.run_due(batch_size=1, dry_run=True, effect_types=[WECOM_MESSAGE_PRIVATE_SEND], test_only=False)
    result = worker.run_due(batch_size=1, dry_run=False, effect_types=[WECOM_MESSAGE_PRIVATE_SEND], test_only=False)
    updated = ExternalEffectService().get(job_id)
    attempts = ExternalEffectService().list_attempts(job_id)

    assert preview["counts"]["candidate_count"] == 1
    assert dry_run["real_external_call_executed"] is False
    assert result["counts"]["succeeded_count"] == 1
    assert result["real_external_call_executed"] is True
    assert len(calls) == 1
    assert calls[0]["payload"]["sender"] == "owner_fixture"
    assert calls[0]["payload"]["external_userids"] == ["wm_fixture_a"]
    assert calls[0]["payload"]["text"] == {"content": "fixture hello"}
    assert updated is not None
    assert updated.status == "succeeded"
    assert attempts[0].status == "succeeded"
    assert attempts[0].response_summary_json["wecom_send_executed"] is True


def test_wecom_private_external_effect_cannot_self_authorize_allowlist_miss(monkeypatch) -> None:
    _reset_fixture_state()
    monkeypatch.setenv("AI_ASSIST_EXTERNAL_EFFECT_SEND_MODE", "wecom_private")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", WECOM_MESSAGE_PRIVATE_SEND)
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TARGET_EXTERNAL_USERIDS", "wm_other")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS", "owner_fixture")
    monkeypatch.setenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", "allowlisted_canary")
    calls: list[dict] = []

    class _Adapter:
        def create_private_message_task(self, payload, *, idempotency_key=""):
            calls.append({"payload": payload, "idempotency_key": idempotency_key})
            return {
                "ok": True,
                "side_effect_executed": True,
                "exact_target_verified": True,
                "requested_external_userids": ["wm_fixture_a"],
                "wecom_msgid": "msg-target-allowlist-independent",
            }

    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: _Adapter())
    plan = execute_cloud_campaign_run_due_command(
        PlanCloudCampaignRunDueCommand(
            batch_size=1,
            source_route="/api/admin/cloud-orchestrator/campaigns/run-due",
            idempotency_key="ai-assist-wecom-private-target-miss",
        )
    )
    job_id = plan["external_effect_job_ids"][0]
    service = ExternalEffectService()
    pending = service.get(job_id)
    assert pending is not None
    assert (
        service.authorize_allowlisted_canary(
            job_id,
            actor="pytest",
            reason="target is intentionally outside the canary allowlist",
            expected_version=pending.row_version,
        )
        is None
    )
    service.approve(job_id)

    result = ExternalEffectWorker(
        adapter_registry=build_external_effect_adapter_registry()
    ).run_due(batch_size=1, dry_run=False, effect_types=[WECOM_MESSAGE_PRIVATE_SEND], test_only=False)
    updated = ExternalEffectService().get(job_id)
    attempts = ExternalEffectService().list_attempts(job_id)

    assert result["counts"]["succeeded_count"] == 0
    assert result["real_external_call_executed"] is False
    assert calls == []
    assert updated is not None
    assert updated.status == "blocked"
    assert attempts[0].status == "blocked"
    assert attempts[0].error_code == "wecom_execution_scope_not_allowlisted_canary"


def test_ai_assist_campaign_loopback_test_only_false_is_rejected(monkeypatch) -> None:
    _reset_fixture_state()
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK)

    plan = execute_cloud_campaign_run_due_command(
        PlanCloudCampaignRunDueCommand(
            batch_size=1,
            source_route="/api/admin/cloud-orchestrator/campaigns/run-due",
            idempotency_key="ai-assist-loopback-test-only-required",
            test_only=True,
            test_receiver_base_url="https://crm.example.test",
        )
    )
    rejected = ExternalEffectWorker().run_due(batch_size=1, dry_run=False, effect_types=[AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK], test_only=False)
    receipts, total = ExternalEffectService().list_test_receipts({"job_id": plan["external_effect_job_ids"][0]}, limit=10)

    assert rejected["ok"] is False
    assert rejected["error"] == "test_only_required"
    assert rejected["real_external_call_executed"] is False
    assert receipts == []
    assert total == 0
