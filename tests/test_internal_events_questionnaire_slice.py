from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("composed_internal_event_registry")

from fastapi.testclient import TestClient

from aicrm_next.internal_event_composition import register_questionnaire_event_consumers
from aicrm_next.main import create_app
from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.external_effects import (
    WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
    ExternalEffectService,
    reset_external_effect_fixture_state,
)
from aicrm_next.platform_foundation.internal_events import (
    InternalEventService,
    QUESTIONNAIRE_SUBMITTED_EVENT_TYPE,
    reset_internal_event_fixture_state,
)
from aicrm_next.platform_foundation.internal_events.consumer_registry import InternalEventConsumerRegistry
from aicrm_next.platform_foundation.internal_events.repository import InMemoryInternalEventRepository
from aicrm_next.platform_foundation.internal_events.view_model import build_event_detail_payload
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker
from aicrm_next.questionnaire.h5_write import reset_questionnaire_h5_write_fixture_state
from aicrm_next.questionnaire.repo import reset_questionnaire_fixture_state
from tests.wechat_identity_test_support import authorize_wechat_client


QUESTIONNAIRE_CONSUMERS = frozenset({
    "ai_audience_source_poke_consumer",
    "automation_questionnaire_consumer",
    "customer_read_model_dirty_consumer",
    "customer_timeline_projection_consumer",
    "customer_summary_consumer",
    "questionnaire_projection_consumer",
    "questionnaire_tag_consumer",
    "questionnaire_webhook_consumer",
})


def _reset() -> None:
    reset_questionnaire_fixture_state()
    reset_questionnaire_h5_write_fixture_state()
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()


def _client(monkeypatch, *, questionnaire_enabled: bool = True, allowed_event_types: str = "questionnaire.submitted") -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_QUESTIONNAIRE_ENABLED", "1" if questionnaire_enabled else "0")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", allowed_event_types)
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "0")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", "")
    _reset()
    return TestClient(create_app())


def _submit(client: TestClient, *, key: str = "q-submit") -> dict:
    authorize_wechat_client(
        client,
        {
            "external_userid": f"wm_questionnaire_{key}",
            "openid": f"openid_{key}",
            "unionid": f"unionid_{key}",
            "respondent_key": f"respondent_{key}",
        },
    )
    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={
            "answers": {
                "q_activation": "activated",
                "q_interest": ["ai_tools"],
                "q_note": "原始敏感答案不应出现在 event list",
            },
            "identity": {
                "external_userid": f"wm_questionnaire_{key}",
                "mobile": "13800138000",
                "openid": f"openid_{key}",
                "unionid": f"unionid_{key}",
                "respondent_key": f"respondent_{key}",
            },
            "source": {"scene": "questionnaire-internal-event-test"},
        },
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 200
    return response.json()


def test_questionnaire_internal_event_is_durable_even_when_legacy_feature_flag_is_off(monkeypatch) -> None:
    client = _client(monkeypatch, questionnaire_enabled=False)

    body = _submit(client, key="flag-off")
    events, total = InternalEventService().list_events({"event_type": QUESTIONNAIRE_SUBMITTED_EVENT_TYPE})

    assert body["success"] is True
    assert body["internal_event_status"] == "emitted"
    assert body["internal_event_id"] == events[0].event_id
    assert total == 1


def test_questionnaire_submit_emits_single_event_and_expected_consumer_runs(monkeypatch) -> None:
    client = _client(monkeypatch, questionnaire_enabled=True)

    body = _submit(client, key="flag-on")
    duplicate = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={
            "answers": {"q_activation": "activated"},
            "identity": {"external_userid": "wm_questionnaire_flag-on", "mobile": "13800138000"},
            "source": {"scene": "duplicate"},
        },
        headers={"Idempotency-Key": "flag-on-duplicate"},
    )
    events, total = InternalEventService().list_events({"event_type": QUESTIONNAIRE_SUBMITTED_EVENT_TYPE})
    runs, run_total = InternalEventService().list_consumer_runs({"event_id": events[0].event_id})

    assert body["internal_event_status"] == "emitted"
    assert body["internal_event_id"] == events[0].event_id
    assert duplicate.status_code == 409
    assert total == 1
    assert events[0].event_type == "questionnaire.submitted"
    assert events[0].aggregate_type == "questionnaire_submission"
    assert events[0].aggregate_id == body["submission_id"]
    assert events[0].idempotency_key == f"questionnaire.submitted:{body['submission_id']}"
    assert events[0].payload_summary_json["answer_count"] == 3
    assert "13800138000" not in str(events[0].payload_summary_json)
    assert "openid_flag-on" not in str(events[0].payload_summary_json)
    assert run_total == len(QUESTIONNAIRE_CONSUMERS)
    assert {run.consumer_name for run in runs} == QUESTIONNAIRE_CONSUMERS


