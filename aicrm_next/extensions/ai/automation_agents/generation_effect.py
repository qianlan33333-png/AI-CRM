from __future__ import annotations

from typing import Any, Callable

from aicrm_next.extensions.ai.ai_audience_ops.agent_gateway import AgentGatewayResult, generate_agent_reply
from aicrm_next.platform.platform_foundation.external_effects.models import (
    AI_AGENT_GENERATE,
    ExternalEffectDispatchResult,
    ExternalEffectJob,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


class AutomationAgentGenerationAdapter:
    """Run one model generation outside every database transaction."""

    def __init__(
        self,
        generator: Callable[..., AgentGatewayResult] | None = None,
    ) -> None:
        self._generator = generator or generate_agent_reply

    def dispatch(self, job: ExternalEffectJob) -> ExternalEffectDispatchResult:
        payload = dict(job.payload_json or {})
        request_summary = {
            "effect_type": job.effect_type,
            "item_id": int(payload.get("item_id") or 0),
            "batch_id": _text(payload.get("batch_id")),
            "agent_code": _text(payload.get("agent_code")),
            "agent_published_version": int(payload.get("agent_published_version") or 0),
            "role_prompt_chars": len(_text(payload.get("role_prompt"))),
            "task_prompt_chars": len(_text(payload.get("task_prompt"))),
        }
        if job.effect_type != AI_AGENT_GENERATE:
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary={"real_external_call_executed": False},
                error_code="unsupported_effect_type",
                error_message="AI generation adapter received an unsupported effect type",
            )
        if job.execution_mode != "execute":
            return ExternalEffectDispatchResult(
                status="blocked",
                adapter_mode=job.execution_mode or "disabled",
                request_summary=request_summary,
                response_summary={"blocked": True, "real_external_call_executed": False},
                error_code="ai_generation_execution_disabled",
                error_message="AI generation execution mode is disabled",
            )
        required = {
            "item_id": int(payload.get("item_id") or 0),
            "agent_code": _text(payload.get("agent_code")),
            "role_prompt": _text(payload.get("role_prompt")),
            "task_prompt": _text(payload.get("task_prompt")),
        }
        if not all(required.values()):
            return ExternalEffectDispatchResult(
                status="failed_terminal",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary={"real_external_call_executed": False},
                error_code="ai_generation_payload_invalid",
                error_message="AI generation effect payload is incomplete",
            )
        variables = payload.get("variables") if isinstance(payload.get("variables"), dict) else {}
        try:
            result = self._generator(
                agent_code=required["agent_code"],
                role_prompt=required["role_prompt"],
                task_prompt=required["task_prompt"],
                variables=dict(variables),
            )
        except Exception as exc:
            return ExternalEffectDispatchResult(
                status="failed_retryable",
                adapter_mode="execute",
                request_summary=request_summary,
                response_summary={"real_external_call_executed": False},
                error_code="ai_generation_adapter_exception",
                error_message=exc.__class__.__name__,
            )

        response_summary = {
            "provider": _text(result.provider),
            "model": _text(result.model),
            "mode": _text(result.mode),
            "latency_ms": int(result.latency_ms or 0),
            "content_chars": len(_text(result.final_text)),
            "real_external_call_executed": bool(result.external_call_executed),
        }
        gateway_summary = dict(result.response_summary or {})
        for key in ("http_status", "choice_count", "usage"):
            if key in gateway_summary:
                response_summary[key] = gateway_summary[key]
        if result.ok and _text(result.final_text):
            return ExternalEffectDispatchResult(
                status="succeeded",
                adapter_mode=_text(result.mode) or "execute",
                request_summary=request_summary,
                response_summary=response_summary,
                provider_result={"final_text": _text(result.final_text)},
                real_external_call_executed=bool(result.external_call_executed),
                provider_result_received=True,
            )
        retryable_codes = {
            "agent_gateway_call_failed",
            "agent_gateway_http_error",
            "agent_generation_empty",
        }
        http_status = int(gateway_summary.get("http_status") or 0)
        return ExternalEffectDispatchResult(
            status="failed_retryable" if result.error_code in retryable_codes else "failed_terminal",
            adapter_mode=_text(result.mode) or "execute",
            request_summary=request_summary,
            response_summary=response_summary,
            error_code=_text(result.error_code) or "agent_generation_failed",
            error_message=_text(result.error_message)[:500],
            retry_after_seconds=30 if http_status == 429 else None,
            real_external_call_executed=bool(result.external_call_executed),
            provider_result_received=False,
        )


__all__ = ["AutomationAgentGenerationAdapter"]
