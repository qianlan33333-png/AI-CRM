from __future__ import annotations

import json

from aicrm_next.external_effect_composition import build_external_effect_continuation_registry
from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.external_effects import (
    WEBHOOK_ORDER_PAID_PUSH,
    WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
    WECOM_CONTACT_TAG_MARK,
    ExternalEffectDispatchResult,
    ExternalEffectService,
    reset_external_effect_fixture_state,
)
from aicrm_next.platform_foundation.external_effects.adapters import ExternalEffectAdapterRegistry
from aicrm_next.platform_foundation.external_effects.jobs import (
    SCHEDULER_ENABLED_KEY,
    TEST_EXECUTION_ONLY_KEY,
    run_scheduled_external_effects,
)
from aicrm_next.platform_foundation.external_effects.repo import build_external_effect_repository
from aicrm_next.platform_foundation.external_effects.test_receiver import (
    TEST_RECEIVER_PATH_PREFIX,
    canonical_payload_hash,
)
from scripts import run_external_effect_queue_worker as worker_script


class _SucceedingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def dispatch(self, job):
        self.calls += 1
        return ExternalEffectDispatchResult(
            status="succeeded",
            adapter_mode="execute",
            request_summary={"effect_type": job.effect_type},
            response_summary={"status_code": 200, "real_external_call_executed": True},
            real_external_call_executed=True,
            provider_result_received=True,
        )


def _registry(adapter: _SucceedingAdapter) -> ExternalEffectAdapterRegistry:
    registry = ExternalEffectAdapterRegistry()
    registry._adapters["outbound_webhook"] = adapter  # type: ignore[attr-defined]
    return registry


def _context(trace_id: str) -> CommandContext:
    return CommandContext(actor_id="pytest", actor_type="system", request_id=trace_id, trace_id=trace_id, source_route="/pytest/external-effect-scheduler")


def _plan(effect_type: str, *, key: str, test_only: bool = False) -> None:
    payload = (
        {"external_userid": key, "tag_ids": ["tag_a"]}
        if effect_type == WECOM_CONTACT_TAG_MARK
        else {"webhook_url": "https://hooks.example.test/effect", "body": {"id": key}}
    )
    if test_only:
        payload.update(
            {
                "webhook_url": f"https://crm.example.test{TEST_RECEIVER_PATH_PREFIX}",
                "execution_scope": "test_loopback",
                "is_test": True,
                "expected_payload_hash": canonical_payload_hash(payload["body"]),
            }
        )
    ExternalEffectService().plan_effect(
        effect_type=effect_type,
        adapter_name="wecom_tag" if effect_type == WECOM_CONTACT_TAG_MARK else "outbound_webhook",
        operation="post",
        target_type="external_user" if effect_type == WECOM_CONTACT_TAG_MARK else ("questionnaire_submission" if effect_type == WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH else "wechat_pay_order"),
        target_id=key,
        business_type="wecom_tag" if effect_type == WECOM_CONTACT_TAG_MARK else ("questionnaire" if effect_type == WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH else "commerce_order"),
        business_id=key,
        payload=payload,
        context=_context(f"trace-{key}"),
        idempotency_key=f"scheduler-{key}",
        status="queued",
        execution_mode="execute",
    )


def test_external_effect_scheduler_dry_run_previews_all_due_jobs() -> None:
    reset_external_effect_fixture_state()
    _plan(WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH, key="q-preview")
    _plan(WEBHOOK_ORDER_PAID_PUSH, key="order-preview")

    result = run_scheduled_external_effects(dry_run=True, limit=10)

    assert result["dry_run"] is True
    assert result["counts"]["candidate_count"] == 2
    assert result["counts"]["skipped_count"] == 2
    assert {item["dispatch_status"] for item in result["items"]} == {"skipped"}
    assert {item["effect_type"] for item in result["items"]} == {WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH, WEBHOOK_ORDER_PAID_PUSH}
    assert result["real_external_call_executed"] is False


