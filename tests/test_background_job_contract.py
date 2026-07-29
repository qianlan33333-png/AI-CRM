from __future__ import annotations

from dataclasses import replace

from aicrm_next.platform.platform_foundation.background_jobs.contract import (
    BackgroundJobContract,
    BackgroundJobHandlerResult,
    BackgroundJobQueue,
    BackgroundJobWorker,
    WebhookRouteContract,
    enqueue_webhook_job,
    webhook_route_contracts,
)
from tools.check_background_job_contract import check_background_job_contract, validate_background_job_route_contracts


def test_background_job_contract_defines_required_envelope_fields() -> None:
    job = BackgroundJobContract(
        job_type="webhook.demo",
        source_route="/api/demo/webhook",
        idempotency_key="demo-key",
        external_effect_key="webhook.demo.push",
        audit_context={"actor": "pytest"},
    )

    payload = job.to_dict()

    for field in [
        "job_type",
        "source_route",
        "idempotency_key",
        "payload_schema_version",
        "attempt_count",
        "next_run_at",
        "status",
        "external_effect_key",
        "audit_context",
        "created_at",
        "updated_at",
        "last_error",
        "error_code",
    ]:
        assert field in payload
    assert job.status == "pending"


def test_duplicate_webhook_event_does_not_create_second_job() -> None:
    queue = BackgroundJobQueue()
    first = enqueue_webhook_job(
        queue,
        job_type="webhook.demo",
        source_route="/api/demo/webhook",
        payload={"event_id": "evt-1", "value": 1},
        signature_valid=True,
        idempotency_key="provider-event-evt-1",
    )
    duplicate = enqueue_webhook_job(
        queue,
        job_type="webhook.demo",
        source_route="/api/demo/webhook",
        payload={"event_id": "evt-1", "value": 1},
        signature_valid=True,
        idempotency_key="provider-event-evt-1",
    )

    assert first.ok is True
    assert first.created is True
    assert duplicate.ok is True
    assert duplicate.created is False
    assert duplicate.duplicate is True
    assert duplicate.job == first.job
    assert len(queue.list_jobs()) == 1


def test_invalid_payload_or_signature_does_not_enqueue_job() -> None:
    queue = BackgroundJobQueue()

    bad_signature = enqueue_webhook_job(
        queue,
        job_type="webhook.demo",
        source_route="/api/demo/webhook",
        payload={"event_id": "evt-1"},
        signature_valid=False,
    )
    bad_payload = enqueue_webhook_job(
        queue,
        job_type="webhook.demo",
        source_route="/api/demo/webhook",
        payload={},
        signature_valid=True,
    )

    assert bad_signature.ok is False
    assert bad_signature.error_code == "invalid_signature"
    assert bad_payload.ok is False
    assert bad_payload.error_code == "invalid_payload"
    assert queue.list_jobs() == []


def test_enqueue_success_records_audit_context_without_real_external_call() -> None:
    queue = BackgroundJobQueue()

    result = enqueue_webhook_job(
        queue,
        job_type="webhook.demo",
        source_route="/api/demo/webhook",
        payload={"event_id": "evt-1"},
        signature_valid=True,
        external_effect_key="webhook.demo.push",
        audit_context={"request_id": "req-1"},
    )

    assert result.ok is True
    assert result.created is True
    assert result.job is not None
    assert result.job.external_effect_key == "webhook.demo.push"
    assert result.job.audit_context["request_id"] == "req-1"
    assert result.job.audit_context["real_external_call_executed"] is False


def test_worker_success_and_failure_are_auditable() -> None:
    queue = BackgroundJobQueue()
    enqueue_webhook_job(
        queue,
        job_type="webhook.demo",
        source_route="/api/demo/webhook",
        payload={"event_id": "evt-success"},
        signature_valid=True,
        idempotency_key="evt-success",
    )

    worker = BackgroundJobWorker(queue, lambda job: BackgroundJobHandlerResult(status="succeeded", result_summary={"ok": True}))
    result = worker.run_due(max_attempts=3)

    assert result["succeeded_count"] == 1
    assert result["real_external_call_executed"] is False
    assert queue.get("evt-success").status == "succeeded"  # type: ignore[union-attr]

    enqueue_webhook_job(
        queue,
        job_type="webhook.demo",
        source_route="/api/demo/webhook",
        payload={"event_id": "evt-fail"},
        signature_valid=True,
        idempotency_key="evt-fail",
    )
    failing_worker = BackgroundJobWorker(queue, lambda job: BackgroundJobHandlerResult(status="failed", error_code="temporary_failure", last_error="try later"))

    failed = failing_worker.run_due(max_attempts=3)

    assert failed["failed_count"] == 1
    failed_job = queue.get("evt-fail")
    assert failed_job is not None
    assert failed_job.status == "failed"
    assert failed_job.error_code == "temporary_failure"
    assert failed_job.last_error == "try later"


