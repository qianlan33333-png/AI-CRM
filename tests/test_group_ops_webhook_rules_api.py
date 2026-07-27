from __future__ import annotations

from aicrm_next.crm.identity_contact.dto import IdentityResolution, IdentityResolveResult
from tests.group_ops_test_helpers import group_ops_api_client  # noqa: F401


def error_code(response) -> str:
    body = response.json()
    detail = body.get("detail") if isinstance(body, dict) else {}
    return str(detail.get("error_code") or "") if isinstance(detail, dict) else ""


def _resolved_identity(request) -> IdentityResolveResult:
    return IdentityResolveResult(
        status="resolved",
        identity=IdentityResolution(
            person_id=None,
            external_userid=request.external_userid,
            mobile=None,
            unionid=request.unionid,
            binding_status="bound",
        ),
        candidate_count=1,
    )


def test_create_no_sop_webhook_receiver_uses_registered_message_signatures(group_ops_api_client):  # noqa: F811
    response = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={
            "name": "核心功能激活计划",
            "type": "webhook_receiver",
            "status": "disabled",
            "operatorMemberId": "HuangYouCan",
            "defaultActionType": "record_only",
            "allowNoSop": True,
            "description": "通过 Webhook 触发核心功能激活任务",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["planId"].startswith("plan_")
    assert body["type"] == "webhook_receiver"
    assert body["webhook"]["endpointKey"]
    assert body["webhook"]["authMode"] == "aicrm_hmac_sha256"
    assert "token" not in body["webhook"]

    detail = group_ops_api_client.get(f"/api/admin/automation-conversion/group-ops/plans/{body['id']}")
    assert detail.status_code == 200
    assert "token" not in detail.json()["plan"]["webhook"]
    assert detail.json()["plan"]["allowNoSop"] is True


def test_webhook_direct_recipients_idempotency_and_disabled_guard(group_ops_api_client):  # noqa: F811
    created = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={
            "name": "Webhook recipient plan",
            "type": "webhook_receiver",
            "status": "disabled",
            "operatorMemberId": "HuangYouCan",
            "defaultActionType": "record_only",
            "allowNoSop": True,
        },
    ).json()
    endpoint_key = created["webhook"]["endpointKey"]
    disabled = group_ops_api_client.post(
        f"/api/automation/group-ops/webhooks/{endpoint_key}",
        headers={"X-Idempotency-Key": "pytest-disabled"},
        json={
            "event": "core_feature_activation",
            "recipients": [{"external_user_id": "wmbNXyCwAAXhagLBNjtlFj2jbQevWinQ"}],
            "action": {"action_type": "record_only"},
        },
    )
    assert disabled.status_code == 409
    assert error_code(disabled) == "plan_not_active"

    enabled = group_ops_api_client.post(f"/api/admin/automation-conversion/group-ops/plans/{created['id']}/enable")
    assert enabled.status_code == 200

    first = group_ops_api_client.post(
        f"/api/automation/group-ops/webhooks/{endpoint_key}",
        headers={"X-Idempotency-Key": "pytest-direct-1"},
        json={
            "event": "core_feature_activation",
            "source": "pytest",
            "sender": {"operatorAccount": "HuangYouCan"},
            "recipients": [{"external_user_id": "wmbNXyCwAAXhagLBNjtlFj2jbQevWinQ"}],
            "action": {"action_type": "record_only"},
        },
    )
    duplicate = group_ops_api_client.post(
        f"/api/automation/group-ops/webhooks/{endpoint_key}",
        headers={"X-Idempotency-Key": "pytest-direct-1"},
        json={
            "event": "core_feature_activation",
            "recipients": [{"external_user_id": "wmbNXyCwAAXhagLBNjtlFj2jbQevWinQ"}],
            "action": {"action_type": "record_only"},
        },
    )

    assert first.status_code == 202
    assert first.json()["executed"] == 1
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True

    logs = group_ops_api_client.get(f"/api/admin/automation-conversion/group-ops/plans/{created['id']}/executions")
    assert logs.status_code == 200
    assert logs.json()["total"] == 1
    assert logs.json()["items"][0]["external_user_id"] == "wmbNXyCwAAXhagLBNjtlFj2jbQevWinQ"


