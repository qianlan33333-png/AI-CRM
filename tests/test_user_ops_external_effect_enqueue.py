from __future__ import annotations

from aicrm_next.automation.ops_enrollment.effect_enqueue import UserOpsExternalEffectEnqueueGateway
from aicrm_next.platform.platform_foundation.external_effects import ExternalEffectService, WECOM_MESSAGE_PRIVATE_SEND, reset_external_effect_fixture_state


def test_user_ops_external_effect_gateway_creates_single_target_private_message_job() -> None:
    reset_external_effect_fixture_state()
    gateway = UserOpsExternalEffectEnqueueGateway()

    results = gateway.enqueue_wecom_private_message_jobs(
        record_id="user_ops_send_0001",
        targets=[
            {
                "unionid": "union_ops_001",
                "external_userid": "wx_ext_001",
                "owner_userid": "ZhaoYanFang",
                "customer_name": "张小蓝",
            }
        ],
        content="欢迎继续了解黄小璨课程",
        media_refs=[{"kind": "image", "index": 0}],
        operator="ops-admin",
        idempotency_key="idem-user-ops-001",
        command_id="cmd-user-ops-001",
        requires_approval=True,
    )

    assert results == [
        {
            "ok": True,
            "job_id": 1,
            "status": "planned",
            "idempotency_key": "user_ops_batch_send:idem-user-ops-001:union_ops_001",
            "target_unionid": "union_ops_001",
            "external_userid": "wx_ext_001",
            "error_code": "",
            "error_message": "",
        }
    ]
    job = ExternalEffectService().get(1)
    assert job is not None
    assert job.effect_type == WECOM_MESSAGE_PRIVATE_SEND
    assert job.adapter_name == "wecom_private_message"
    assert job.operation == "send_private_message"
    assert job.target_type == "user_ops_customer"
    assert job.target_id == "union_ops_001"
    assert job.business_type == "user_ops_batch_send"
    assert job.business_id == "user_ops_send_0001"
    assert job.source_module == "ops_enrollment"
    assert job.source_route == "/api/admin/user-ops/batch-send/execute"
    assert job.requires_approval is True
    assert job.payload_json["channel"] == "wecom_private"
    assert job.payload_json["target_unionid"] == "union_ops_001"
    assert job.payload_json["target_display_name"] == "张小蓝"
    assert job.payload_json["external_userids"] == ["wx_ext_001"]
    assert len(job.payload_json["external_userids"]) == 1
    assert job.payload_json["owner_userid"] == "ZhaoYanFang"
    assert job.payload_summary_json["real_external_call_executed"] is False


def test_user_ops_external_effect_gateway_is_idempotent_per_target() -> None:
    reset_external_effect_fixture_state()
    gateway = UserOpsExternalEffectEnqueueGateway()
    target = {"unionid": "union_ops_001", "external_userid": "wx_ext_001", "owner_userid": "ZhaoYanFang"}

    first = gateway.enqueue_wecom_private_message_jobs(
        record_id="user_ops_send_0001",
        targets=[target],
        content="hello",
        operator="ops-admin",
        idempotency_key="idem-user-ops-repeat",
    )
    second = gateway.enqueue_wecom_private_message_jobs(
        record_id="user_ops_send_0001",
        targets=[target],
        content="hello",
        operator="ops-admin",
        idempotency_key="idem-user-ops-repeat",
    )

    assert first[0]["job_id"] == second[0]["job_id"]
    jobs, total = ExternalEffectService().list_jobs({"business_type": "user_ops_batch_send", "business_id": "user_ops_send_0001"})
    assert total == 1
    assert jobs[0].id == first[0]["job_id"]
