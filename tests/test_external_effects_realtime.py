from __future__ import annotations

from pathlib import Path

from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.external_effects import (
    ExternalEffectService,
    WECOM_WELCOME_MESSAGE_SEND,
)
from aicrm_next.platform_foundation.external_effects.realtime import (
    CHANNEL_ENTRY_REALTIME_EFFECT_TYPES,
    realtime_wakeup_state,
    wake_external_effect_job,
)
from aicrm_next.platform_foundation.external_effects.repo import InMemoryExternalEffectRepository


ROOT = Path(__file__).resolve().parents[1]


def _plan_welcome(repo: InMemoryExternalEffectRepository, key: str = "welcome-signal") -> dict:
    return ExternalEffectService(repo).plan_effect(
        effect_type=WECOM_WELCOME_MESSAGE_SEND,
        adapter_name="wecom_welcome_message",
        operation="send",
        target_type="external_user",
        target_id="wm-signal",
        business_type="channel_entry",
        business_id="channel-1",
        source_module="channel_entry.application",
        source_event_id="evt-1",
        idempotency_key=key,
        payload={
            "welcome_code": "welcome-code",
            "external_userid": "wm-signal",
            "follow_user_userid": "HuangYouCan",
            "text": {"content": "欢迎加入"},
        },
        payload_summary={"welcome_code_present": True},
        context=CommandContext(
            actor_id="pytest",
            actor_type="system",
            request_id="req-1",
            trace_id="trace-1",
        ),
        status="queued",
        execution_mode="execute",
    )


def test_realtime_state_exposes_retired_signal_only_contract() -> None:
    state = realtime_wakeup_state()

    assert state["status"] == "durable_signal_only"
    assert state["enabled_source"] == "postgres_queue_trigger"
    assert state["signal_transport"] == "transactional_queue_trigger"
    assert state["dispatch_boundary"] == "postgres_execution_runtime_claim_one"
    assert state["provider_dispatch_allowed"] is False
    assert state["uses_process_local_executor"] is False
    assert state["max_concurrency"] == 0
    assert set(state["allowed_types"]) == set(CHANNEL_ENTRY_REALTIME_EFFECT_TYPES)


def test_realtime_signal_rejects_invalid_job_or_non_channel_effect() -> None:
    assert wake_external_effect_job(0, reason="pytest", effect_type=WECOM_WELCOME_MESSAGE_SEND) is False
    assert wake_external_effect_job(1, reason="pytest", effect_type="webhook.outbound") is False


def test_legacy_realtime_flags_cannot_disable_durable_signal(monkeypatch) -> None:
    repo = InMemoryExternalEffectRepository()
    job = _plan_welcome(repo)
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_REALTIME_ENABLED", "0")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_REALTIME_ALLOWED_TYPES", "")
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "disabled")

    signalled = wake_external_effect_job(
        job["id"],
        reason="pytest",
        effect_type=WECOM_WELCOME_MESSAGE_SEND,
        repository=repo,
        run_inline=True,
    )

    assert signalled is True
    assert repo.get_job(job["id"]).status == "queued"  # type: ignore[union-attr]
    assert repo.list_attempts(job["id"]) == []


def test_realtime_signal_never_dispatches_provider_or_creates_attempt() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _plan_welcome(repo, key="welcome-no-inline-provider")

    class ExplodingRegistry:
        def get(self, _name: str):
            raise AssertionError("callback signal must not resolve a provider adapter")

    signalled = wake_external_effect_job(
        job["id"],
        reason="pytest",
        effect_type=WECOM_WELCOME_MESSAGE_SEND,
        repository=repo,
        adapter_registry=ExplodingRegistry(),
        run_inline=True,
    )

    updated = repo.get_job(job["id"])
    assert signalled is True
    assert updated is not None
    assert updated.status == "queued"
    assert updated.provider_call_started_at == ""
    assert repo.list_attempts(job["id"]) == []


def test_realtime_module_has_no_inline_claim_or_provider_worker() -> None:
    source = (
        ROOT
        / "aicrm_next"
        / "platform_foundation"
        / "external_effects"
        / "realtime.py"
    ).read_text(encoding="utf-8")

    assert "ExternalEffectWorker" not in source
    assert "dispatch_one(" not in source
    assert "dispatch_external_effect_job_now" not in source
    assert "dispatch_external_effect_job_realtime" not in source
    assert "adapter_registry.get" not in source
