from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from aicrm_next.automation_engine.group_ops.action_dispatcher import (
    GroupOpsActionDispatcher,
    NextOutboundMessageQueueGateway,
)
from aicrm_next.automation_engine.group_ops.external_effects import (
    group_ops_effect_action_type,
    plan_group_ops_action_effect,
    plan_group_ops_external_effect,
)
from aicrm_next.automation_engine.group_ops.scheduler import run_group_ops_due_scheduler
from aicrm_next.background_jobs.automation_ops_scheduler import run_automation_ops_scheduler
from aicrm_next.identity_contact.dto import IdentityResolution, IdentityResolveResult
from aicrm_next.platform_foundation.external_effects import (
    GROUP_OPS_WEBHOOK_ACTION_LOOPBACK,
    WECOM_MESSAGE_GROUP_SEND,
)


ROOT = Path(__file__).resolve().parents[1]


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


class _DueRepo:
    def list_plans(self, _filters: dict[str, Any]):
        return [
            {
                "id": 1,
                "plan_type": "standard",
                "status": "active",
                "owner_userid": "owner_001",
                "created_at": "2026-07-12T08:00:00+08:00",
            }
        ], 1

    def list_bound_groups(self, _plan_id: int):
        return [
            {
                "chat_id": "chat_001",
                "status": "active",
                "created_at": "2026-07-12T08:00:00+08:00",
            }
        ]

    def list_nodes(self, _plan_id: int):
        return [
            {
                "id": 11,
                "status": "active",
                "day_index": 1,
                "scheduled_time": "08:30",
                "action_title": "morning",
                "text_content": "hello",
                "attachments": [],
            }
        ]


def test_only_group_and_webhook_actions_plan_external_effects() -> None:
    expected = {
        "enqueue": False,
        "publish_task": False,
        "send_message": False,
        "send_group_message": True,
        "group_notice": True,
        "webhook_notify": True,
        "record_only": False,
        "add_to_audience": False,
    }

    assert {action: group_ops_effect_action_type(action) for action in expected} == expected


def test_group_and_webhook_actions_map_to_distinct_effect_types(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_plan(**kwargs):
        calls.append(kwargs)
        return {"id": len(calls), "created_on_plan": True}

    monkeypatch.setattr(
        "aicrm_next.automation_engine.group_ops.external_effects.plan_group_ops_external_effect",
        fake_plan,
    )

    for action_type in ("send_group_message", "group_notice", "webhook_notify"):
        plan_group_ops_action_effect(
            plan_id=1,
            trigger_event_id="event_001",
            recipient={"group_id": "chat_001"},
            action={"action_type": action_type, "content": "hello"},
            operator_member_id="owner_001",
            source_route="/api/automation/group-ops/webhooks/{webhook_key}",
            idempotency_key=f"idem-{action_type}",
        )

    assert [item["effect_type"] for item in calls] == [
        WECOM_MESSAGE_GROUP_SEND,
        WECOM_MESSAGE_GROUP_SEND,
        GROUP_OPS_WEBHOOK_ACTION_LOOPBACK,
    ]


def test_send_message_enqueues_one_private_broadcast_job() -> None:
    inserted: list[dict[str, Any]] = []

    def insert_job(**kwargs):
        inserted.append(kwargs)
        return 901

    dispatcher = GroupOpsActionDispatcher(
        queue_gateway=NextOutboundMessageQueueGateway(insert_job=insert_job),
        identity_resolver=_resolved_identity,
    )
    result = dispatcher.dispatch(
        {
            "plan_id": 1,
            "trigger_event_id": "event-private-001",
            "operator_member_id": "owner_001",
            "recipient": {"external_user_id": "external_001", "unionid": "union_001"},
            "action": {"action_type": "send_message", "content": "private hello"},
        }
    )

    assert result["ok"] is True
    assert result["status"] == "queued"
    assert result["action_ref_id"] == "901"
    assert result["side_effect_executed"] is False
    assert len(inserted) == 1
    assert inserted[0]["payload"]["channel"] == "wecom_private"
    assert inserted[0]["payload"]["unionids"] == ["union_001"]


def test_external_effect_planner_failure_is_not_swallowed(monkeypatch) -> None:
    def fail_plan(self, **_kwargs):
        raise RuntimeError("postgres planning unavailable")

    monkeypatch.setattr(
        "aicrm_next.automation_engine.group_ops.external_effects.ExternalEffectService.plan_effect",
        fail_plan,
    )

    with pytest.raises(RuntimeError, match="postgres planning unavailable"):
        plan_group_ops_external_effect(
            effect_type=WECOM_MESSAGE_GROUP_SEND,
            plan_id=1,
            target_type="group_ops_node",
            target_id="11",
            chat_ids=["chat_001"],
            content_payload={"channel": "wecom_customer_group", "chat_ids": ["chat_001"]},
            idempotency_key="planning-failure",
        )


def test_scheduler_planner_failure_produces_non_success_summary(monkeypatch) -> None:
    class FailingGraphRepository:
        def plan(self, _request):
            raise RuntimeError("planner transaction failed")

    monkeypatch.setattr(
        "aicrm_next.automation_engine.group_ops.scheduler.build_group_ops_effect_graph_repository",
        lambda: FailingGraphRepository(),
    )
    monkeypatch.setattr(
        "aicrm_next.automation_engine.group_ops.scheduler.resolve_group_ops_content_package_materials",
        lambda _content_package: ([], []),
    )

    group_ops = run_group_ops_due_scheduler(
        repo=_DueRepo(),
        now=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc),
        operator="pytest",
    )
    overall = run_automation_ops_scheduler(
        now=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc),
        group_ops_runner=lambda **_kwargs: {"component": "group_ops_scheduler", "status": "failed", **group_ops},
    )

    assert group_ops["errors"] == [
        {
            "scope": "group_ops_node",
            "plan_id": 1,
            "node_id": 11,
            "error": "planner transaction failed",
        }
    ]
    assert overall["ok"] is False


def test_group_ops_planning_source_has_no_shadow_or_direct_send_modes() -> None:
    source = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "aicrm_next/automation_engine/group_ops/external_effects.py",
            "aicrm_next/automation_engine/group_ops/action_dispatcher.py",
            "aicrm_next/automation_engine/group_ops/scheduler.py",
        )
    )

    for forbidden in (
        "AICRM_GROUP_OPS_OUTBOUND_MODE",
        "AICRM_GROUP_OPS_EXTERNAL_EFFECT_SEND_MODE",
        "AICRM_GROUP_OPS_PRIVATE_MESSAGE_MODE",
        "NextPrivateMessageTaskGateway",
        "GroupOpsDuplicateChecker",
        "build_group_ops_duplicate_checker",
    ):
        assert forbidden not in source