def test_webhook_enqueue_action_routes_through_next_action_port(group_ops_api_client, monkeypatch):  # noqa: F811
    calls: list[dict] = []

    class FakeActionPort:
        def dispatch(self, input_data):
            calls.append(input_data)
            return {"ok": True, "status": "queued", "action_ref_id": "job_123", "side_effect_executed": False}

    monkeypatch.setattr(
        "aicrm_next.automation_engine.group_ops.action_port.build_group_ops_action_port",
        lambda: FakeActionPort(),
    )
    created = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={
            "name": "Webhook enqueue plan",
            "type": "webhook_receiver",
            "status": "disabled",
            "operatorMemberId": "HuangYouCan",
            "defaultActionType": "enqueue",
            "allowNoSop": True,
        },
    ).json()
    enabled = group_ops_api_client.post(f"/api/admin/automation-conversion/group-ops/plans/{created['id']}/enable")
    assert enabled.status_code == 200

    response = group_ops_api_client.post(
        f"/api/automation/group-ops/webhooks/{created['webhook']['endpointKey']}",
        headers={"X-Idempotency-Key": "pytest-enqueue-action"},
        json={
            "event": "core_feature_activation",
            "source": "pytest",
            "recipients": [{"external_user_id": "wm_enqueue_001"}],
            "action": {"action_type": "enqueue", "content": "queued content"},
        },
    )

    assert response.status_code == 202
    assert response.json()["executed"] == 1
    assert calls[0]["action"]["action_type"] == "enqueue"
    assert calls[0]["recipient"]["external_user_id"] == "wm_enqueue_001"


def test_rules_segmentation_and_missing_builtin_data_source_are_explicit(group_ops_api_client):  # noqa: F811
    rules = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/audience-rules")
    assert rules.status_code == 200
    assert "has_used_core_feature" in {item["rule_key"] for item in rules.json()["items"]}

    created = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={"name": "Rule plan", "type": "webhook_receiver", "operatorMemberId": "HuangYouCan"},
    ).json()
    bound = group_ops_api_client.put(
        f"/api/admin/automation-conversion/group-ops/plans/{created['id']}/segmentation",
        json={
            "segmentationType": "preset_rule",
            "ruleKey": "has_used_core_feature",
            "ruleVersion": 1,
            "params": {"lookback_days": 30},
            "layerActions": {"high_intent_not_used": {"actionType": "record_only"}},
        },
    )
    assert bound.status_code == 200

    preview = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/audience-rules/has_used_core_feature/preview",
        json={"planId": created["id"], "version": 1, "params": {"lookback_days": 30}, "limit": 20},
    )
    assert preview.status_code == 400
    assert error_code(preview) in {"rule_data_source_missing", "contract_error"}


def test_action_port_enqueue_uses_next_queue_gateway_and_exact_external_userid():
    from aicrm_next.automation_engine.group_ops.action_dispatcher import GroupOpsActionDispatcher
    from aicrm_next.automation_engine.group_ops.action_dispatcher import NextOutboundMessageQueueGateway
    from aicrm_next.automation_engine.group_ops.action_port import DefaultGroupOpsActionPort

    captured: dict = {}

    def fake_insert_job(**kwargs):
        captured.update(kwargs)
        return 123

    dispatcher = GroupOpsActionDispatcher(
        queue_gateway=NextOutboundMessageQueueGateway(insert_job=fake_insert_job),
        identity_resolver=_resolved_identity,
    )

    result = DefaultGroupOpsActionPort(dispatcher).dispatch(
        {
            "plan_id": 1,
            "trigger_event_id": "evt_001",
            "operator_member_id": "HuangYouCan",
            "recipient": {"external_user_id": "wmbNXyCwAAXhagLBNjtlFj2jbQevWinQ", "unionid": "union_group_ops_001"},
            "action": {"action_type": "enqueue", "content": "AI-CRM Webhook 触发测试消息"},
        }
    )

    assert result["ok"] is True
    assert result["status"] == "queued"
    assert result["side_effect_executed"] is False
    assert captured["command"].idempotency_key == "group_ops:1:evt_001:wmbNXyCwAAXhagLBNjtlFj2jbQevWinQ:enqueue"
    assert captured["payload"]["unionids"] == ["union_group_ops_001"]
    assert captured["payload"]["sender"] == "HuangYouCan"


