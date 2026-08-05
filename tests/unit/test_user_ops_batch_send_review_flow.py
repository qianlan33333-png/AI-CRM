from __future__ import annotations

from typing import Any

import pytest

from aicrm_next.automation.ops_enrollment.application import ExecuteUserOpsBatchSendCommand
from aicrm_next.automation.ops_enrollment.dto import BatchSendRequest
from aicrm_next.automation.ops_enrollment.repo import InMemoryUserOpsRepository
from aicrm_next.extensions.growth.cloud_orchestrator.application import ApproveCloudPlanCommand
from aicrm_next.extensions.growth.cloud_orchestrator.repository_memory import InMemoryCloudPlanRepository
from aicrm_next.extensions.growth.cloud_orchestrator.review_plans import create_ai_assist_batch_review_plan


class AcceptBatchPreviewGateway:
    mode = "test"

    def build_batch_send_preview(self, **_: Any) -> dict[str, Any]:
        return {"ok": True, "error_code": "", "error_message": ""}


class CapturingReviewPlanGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_pending_review_plan(self, **payload: Any) -> dict[str, Any]:
        self.calls.append(payload)
        return {
            "ok": True,
            "status": "created",
            "plan_id": "agent_plan:test_batch",
            "plan_url": "/admin/cloud-orchestrator/plans/agent_plan:test_batch",
            "review_status": "pending_review",
            "run_status": "draft",
            "recipient_count": len(payload["targets"]),
            "message_count": len(payload["targets"]),
            "broadcast_job_count": 0,
            "real_external_call_executed": False,
        }


def test_user_ops_execute_creates_review_plan_without_external_effect_jobs() -> None:
    repo = InMemoryUserOpsRepository()
    review_gateway = CapturingReviewPlanGateway()
    result = ExecuteUserOpsBatchSendCommand(
        repo,
        batch_gateway=AcceptBatchPreviewGateway(),
        review_plan_gateway=review_gateway,
    )(
        BatchSendRequest(
            selection_mode="manual",
            selected_ids=[1],
            content="审批后发送",
            images=[{"library_id": 11}],
            attachments=[
                {"msgtype": "miniprogram", "miniprogram": {"library_id": 12}},
                {"msgtype": "file", "file": {"library_id": 13}},
                {"msgtype": "link", "link": {"library_id": 14}},
            ],
            confirm=True,
            operator="admin",
        ),
        idempotency_key="user_ops_batch_send:test_review_flow",
    )

    assert result["ok"] is True
    assert result["execution_backend"] == "ai_assist_review_plan"
    assert result["review_status"] == "pending_review"
    assert result["run_status"] == "draft"
    assert result["next_step"] == "ai_assist_review"
    assert result["external_effect_job_ids"] == []
    assert result["broadcast_job_count"] == 0
    assert len(review_gateway.calls) == 1
    assert review_gateway.calls[0]["targets"][0]["unionid"] == "union_ops_001"
    assert review_gateway.calls[0]["content_package"] == {
        "content_text": "审批后发送",
        "image_library_ids": [11],
        "miniprogram_library_ids": [12],
        "attachment_library_ids": [13],
        "group_invite_library_ids": [14],
    }
    record = repo.get_send_record(result["record_id"])
    assert record is not None
    assert record["execution_backend"] == "ai_assist_review_plan"
    assert record["status"] == "planned"
    assert record["status_label"] == "AI 助手待审批"
    assert record["external_effect_job_ids"] == []


def test_batch_review_plan_is_idempotent_and_stays_unsent_until_approval() -> None:
    repo = InMemoryCloudPlanRepository()
    payload = {
        "external_event_id": "user_ops_batch_send:test_batch_plan",
        "package_key": "ai_audience_package:14",
        "operator": "admin",
        "display_name": "AI 人群包群发审批 · 2 人",
        "content_package": {"content_text": "测试审批话术"},
        "recipients": [
            {"unionid": "union_batch_a", "owner_userid": "HuangYouCan", "customer_name": "测试 A"},
            {"unionid": "union_batch_b", "owner_userid": "HuangYouCan", "customer_name": "测试 B"},
        ],
    }

    created = create_ai_assist_batch_review_plan(payload, repository=repo)
    reused = create_ai_assist_batch_review_plan(payload, repository=repo)

    assert created["status"] == "created"
    assert reused["status"] == "reused"
    assert created["plan_id"] == reused["plan_id"]
    assert created["review_status"] == "pending_review"
    assert created["run_status"] == "draft"
    assert created["recipient_count"] == 2
    assert created["message_count"] == 2
    assert created["broadcast_job_count"] == 0
    assert repo.broadcast_jobs == []

    approved = ApproveCloudPlanCommand(repo)(created["plan_id"], operator="admin")
    assert approved["ok"] is True
    assert approved["broadcast_enqueue"]["broadcast_job_count"] == 2
    assert len(repo.broadcast_jobs) == 2
    with pytest.raises(ValueError, match="review_plan_created_broadcast_jobs_early"):
        create_ai_assist_batch_review_plan(payload, repository=repo)
