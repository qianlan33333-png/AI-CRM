from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator import api as cloud_orchestrator_api
from aicrm_next.extensions.growth.cloud_orchestrator.review_plans import create_ai_assist_review_plan


class FakeCloudPlanRepository:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create_or_reuse_agent_send_plan(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "status": "created",
            "plan_id": "agent_plan:test-review",
            "recipient_id": 91,
            "message_id": 92,
            "downstream_status": "send_plan_pending",
            "push_center_job_id": "cloud_plan:agent_plan:test-review",
        }


def test_create_ai_assist_review_plan_stops_before_broadcast_queue() -> None:
    repo = FakeCloudPlanRepository()

    result = create_ai_assist_review_plan(
        {
            "external_event_id": "review-event-1",
            "external_userid": "wm_review_1",
            "owner_userid": "HuangYouCan",
            "content_text": "请人工确认后再发送",
            "operator": "pytest",
        },
        repository=repo,
    )

    assert result["ok"] is True
    assert result["plan_id"] == "agent_plan:test-review"
    assert result["run_status"] == "draft"
    assert result["broadcast_job_created"] is False
    assert result["real_external_call_executed"] is False
    assert result["review_status"] == "pending_review"
    assert result["next_step"] == "admin_click_approve_and_start"
    assert result["plan_url"].endswith("/agent_plan:test-review")
    assert repo.calls == [
        {
            "external_event_id": "review-event-1",
            "package_key": "admin_ai_assist_review_plan",
            "external_userid": "wm_review_1",
            "owner_userid": "HuangYouCan",
            "content_package": {
                "content_text": "请人工确认后再发送",
                "image_library_ids": [],
                "miniprogram_library_ids": [],
                    "attachment_library_ids": [],
                    "group_invite_library_ids": [],
            },
            "operator": "pytest",
            "requires_review": True,
        }
    ]


def test_review_plan_api_rejects_missing_target(monkeypatch) -> None:
    async def fake_write_context(request):
        return await request.json(), ""

    monkeypatch.setattr(cloud_orchestrator_api, "_write_context", fake_write_context)
    app = FastAPI()
    app.include_router(cloud_orchestrator_api.router)
    client = TestClient(app)

    response = client.post(
        "/api/admin/ai-assist/review-plans",
        content=json.dumps({"owner_userid": "HuangYouCan", "content_text": "hello"}),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "external_userid_required"