def test_action_port_enqueue_duplicate_does_not_create_second_task():
    from aicrm_next.automation_engine.group_ops.action_dispatcher import GroupOpsActionDispatcher
    from aicrm_next.automation_engine.group_ops.action_dispatcher import NextOutboundMessageQueueGateway
    from aicrm_next.automation_engine.group_ops.action_port import DefaultGroupOpsActionPort

    seen: set[str] = set()

    def fake_insert_job(**kwargs):
        key = kwargs["command"].idempotency_key
        if key in seen:
            return 0
        seen.add(key)
        return 456

    dispatcher = GroupOpsActionDispatcher(
        queue_gateway=NextOutboundMessageQueueGateway(insert_job=fake_insert_job),
        identity_resolver=_resolved_identity,
    )
    payload = {
        "plan_id": 1,
        "trigger_event_id": "evt_dup",
        "operator_member_id": "HuangYouCan",
        "recipient": {"external_user_id": "wm_dup", "unionid": "union_dup"},
        "action": {"action_type": "enqueue", "content": "hello"},
    }

    first = DefaultGroupOpsActionPort(dispatcher).dispatch(payload)
    second = DefaultGroupOpsActionPort(dispatcher).dispatch(payload)

    assert first["status"] == "queued"
    assert second["status"] == "duplicate"
    assert len(seen) == 1


def test_action_port_publish_task_uses_next_queue_gateway():
    from aicrm_next.automation_engine.group_ops.action_dispatcher import GroupOpsActionDispatcher
    from aicrm_next.automation_engine.group_ops.action_dispatcher import NextOutboundMessageQueueGateway
    from aicrm_next.automation_engine.group_ops.action_port import DefaultGroupOpsActionPort

    captured: dict = {}

    def fake_insert_job(**kwargs):
        captured.update(kwargs)
        return 789

    dispatcher = GroupOpsActionDispatcher(
        queue_gateway=NextOutboundMessageQueueGateway(insert_job=fake_insert_job),
        identity_resolver=_resolved_identity,
    )

    result = DefaultGroupOpsActionPort(dispatcher).dispatch(
        {
            "plan_id": 2,
            "trigger_event_id": "evt_publish",
            "operator_member_id": "HuangYouCan",
            "recipient": {"external_user_id": "wm_publish", "unionid": "union_publish"},
            "action": {"action_type": "publish_task", "content": "publish task content"},
        }
    )

    assert result["ok"] is True
    assert result["status"] == "queued"
    assert result["side_effect_executed"] is False
    assert captured["command"].idempotency_key == "group_ops:2:evt_publish:wm_publish:publish_task"
    assert captured["payload"]["unionids"] == ["union_publish"]
    assert captured["payload"]["action"]["action_type"] == "publish_task"


def test_action_port_add_to_audience_records_audit_without_side_effect():
    from aicrm_next.automation_engine.group_ops.action_port import DefaultGroupOpsActionPort

    result = DefaultGroupOpsActionPort().dispatch(
        {
            "plan_id": 3,
            "trigger_event_id": "evt_audience",
            "operator_member_id": "HuangYouCan",
            "recipient": {"external_user_id": "wm_audience"},
            "action": {"action_type": "add_to_audience", "audience_id": "aud_high_intent"},
        }
    )

    assert result["ok"] is True
    assert result["status"] == "added"
    assert result["action_ref_id"] == "aud_high_intent"
    assert result["side_effect_executed"] is False
    assert result["audit"]["action_type"] == "add_to_audience"
    assert result["audit"]["external_userid"] == "wm_audience"


def test_action_port_missing_external_userid_returns_clear_error():
    from aicrm_next.automation_engine.group_ops.action_port import DefaultGroupOpsActionPort

    try:
        DefaultGroupOpsActionPort().dispatch(
            {
                "plan_id": 1,
                "trigger_event_id": "evt_missing",
                "operator_member_id": "HuangYouCan",
                "recipient": {},
                "action": {"action_type": "enqueue", "content": "hello"},
            }
        )
    except Exception as exc:
        assert "external_user_id is required for enqueue" in str(exc)
    else:
        raise AssertionError("missing external_userid must fail")
