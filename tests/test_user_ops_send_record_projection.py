from __future__ import annotations

from aicrm_next.ops_enrollment.send_record_projection import build_send_record_external_effect_projection
from aicrm_next.platform.platform_foundation.external_effects import (
    ExternalEffectService,
    InMemoryExternalEffectRepository,
    WECOM_MESSAGE_PRIVATE_SEND,
)


def _plan_job(service: ExternalEffectService, *, status: str, target_id: str, external_userid: str) -> dict:
    return service.plan_effect(
        effect_type=WECOM_MESSAGE_PRIVATE_SEND,
        adapter_name="wecom_private_message",
        operation="send_private_message",
        target_type="user_ops_customer",
        target_id=target_id,
        payload={
            "channel": "wecom_private",
            "target_unionid": target_id,
            "external_userids": [external_userid],
            "owner_userid": "ZhaoYanFang",
            "content_text": "hello",
        },
        business_type="user_ops_batch_send",
        business_id="user_ops_send_0001",
        source_module="ops_enrollment",
        requires_approval=False,
        status=status,
        idempotency_key=f"user-ops-projection:{target_id}:{status}",
    )


def test_user_ops_send_record_projection_aggregates_external_effect_statuses(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_WECOM_EXECUTION_MODE", raising=False)
    service = ExternalEffectService(InMemoryExternalEffectRepository())
    _plan_job(service, status="succeeded", target_id="union_ops_001", external_userid="wx_ext_001")
    _plan_job(service, status="failed_terminal", target_id="union_ops_002", external_userid="wx_ext_002")
    _plan_job(service, status="blocked", target_id="union_ops_003", external_userid="wx_ext_003")
    _plan_job(service, status="queued", target_id="union_ops_004", external_userid="wx_ext_004")

    projection = build_send_record_external_effect_projection("user_ops_send_0001", service=service)

    assert projection["status"] == "partially_succeeded"
    assert projection["succeeded_count"] == 1
    assert projection["failed_count"] == 1
    assert projection["blocked_count"] == 1
    assert projection["queued_count"] == 1
    assert projection["external_effect_job_ids"] == [1, 2, 3, 4]
    assert projection["task_results"][0]["target_unionid"] == "union_ops_001"
    assert projection["task_results"][0]["external_userid_masked"] == "wx_e***001"