def test_questionnaire_projection_consumer_succeeds() -> None:
    registry = InternalEventConsumerRegistry()
    register_questionnaire_event_consumers(registry)
    repo = InMemoryInternalEventRepository()
    service = InternalEventService(repo, registry)
    emitted = service.emit_event(
        event_type=QUESTIONNAIRE_SUBMITTED_EVENT_TYPE,
        aggregate_type="questionnaire_submission",
        aggregate_id="sub_projection",
        payload={"submission": {"submission_id": "sub_projection"}},
        payload_summary={"submission_id": "sub_projection"},
        context=CommandContext(trace_id="sub_projection"),
        idempotency_key="questionnaire.submitted:sub_projection",
    )

    result = InternalEventWorker(repo, registry).run_due(
        batch_size=1,
        dry_run=False,
        event_types=[QUESTIONNAIRE_SUBMITTED_EVENT_TYPE],
        consumer_names=["questionnaire_projection_consumer"],
    )
    runs, _ = service.list_consumer_runs({"event_id": emitted["event"]["event_id"], "consumer_name": "questionnaire_projection_consumer"})

    assert result["counts"]["succeeded_count"] == 1, result.get("items")
    assert runs[0].status == "succeeded"
    assert runs[0].result_summary_json["questionnaire_projection"] == "submitted_confirmed"


def test_webhook_consumer_creates_shadow_external_effect_job_without_attempt(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "0")
    reset_external_effect_fixture_state()
    registry = InternalEventConsumerRegistry()
    register_questionnaire_event_consumers(registry)
    repo = InMemoryInternalEventRepository()
    service = InternalEventService(repo, registry)
    emitted = _emit_questionnaire_event_to_service(service, key="sub_webhook_create")

    result = InternalEventWorker(repo, registry).run_due(
        batch_size=1,
        dry_run=False,
        event_types=[QUESTIONNAIRE_SUBMITTED_EVENT_TYPE],
        consumer_names=["questionnaire_webhook_consumer"],
    )
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH, "target_id": "sub_webhook_create"})

    assert result["counts"]["succeeded_count"] == 1, result
    assert result["real_external_call_executed"] is False
    assert total == 1
    assert jobs[0].execution_mode == "execute"
    assert jobs[0].status == "queued"
    assert jobs[0].attempt_count == 0
    assert ExternalEffectService().list_attempts(jobs[0].id) == []
    assert result["items"][0]["attempt"]["response_summary_json"]["external_effect_job_created"] is True
    assert emitted["event"]["event_id"]


def test_questionnaire_event_detail_reconciles_submit_path_external_effect(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "0")
    reset_external_effect_fixture_state()
    registry = InternalEventConsumerRegistry()
    register_questionnaire_event_consumers(registry)
    repo = InMemoryInternalEventRepository()
    service = InternalEventService(repo, registry)
    emitted = _emit_questionnaire_event_to_service(service, key="reconcile-submit-path")
    ExternalEffectService().plan_effect(
        effect_type=WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
        adapter_name="outbound_webhook",
        operation="post",
        target_type="questionnaire_submission",
        target_id="reconcile-submit-path",
        business_type="questionnaire",
        business_id="1",
        payload={"webhook_url": "https://hooks.example.invalid/questionnaire"},
        payload_summary={"submission_id": "reconcile-submit-path"},
        context=CommandContext(trace_id="reconcile-submit-path"),
        source_module="questionnaire.external_push",
        execution_mode="execute",
        status="queued",
        idempotency_key="submit-path:reconcile-submit-path",
    )

    detail = build_event_detail_payload(emitted["event"]["event_id"], service=service)
    assert detail is not None
    reconciliation = detail["reconciliation"]
    effect = next(item for item in reconciliation["external_effects"] if item["effect_type"] == WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH)

    assert detail["derived_status"] == "effect_planned"
    assert detail["reconciliation_summary"]["unresolved_consumer_count"] == 3
    assert detail["reconciliation_summary"]["placeholder_consumer_count"] == 2
    assert reconciliation["derived_status"] == "effect_planned"
    assert effect["effect_type"] == WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH
    assert effect["job_status"] == "queued"
    assert effect["source"] == "submit_path"
    assert effect["reused"] is False
    assert effect["created"] is True
    assert effect["real_external_call_executed"] is False
    assert "payload_json" not in str(detail)


