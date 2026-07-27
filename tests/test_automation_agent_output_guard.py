from __future__ import annotations

from types import SimpleNamespace

from aicrm_next.extensions.ai.automation_agents import worker as worker_module
from aicrm_next.extensions.ai.automation_agents.worker import AutomationAgentWorker


class FakeAgentRepository:
    def __init__(self, *, need_human_review: bool = False) -> None:
        self.need_human_review = need_human_review
        self.updates: list[dict] = []

    def update_item(self, item_id: int, payload: dict) -> None:
        self.updates.append({"item_id": item_id, **payload})

    def get_agent_by_code(self, agent_code: str) -> dict:
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
    monkeypatch.setattr(worker_module, "generate_agent_reply", lambda **kwargs: SimpleNamespace(ok=True, final_text="输出话术：{{最近20条聊天信息}}"))

    result = AutomationAgentWorker(repository=repo).run_item({"id": 101, "agent_code": "activation_agent", "unionid": "union_001"})

    assert result["ok"] is False
    assert result["error"] == "llm_output_rejected"
    assert repo.updates[-1]["status"] == "failed"
    assert repo.updates[-1]["error_code"] == "llm_output_rejected"
    assert "callback_payload_json" not in repo.updates[-1]


def test_worker_blocks_human_review_agent_before_auto_send(monkeypatch) -> None:
    repo = FakeAgentRepository(need_human_review=True)
    monkeypatch.setattr(worker_module, "build_agent_context", _context)
    monkeypatch.setattr(worker_module, "generate_agent_reply", lambda **kwargs: SimpleNamespace(ok=True, final_text="你好，这是生成话术"))

    result = AutomationAgentWorker(repository=repo).run_item({"id": 102, "agent_code": "activation_agent", "unionid": "union_001"})

    assert result["ok"] is False
    assert result["error"] == "human_review_required"
    assert repo.updates[-1]["status"] == "failed"
    assert repo.updates[-1]["error_code"] == "human_review_required"
    assert "callback_payload_json" not in repo.updates[-1]
