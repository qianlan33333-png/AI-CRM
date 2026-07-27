from __future__ import annotations

from aicrm_next.extensions.ai.automation_agents.internal_webhook_adapter import (
    AutomationAgentRoutingWebhookAdapter,
    automation_agent_code_from_webhook_url,
)
from aicrm_next.platform.platform_foundation.external_effects.execution_policy import normalize_dispatch_result
from aicrm_next.platform.platform_foundation.external_effects.models import (
    WEBHOOK_GENERIC_PUSH,
    ExternalEffectDispatchResult,
    ExternalEffectJob,
)


class _Fallback:
    def __init__(self) -> None:
        self.calls: list[ExternalEffectJob] = []

    def dispatch(self, job: ExternalEffectJob) -> ExternalEffectDispatchResult:
        self.calls.append(job)
        return ExternalEffectDispatchResult(
            status="failed_terminal",
            error_code="ssrf_blocked",
            real_external_call_executed=False,
        )


class _Service:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def handle(self, agent_code, payload, *, raw_body, headers):
        self.calls.append(
            {
                "agent_code": agent_code,
                "payload": payload,
                "raw_body": raw_body,
                "headers": headers,
            }
        )
        return {
            "ok": True,
            "batch_id": "agent_batch_internal_001",
            "mode": "queued",
            "received_count": 1,
            "deduped_count": 1,
            "accepted_count": 1,
        }, 200


def _job(url: str) -> ExternalEffectJob:
    return ExternalEffectJob(
        id=1,
        effect_type=WEBHOOK_GENERIC_PUSH,
        adapter_name="webhook",
        operation="post",
        execution_mode="execute",
        payload_json={
            "webhook_url": url,
            "headers": {"X-AICRM-Idempotency-Key": "internal-agent-001"},
            "body": ["wm_masked"],
        },
    )


def test_exact_automation_agent_webhook_is_dispatched_in_process() -> None:
    fallback = _Fallback()
    service = _Service()
    adapter = AutomationAgentRoutingWebhookAdapter(fallback, service_factory=lambda: service)

    result = adapter.dispatch(_job("http://127.0.0.1:5001/api/ai/agents/activation_agent/audience-webhook"))

    assert result.status == "succeeded"
    assert result.real_external_call_executed is False
    assert result.response_summary["internal_service_call_executed"] is True
    assert result.response_summary["internal_side_effect_executed"] is True
    assert result.response_summary["automation_agent_batch_id"] == "agent_batch_internal_001"
    assert service.calls[0]["agent_code"] == "activation_agent"
    assert service.calls[0]["payload"] == ["wm_masked"]
    assert fallback.calls == []

    normalized = normalize_dispatch_result(
        _job("http://127.0.0.1:5001/api/ai/agents/activation_agent/audience-webhook"),
        result,
    )
    assert normalized.status == "succeeded"
    assert normalized.real_external_call_executed is False
    assert normalized.provider_result_received is True
    assert normalized.response_summary["internal_side_effect_executed"] is True


def test_non_agent_or_ambiguous_loopback_url_keeps_public_webhook_ssrf_policy() -> None:
    fallback = _Fallback()
    service = _Service()
    adapter = AutomationAgentRoutingWebhookAdapter(fallback, service_factory=lambda: service)

    result = adapter.dispatch(_job("http://127.0.0.1:5001/api/not-an-agent/webhook"))
    encoded_slash = automation_agent_code_from_webhook_url(
        "http://127.0.0.1:5001/api/ai/agents/bad%2Fcode/audience-webhook"
    )
    third_party = automation_agent_code_from_webhook_url(
        "https://untrusted.example/api/ai/agents/activation_agent/audience-webhook"
    )

    assert result.error_code == "ssrf_blocked"
    assert len(fallback.calls) == 1
    assert service.calls == []
    assert encoded_slash == ""
    assert third_party == ""


def test_internal_side_effect_without_completion_evidence_remains_blocked() -> None:
    normalized = normalize_dispatch_result(
        _job("http://127.0.0.1:5001/api/ai/agents/activation_agent/audience-webhook"),
        ExternalEffectDispatchResult(
            status="succeeded",
            adapter_mode="execute",
            response_summary={"internal_side_effect_executed": True},
            real_external_call_executed=False,
            provider_result_received=False,
        ),
    )

    assert normalized.status == "blocked"
    assert normalized.error_code == "success_without_side_effect"
    assert normalized.real_external_call_executed is False