def _emit_questionnaire_event_to_service(service: InternalEventService, *, key: str) -> dict:
    return service.emit_event(
        event_type=QUESTIONNAIRE_SUBMITTED_EVENT_TYPE,
        aggregate_type="questionnaire_submission",
        aggregate_id=key,
        subject_type="customer",
        subject_id="wm_questionnaire_consumer",
        idempotency_key=f"questionnaire.submitted:{key}",
        source_module="tests.internal_events_questionnaire_slice",
        source_command_id=f"cmd-{key}",
        context=CommandContext(request_id=f"req-{key}", trace_id=key, source_route="/tests/questionnaire-submitted"),
        payload={
            "questionnaire": {
                "id": 1,
                "slug": "hxc-activation-v1",
                "title": "黄小璨激活问卷",
                "external_push_config": {
                    "enabled": True,
                    "webhook_url": "https://hooks.example.invalid/questionnaire",
                    "type": "trial",
                },
            },
            "submission": {
                "submission_id": key,
                "questionnaire_id": 1,
                "slug": "hxc-activation-v1",
                "respondent_key": "respondent_masked",
                "external_userid": "wm_questionnaire_consumer",
                "follow_user_userid": "owner_questionnaire_consumer",
                "unionid": "union_questionnaire_consumer",
                "unionid_present": True,
                "final_tags": [],
                "submitted_at": "2026-06-14T12:00:00Z",
                "answer_count": 1,
            },
            "answer_snapshots": [
                {
                    "question_type": "single_choice",
                    "question_title_snapshot": "黄小璨是否已激活？",
                    "selected_option_texts_snapshot": ["已激活"],
                }
            ],
            "source": {"command_id": f"cmd-{key}"},
        },
        payload_summary={"submission_id": key, "questionnaire_id": 1, "answer_count": 1},
    )


def test_webhook_consumer_reuses_existing_external_effect_job(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "0")
    reset_external_effect_fixture_state()
    registry = InternalEventConsumerRegistry()
    register_questionnaire_event_consumers(registry)
    repo = InMemoryInternalEventRepository()
    service = InternalEventService(repo, registry)
    emitted = _emit_questionnaire_event_to_service(service, key="sub_webhook_reuse")
    existing = ExternalEffectService().plan_effect(
        effect_type=WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
        adapter_name="outbound_webhook",
        operation="post",
        target_type="questionnaire_submission",
        target_id="sub_webhook_reuse",
        business_type="questionnaire",
        business_id="1",
        payload={"webhook_url": "https://hooks.example.invalid/questionnaire"},
        payload_summary={"submission_id": "sub_webhook_reuse"},
        context=CommandContext(trace_id="sub_webhook_reuse"),
        source_module="questionnaire.h5_write",
        execution_mode="shadow",
        status="planned",
        idempotency_key="legacy:sub_webhook_reuse",
    )

    result = InternalEventWorker(repo, registry).run_due(
        batch_size=1,
        dry_run=False,
        event_types=[QUESTIONNAIRE_SUBMITTED_EVENT_TYPE],
        consumer_names=["questionnaire_webhook_consumer"],
    )
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH, "target_id": "sub_webhook_reuse"})
    response_summary = result["items"][0]["attempt"]["response_summary_json"]

    assert result["counts"]["succeeded_count"] == 1
    assert total == 1
    assert jobs[0].id == existing["id"]
    assert response_summary["external_effect_job_reused"] is True
    assert response_summary["external_effect_job_created"] is False
    assert response_summary["external_effect_job_id"] == existing["id"]
    assert ExternalEffectService().list_attempts(existing["id"]) == []
    reconciliation = service.get_event_reconciliation(emitted["event"]["event_id"])
    assert reconciliation["derived_status"] == "effect_reused"
    assert reconciliation["external_effects"][0]["reused"] is True
    assert reconciliation["external_effects"][0]["job_id"] == existing["id"]


def test_unconfigured_questionnaire_consumers_are_skipped_with_reasons() -> None:
    registry = InternalEventConsumerRegistry()
    register_questionnaire_event_consumers(registry)
    repo = InMemoryInternalEventRepository()
    service = InternalEventService(repo, registry)
    emitted = _emit_questionnaire_event_to_service(service, key="sub_noop")
    worker = InternalEventWorker(repo, registry)

    for consumer_name, reason in {
        "automation_questionnaire_consumer": "automation_questionnaire_not_configured",
        "customer_summary_consumer": "customer_summary_not_configured",
    }.items():
        result = worker.run_due(
            batch_size=1,
            dry_run=False,
            event_types=[QUESTIONNAIRE_SUBMITTED_EVENT_TYPE],
            consumer_names=[consumer_name],
        )
        attempts = [attempt for attempt in service.list_attempts(event_id=emitted["event"]["event_id"]) if attempt.consumer_name == consumer_name]
        assert result["counts"]["skipped_count"] == 1
        assert attempts[0].status == "skipped"
        assert attempts[0].response_summary_json["reason"] == reason


