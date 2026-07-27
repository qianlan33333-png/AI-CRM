from __future__ import annotations

import importlib.util

from aicrm_next.extensions.ai.ai_audience_ops import agent_gateway


def test_retired_automation_gateway_modules_are_removed() -> None:
    assert importlib.util.find_spec("aicrm_next.channels.integration_gateway.automation_adapters") is None
    assert importlib.util.find_spec("aicrm_next.channels.integration_gateway.automation_contracts") is None


def test_ai_audience_agent_gateway_remains_available(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_MODE", "fake")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_FAKE_ALLOWED", "1")
    result = agent_gateway.generate_agent_reply(
        agent_code="ai_audience_agent",
        role_prompt="你是私域运营助手",
        task_prompt="请生成一句话术",
        variables={"member": {"external_userid": "wm_test"}},
        mock_output="你好，这是 AI Audience 话术。",
    )

    assert result.ok is True
    assert result.mode == "fake"
    assert result.final_text == "你好，这是 AI Audience 话术。"
    assert result.external_call_executed is False