def test_external_effect_scheduler_execute_requires_global_scheduler_switch(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    monkeypatch.setenv(SCHEDULER_ENABLED_KEY, "0")
    _plan(WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH, key="q-disabled")
    adapter = _SucceedingAdapter()

    result = run_scheduled_external_effects(
        dry_run=False,
        limit=10,
        repository=build_external_effect_repository(),
        adapter_registry=_registry(adapter),
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "scheduler_disabled"
    assert adapter.calls == 0


def test_external_effect_worker_script_returns_nonzero_for_blocked_or_unknown(monkeypatch, capsys) -> None:
    captured: dict = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {
            "ok": False,
            "exit_code": 1,
            "counts": {"blocked_count": 1, "unknown_after_dispatch_count": 0},
            "real_external_call_executed": False,
        }

    monkeypatch.setattr(
        worker_script,
        "run_scheduled_external_effects",
        fake_run,
    )

    exit_code = worker_script.main(["--execute", "--limit", "1", "--operator", "pytest"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["counts"]["blocked_count"] == 1
    assert captured["continuation_registry"].names == build_external_effect_continuation_registry().names


def test_external_effect_scheduler_execute_scans_all_due_jobs_one_by_one(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    monkeypatch.setenv(SCHEDULER_ENABLED_KEY, "1")
    _plan(WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH, key="q-execute")
    _plan(WEBHOOK_ORDER_PAID_PUSH, key="order-execute")
    adapter = _SucceedingAdapter()

    result = run_scheduled_external_effects(
        dry_run=False,
        limit=10,
        repository=build_external_effect_repository(),
        adapter_registry=_registry(adapter),
    )

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert result["counts"]["processed_count"] == 2
    assert result["counts"]["succeeded_count"] == 2
    assert adapter.calls == 2


def test_external_effect_scheduler_uses_test_only_scope_when_safety_gate_is_enabled(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    monkeypatch.setenv(SCHEDULER_ENABLED_KEY, "1")
    monkeypatch.setenv(TEST_EXECUTION_ONLY_KEY, "1")
    _plan(WEBHOOK_ORDER_PAID_PUSH, key="order-production-scope")
    _plan(WEBHOOK_ORDER_PAID_PUSH, key="order-test-scope", test_only=True)
    adapter = _SucceedingAdapter()

    result = run_scheduled_external_effects(
        dry_run=False,
        limit=10,
        repository=build_external_effect_repository(),
        adapter_registry=_registry(adapter),
    )

    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["test_only"] is True
    assert result["mode"] == "execute_test_only_due_jobs"
    assert result["scheduler"]["mode"] == "test_only_due_scan"
    assert result["scheduler"]["test_only"] is True
    assert result["counts"]["candidate_count"] == 1
    assert result["counts"]["processed_count"] == 1
    assert result["counts"]["succeeded_count"] == 1
    assert adapter.calls == 1


def test_external_effect_scheduler_test_only_empty_queue_is_success(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    monkeypatch.setenv(SCHEDULER_ENABLED_KEY, "1")
    monkeypatch.setenv(TEST_EXECUTION_ONLY_KEY, "1")
    _plan(WEBHOOK_ORDER_PAID_PUSH, key="order-production-only")
    adapter = _SucceedingAdapter()

    result = run_scheduled_external_effects(
        dry_run=False,
        limit=10,
        repository=build_external_effect_repository(),
        adapter_registry=_registry(adapter),
    )

    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["test_only"] is True
    assert result["counts"]["candidate_count"] == 0
    assert result["counts"]["processed_count"] == 0
    assert adapter.calls == 0


def test_external_effect_scheduler_respects_explicit_wecom_kill_switch(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    monkeypatch.delenv("AICRM_WECOM_EXECUTION_MODE", raising=False)
    monkeypatch.setenv(SCHEDULER_ENABLED_KEY, "1")
    _plan(WECOM_CONTACT_TAG_MARK, key="wx-scheduler-disabled")
    adapter = _SucceedingAdapter()
    registry = ExternalEffectAdapterRegistry()
    registry._adapters["wecom_tag"] = adapter  # type: ignore[attr-defined]
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "disabled")

    result = run_scheduled_external_effects(
        dry_run=False,
        limit=10,
        repository=build_external_effect_repository(),
        adapter_registry=registry,
    )

    assert result["status"] == "failed"
    assert result["exit_code"] == 1
    assert result["counts"]["processed_count"] == 1
    assert result["counts"]["blocked_count"] == 1
    assert result["real_external_call_executed"] is False
    assert adapter.calls == 0
