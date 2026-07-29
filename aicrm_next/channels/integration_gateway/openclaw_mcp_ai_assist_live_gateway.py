from __future__ import annotations

from typing import Any


Json = dict[str, Any]


class OpenClawMcpAiAssistLiveGateway:
    def call_mcp_tool_live(self, *, tool_name: str, arguments_redacted: Json) -> Json:
        return {
            "ok": False,
            "result_status": "blocked",
            "error_code": "live_gateway_disabled",
            "provider_call_executed": False,
            "real_mcp_call_executed": False,
            "real_openclaw_call_executed": False,
            "real_llm_call_executed": False,
            "deepseek_call_executed": False,
        }

    def push_openclaw_context_live(self, *, member_id_redacted: str, context_redacted: Json) -> Json:
        return {
            "ok": False,
            "result_status": "blocked",
            "error_code": "live_gateway_disabled",
            "provider_call_executed": False,
            "real_mcp_call_executed": False,
            "real_openclaw_call_executed": False,
            "real_llm_call_executed": False,
            "deepseek_call_executed": False,
        }

    def run_ai_assist_completion_live(self, *, prompt_redacted: str, context_redacted: Json) -> Json:
        return {
            "ok": False,
            "result_status": "blocked",
            "error_code": "live_gateway_disabled",
            "provider_call_executed": False,
            "real_mcp_call_executed": False,
            "real_openclaw_call_executed": False,
            "real_llm_call_executed": False,
            "deepseek_call_executed": False,
        }


def build_openclaw_mcp_ai_assist_live_gateway() -> OpenClawMcpAiAssistLiveGateway:
    return OpenClawMcpAiAssistLiveGateway()
