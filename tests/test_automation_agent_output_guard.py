from __future__ import annotations

from aicrm_next.extensions.ai.automation_agents import worker as worker_module
from aicrm_next.extensions.ai.automation_agents.worker import AutomationAgentWorker


class FakeAgentRepository:
    def __init__(self, *, need_human_review: bool = False) -> None:
        self.need_human_review = need_human_review
        self.updates: list[dict] = []

    def _agent_snapshot(self, agent_code: str = "activation_agent") -> dict:
        return {
            "agent_code": agent_code,
            "status": "active",
            "automation_type": "agent",
            "published_role_prompt": "你是助手，参考{{用户标签}}",
            "published_task_prompt": "输出话术：{{最近20条聊天信息}}",
            "fixed_content_package_json": {"image_library_ids": [], "miniprogram_library_ids": [], "attachment_library_ids": []},
            "bound_package_key": "agent_callback_pkg",
            "send_webhook_url": "/api/ai/audience/packages/agent_callback_pkg/webhook",
            "need_human_review": self.need_human_review,
        }

    def claim_item_for_prepare(self, item_id: int) -> dict:
        return {
            "id": item_id,
            "batch_id": "agent_batch_guard",
            "agent_code": "activation_agent",
            "agent_published_version": 1,
            "agent_config_snapshot_json": self._agent_snapshot(),
            "external_event_id": f"guard-{item_id}",
            "unionid": "union_001",
            "status": "preparing",
            "pipeline_claimed": True,
        }

    def get_pipeline_item(self, item_id: int) -> dict:
        return {
            "id": item_id,
            "batch_id": "agent_batch_guard",
            "agent_code": "activation_agent",
            "agent_config_snapshot_json": self._agent_snapshot(),
            "generation_effect_job_id": 501,
            "status": "generation_succeeded",
            "unionid": "union_001",
            "owner_userid": "owner_001",
            "context_snapshot_json": {
                "owner_userid": "owner_001",
                "external_userid": "wm_001",
                "customer": {"external_userid": "wm_001"},
                "blocks": _context()["blocks"],
            },
        }

    def fail_pipeline_item(
        self,
        item_id: int,
        *,
        error_code: str,
        error_message: str,
        retryable: bool = False,
        context: dict | None = None,
        owner_userid: str = "",
        prompt_preview: str = "",
    ) -> dict:
        payload = {
            "item_id": item_id,
            "status": "failed_retryable" if retryable else "failed",
            "error_code": error_code,
            "error_message": error_message,
            "context": context or {},
            "owner_userid": owner_userid,
            "prompt_preview": prompt_preview,
        }
        self.updates.append(payload)
        return {"ok": False, "error": error_code, **payload}

    def resolve_external_userid_for_unionid(self, unionid: str) -> str:
        return "wm_001"


def _context(*args, **kwargs) -> dict:
    return {
        "owner_userid": "owner_001",
        "blocks": {"用户标签": "高意向", "最近20条聊天信息": "2026-06-25 wm_001: 我想了解课程"},
    }


def test_worker_rejects_prompt_like_llm_output_without_callback(monkeypatch) -> None:
    repo = FakeAgentRepository()
    monkeypatch.setattr(worker_module, "build_agent_context", _context)

    result = AutomationAgentWorker(repository=repo).complete_generation(
        item_id=101,
        generation_effect_job_id=501,
        final_text="输出话术：{{最近20条聊天信息}}",
    )

    assert result["ok"] is True
    assert result["business_outcome"] == "failed"
    assert result["error"] == "llm_output_rejected"
    assert repo.updates[-1]["status"] == "failed"
    assert repo.updates[-1]["error_code"] == "llm_output_rejected"
    assert "callback_payload_json" not in repo.updates[-1]


def test_worker_blocks_human_review_agent_before_auto_send(monkeypatch) -> None:
    repo = FakeAgentRepository(need_human_review=True)
    monkeypatch.setattr(worker_module, "build_agent_context", _context)

    result = AutomationAgentWorker(repository=repo).prepare_item(102)

    assert result["ok"] is False
    assert result["error"] == "human_review_required"
    assert repo.updates[-1]["status"] == "failed"
    assert repo.updates[-1]["error_code"] == "human_review_required"
    assert "callback_payload_json" not in repo.updates[-1]