def test_worker_dead_letters_after_retry_budget() -> None:
    queue = BackgroundJobQueue()
    enqueue_webhook_job(
        queue,
        job_type="webhook.demo",
        source_route="/api/demo/webhook",
        payload={"event_id": "evt-dead"},
        signature_valid=True,
        idempotency_key="evt-dead",
    )
    worker = BackgroundJobWorker(queue, lambda job: BackgroundJobHandlerResult(status="failed", error_code="still_failing", last_error="boom"))

    first = worker.run_due(max_attempts=2)
    second = worker.run_due(max_attempts=2)

    assert first["failed_count"] == 1
    assert second["dead_lettered_count"] == 1
    dead = queue.get("evt-dead")
    assert dead is not None
    assert dead.status == "dead_lettered"
    assert dead.attempt_count == 2
    assert dead.error_code == "still_failing"


def test_background_job_route_contract_current_repository_passes() -> None:
    assert check_background_job_contract() == []
    assert len(webhook_route_contracts()) >= 30


def test_background_job_route_contract_blocks_unregistered_webhook_route() -> None:
    manifest = [
        {
            "path": "/api/demo/webhook",
            "methods": ["POST"],
            "route_name": "demo_webhook",
            "capability_owner": "demo",
            "runtime_owner": "ai_crm_next",
            "layer": "webhook",
            "external_effects": "none",
            "data_source": "command",
            "requires_auth": False,
            "rollback": "previous_release",
        }
    ]

    violations = validate_background_job_route_contracts(manifest, ())

    assert len(violations) == 1
    assert violations[0].rule == "missing_webhook_route_contract"
    assert "/api/demo/webhook" in violations[0].route


def test_background_job_route_contract_checks_owner_rollback_effects_and_data_source() -> None:
    contract = WebhookRouteContract(
        path="/api/demo/webhook",
        methods=("POST",),
        route_name="demo_webhook",
        expected_external_effects="none",
        expected_data_source="command",
        external_effects_rationale="records inbound event only",
    )
    manifest = [
        {
            "path": "/api/demo/webhook",
            "methods": ["POST"],
            "route_name": "demo_webhook",
            "capability_owner": "unknown",
            "runtime_owner": "ai_crm_next",
            "layer": "webhook",
            "external_effects": "staging_disabled",
            "data_source": "read_model",
            "requires_auth": False,
            "rollback": "",
        }
    ]

    violations = validate_background_job_route_contracts(manifest, (contract,))
    rules = {violation.rule for violation in violations}

    assert "missing_webhook_route_owner" in rules
    assert "missing_webhook_rollback" in rules
    assert "webhook_external_effects_mismatch" in rules
    assert "webhook_data_source_mismatch" in rules


def test_background_job_route_contract_requires_none_external_effects_rationale() -> None:
    contract = WebhookRouteContract(
        path="/api/demo/webhook",
        methods=("POST",),
        route_name="demo_webhook",
        expected_external_effects="none",
        expected_data_source="command",
        external_effects_rationale="",
    )
    manifest = [
        {
            "path": "/api/demo/webhook",
            "methods": ["POST"],
            "route_name": "demo_webhook",
            "capability_owner": "demo",
            "runtime_owner": "ai_crm_next",
            "layer": "webhook",
            "external_effects": "none",
            "data_source": "command",
            "requires_auth": False,
            "rollback": "previous_release",
        }
    ]

    violations = validate_background_job_route_contracts(manifest, (contract,))

    assert [violation.rule for violation in violations] == ["missing_none_external_effects_rationale"]


def test_background_job_route_contract_detects_stale_contract() -> None:
    contract = WebhookRouteContract(
        path="/api/demo/webhook",
        methods=("POST",),
        route_name="demo_webhook",
        expected_external_effects="none",
        expected_data_source="command",
        external_effects_rationale="records inbound event only",
    )

    violations = validate_background_job_route_contracts([], (contract,))

    assert len(violations) == 1
    assert violations[0].rule == "stale_webhook_route_contract"