def test_reconciliation_explains_placeholder_and_allowlist_pending(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", "payment.succeeded")
    registry = InternalEventConsumerRegistry()
    register_questionnaire_event_consumers(registry)
    repo = InMemoryInternalEventRepository()
    service = InternalEventService(repo, registry)
    emitted = _emit_questionnaire_event_to_service(service, key="sub_pending_explain")

    reconciliation = service.get_event_reconciliation(emitted["event"]["event_id"])
    by_consumer = {item["consumer_name"]: item for item in reconciliation["consumer_states"]}

    assert by_consumer["questionnaire_projection_consumer"]["why_pending"]["category"] == "allowlist_blocked"
    assert by_consumer["questionnaire_projection_consumer"]["why_pending"]["actionable"] is False
    assert by_consumer["questionnaire_projection_consumer"]["why_pending"]["rollout_gated"] is True
    assert by_consumer["questionnaire_tag_consumer"]["why_pending"]["category"] == "allowlist_blocked"
    assert by_consumer["automation_questionnaire_consumer"]["why_pending"]["category"] == "placeholder_not_configured"
    assert by_consumer["customer_summary_consumer"]["why_pending"]["actionable"] is False

    detail = build_event_detail_payload(emitted["event"]["event_id"], service=service)
    assert detail is not None
    assert detail["reconciliation_summary"]["unresolved_consumer_count"] == 0
    assert detail["reconciliation_summary"]["rollout_gated_consumer_count"] == 3
    assert detail["reconciliation_summary"]["placeholder_consumer_count"] == 2


def test_internal_event_api_redacts_questionnaire_summary_and_hides_payload(monkeypatch) -> None:
    client = _client(monkeypatch, questionnaire_enabled=True)
    body = _submit(client, key="api-safety")

    list_response = client.get("/api/admin/internal-events?event_type=questionnaire.submitted")
    detail_response = client.get(f"/api/admin/internal-events/{body['internal_event_id']}")
    reconciliation_response = client.get(f"/api/admin/internal-events/{body['internal_event_id']}/reconciliation")
    list_text = list_response.text
    detail = detail_response.json()

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert reconciliation_response.status_code == 200
    assert reconciliation_response.json()["ok"] is True
    assert reconciliation_response.json()["reconciliation"]["event_id"] == body["internal_event_id"]
    assert "reconciliation" in detail
    assert "derived_status" in detail
    assert "reconciliation_summary" in detail
    assert "payload_json" not in list_text
    assert "原始敏感答案" not in list_text
    assert "wm_questionnaire_api-safety" not in list_text
    assert "13800138000" not in list_text
    assert "openid_api-safety" not in list_text
    assert "payload_json" not in detail
    assert "原始敏感答案" not in str(detail)
    assert "wm_questionnaire_api-safety" not in str(detail)
    assert "13800138000" not in str(detail)


def test_internal_events_admin_page_contains_reconciliation_ui(monkeypatch) -> None:
    client = _client(monkeypatch, questionnaire_enabled=True)

    list_response = client.get("/admin/internal-events")
    detail_response = client.get("/admin/internal-events/questionnaire-reconciliation-contract")

    assert list_response.status_code == 200
    assert 'data-execution-page="internal-list"' in list_response.text
    assert detail_response.status_code == 200
    assert 'data-execution-page="internal-detail"' in detail_response.text
    assert "业务效果核对" in detail_response.text
    assert "消费者执行与对账证据" in detail_response.text
    assert "admin_execution_ui.js" in detail_response.text


def test_worker_payment_allowlist_does_not_scan_questionnaire_until_explicitly_allowed(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", "payment.succeeded")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", "order_projection_consumer")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE", "1")
    registry = InternalEventConsumerRegistry()
    register_questionnaire_event_consumers(registry)
    repo = InMemoryInternalEventRepository()
    service = InternalEventService(repo, registry)
    _emit_questionnaire_event_to_service(service, key="sub_allowlist")
    worker = InternalEventWorker(repo, registry)

    payment_preview = worker.preview_due(batch_size=1)
    explicit_blocked = worker.preview_due(
        batch_size=1,
        event_types=[QUESTIONNAIRE_SUBMITTED_EVENT_TYPE],
        consumer_names=["questionnaire_projection_consumer"],
    )
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", QUESTIONNAIRE_SUBMITTED_EVENT_TYPE)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", "questionnaire_projection_consumer")
    explicit_allowed = worker.preview_due(
        batch_size=1,
        event_types=[QUESTIONNAIRE_SUBMITTED_EVENT_TYPE],
        consumer_names=["questionnaire_projection_consumer"],
    )

    assert payment_preview["counts"]["candidate_count"] == 0
    assert explicit_blocked["counts"]["candidate_count"] == 0
    assert explicit_allowed["counts"]["candidate_count"] == 1
    assert explicit_allowed["items"][0]["event_type"] == QUESTIONNAIRE_SUBMITTED_EVENT_TYPE
    assert explicit_allowed["items"][0]["consumer_name"] == "questionnaire_projection_consumer"
