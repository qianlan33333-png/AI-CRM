from __future__ import annotations

import hashlib

import pytest

pytestmark = pytest.mark.usefixtures("composed_internal_event_registry")

from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator.application import ApproveCloudPlanCommand, ApproveCloudPlanRecipientCommand
from aicrm_next.extensions.growth.cloud_orchestrator.repository import build_cloud_plan_repository, reset_cloud_plan_fixture_state
from aicrm_next.main import create_app
from aicrm_next.platform.platform_foundation.external_effects import ExternalEffectService, reset_external_effect_fixture_state
from aicrm_next.platform.platform_foundation.internal_events import InternalEventService, reset_internal_event_fixture_state
from aicrm_next.platform.platform_foundation.internal_events.repository import build_internal_event_repository
from aicrm_next.platform.platform_foundation.internal_events.shadow import BROADCAST_TASK_CREATED_EVENT_TYPE, emit_broadcast_task_created_shadow_event
from aicrm_next.platform.platform_foundation.internal_events.worker import InternalEventWorker

BROADCAST_TASK_CONSUMERS = [
    "audit_projection_consumer",
    "broadcast_queue_projection_consumer",
    "broadcast_task_ai_assist_notify_consumer",
    "push_center_link_consumer",
]


def _hash16(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _reset() -> None:
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()
    reset_cloud_plan_fixture_state()


def _configure(
    monkeypatch,
    *,
    enabled: bool = True,
    allowed_event_types: str = BROADCAST_TASK_CREATED_EVENT_TYPE,
    auto_execute: bool = False,
) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_BROADCAST_TASK_ENABLED", "1" if enabled else "0")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", allowed_event_types)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", "")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS", "")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_SHADOW_ONLY", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1" if auto_execute else "0")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "0")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", "")
    _reset()
    return TestClient(create_app(), raise_server_exceptions=False)


def _approve_recipient(plan_id: str = "plan_probe") -> dict:
    repo = build_cloud_plan_repository()
    repo.recipients[:] = [
        row
        for row in repo.recipients
        if row.get("plan_id") != "plan_probe" or int(row.get("id") or 0) == 1
    ]
    repo.messages[:] = [
        row
        for row in repo.messages
        if row.get("plan_id") != "plan_probe" or int(row.get("recipient_id") or 0) == 1
    ]
    for collection_name in ("plans", "recipients", "messages"):
        for row in getattr(repo, collection_name, []):
            if row.get("plan_id") == "plan_probe":
                row["plan_id"] = plan_id
    plan = ApproveCloudPlanCommand().execute(plan_id, operator="pytest")
    assert plan["ok"] is True
    result = ApproveCloudPlanRecipientCommand().execute(plan_id, 1, operator="pytest")
    assert result["ok"] is True
    return result


def _event():
    events, total = InternalEventService().list_events({"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE})
    assert total == 1
    return events[0]


def _runs(event_id: str):
    return InternalEventService().list_consumer_runs({"event_id": event_id})


def _run_consumer(event_id: str, consumer_name: str) -> dict:
    return InternalEventWorker().dispatch_one_consumer(
        event_id,
        consumer_name,
        dry_run=False,
        force=False,
        reason="broadcast_task_slice_unit_test",
    )


def test_broadcast_task_flag_off_does_not_emit(monkeypatch) -> None:
    _configure(monkeypatch, enabled=False)

    result = _approve_recipient()
    events, total = InternalEventService().list_events({"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE})

    assert result["status"] == "already_approved"
    assert result["internal_event_status"] == "skipped"
    assert result["internal_event_reason"] == "broadcast_task_internal_events_disabled"
    assert result["internal_event_id"] == ""
    assert result["internal_event_consumer_run_count"] == 0
    assert events == []
    assert total == 0


def test_broadcast_task_requires_explicit_event_type_allowlist(monkeypatch) -> None:
    _configure(monkeypatch, enabled=True, allowed_event_types="")

    result = _approve_recipient()
    events, total = InternalEventService().list_events({"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE})

    assert result["internal_event_status"] == "skipped"
    assert result["internal_event_reason"] == "broadcast_task_event_type_not_explicitly_allowed"
    assert result["internal_event_id"] == ""
    assert events == []
    assert total == 0


def test_broadcast_task_created_emits_once_with_expected_safe_schema_and_consumers(monkeypatch) -> None:
    _configure(monkeypatch)
    plan_id = "p0-1284-plan-probe"
    plan_hash = _hash16(plan_id)
    plan_ref = f"ops_plan_ref:{plan_hash}"

    result = _approve_recipient(plan_id)
    duplicate = ApproveCloudPlanRecipientCommand().execute(plan_id, 1, operator="pytest")
    event = _event()
    safe_trace_id = f"broadcast_task.created:{result['job_id']}"
    trace_events, trace_total = InternalEventService().list_events({"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "trace_id": safe_trace_id})
    original_trace_events, original_trace_total = InternalEventService().list_events(
        {"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "original_trace_hash": plan_id}
    )
    trace_hash_events, trace_hash_total = InternalEventService().list_events(
        {"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "trace_hash": plan_hash}
    )
    runs, run_total = _runs(event.event_id)
    broadcast_payload = event.payload_json["broadcast_task"]

    assert result["status"] == "already_approved"
    assert result["internal_event_status"] == "emitted"
    assert result["internal_event_id"] == event.event_id
    assert result["internal_event_consumer_run_count"] == 4
    assert duplicate["status"] == "already_approved"
    assert duplicate["internal_event_status"] == "emitted"
    assert duplicate["internal_event_id"] == event.event_id
    assert trace_total == 1
    assert trace_events[0].event_id == event.event_id
    assert original_trace_total == 1
    assert original_trace_events[0].event_id == event.event_id
    assert trace_hash_total == 1
    assert trace_hash_events[0].event_id == event.event_id
    assert event.trace_id == safe_trace_id
    assert event.correlation_id == safe_trace_id
    assert event.event_type == BROADCAST_TASK_CREATED_EVENT_TYPE
    assert event.aggregate_type == "broadcast_task"
    assert event.aggregate_id == str(result["job_id"])
    assert event.subject_type == "broadcast_task"
    assert event.subject_id == str(result["job_id"])
    assert event.idempotency_key == f"broadcast_task.created:{result['job_id']}"
    assert event.payload_summary_json == {
        "task_id": str(result["job_id"]),
        "task_type": "cloud_plan",
        "send_channel": "wecom_private",
        "source": "cloud_plan_approval",
        "campaign_code": "",
        "ops_plan_id": plan_ref,
        "ops_plan_ref": plan_ref,
        "ops_plan_hash": plan_hash,
        "ops_plan_present": True,
        "target_count": 1,
        "status": "created",
        "scheduled": False,
    }
    payload_json_text = str(event.payload_json)
    payload_text = payload_json_text + str(event.payload_summary_json)
    assert plan_id not in payload_json_text
    assert plan_id not in payload_text
    assert broadcast_payload["trace_id"] == safe_trace_id
    assert broadcast_payload["original_trace_ref"] == f"trace_ref:{plan_hash}"
    assert broadcast_payload["original_trace_present"] is True
    assert broadcast_payload["original_trace_hash"] == plan_hash
    assert len(broadcast_payload["original_trace_hash"]) == 16
    assert broadcast_payload["related_ops_plan_id"] == plan_ref
    assert broadcast_payload["related_ops_plan_hash"] == plan_hash
    assert broadcast_payload["related_ops_plan_present"] is True
    assert "wm_a" not in payload_text
    assert "13800138000" not in payload_text
    assert "webhook" not in payload_text.lower()
    assert "secret" not in payload_text.lower()
    assert "token" not in payload_text.lower()
    assert run_total == 4
    assert sorted(run.consumer_name for run in runs) == BROADCAST_TASK_CONSUMERS
    assert "ai_assist_notify_consumer" not in [run.consumer_name for run in runs]
    assert all(run.status == "pending" for run in runs)
    assert all(run.attempt_count == 0 for run in runs)


def test_broadcast_task_direct_emit_is_idempotent(monkeypatch) -> None:
    _configure(monkeypatch)
    raw_external_userid = "wm_raw_external_userid_abcdef1234567890"
    raw_mobile = "13800138000"
    raw_openid = "openid_raw_1234567890"
    raw_unionid = "unionid_raw_1234567890"
    raw_source_id = f"plan:trigger:{raw_external_userid}:{raw_mobile}:{raw_openid}:{raw_unionid}:send_private_message"
    raw_trace_id = f"trace:{raw_external_userid}:{raw_mobile}:{raw_openid}:{raw_unionid}"
    raw_idempotency_key = f"idempotency:{raw_external_userid}:{raw_mobile}:{raw_openid}:{raw_unionid}"
    job = {
        "id": "broadcast-direct-1",
        "source_type": "unit_test",
        "source_id": raw_source_id,
        "trace_id": raw_trace_id,
        "idempotency_key": raw_idempotency_key,
        "target_count": 2,
        "target_external_userids": [raw_external_userid, "wm_direct_b"],
        "content_summary": "完整消息正文不进入摘要",
        "created_by": "pytest",
    }

    first = emit_broadcast_task_created_shadow_event(
        job=job,
        source_module="tests.broadcast_task_slice",
        source_route="/tests/broadcast-task",
        operator="pytest",
        source="unit_test",
    )
    second = emit_broadcast_task_created_shadow_event(
        job=job,
        source_module="tests.broadcast_task_slice",
        source_route="/tests/broadcast-task",
        operator="pytest",
        source="unit_test",
    )
    events, total = InternalEventService().list_events({"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "aggregate_id": "broadcast-direct-1"})
    raw_trace_events, raw_trace_total = InternalEventService().list_events(
        {"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "original_trace_hash": raw_trace_id}
    )
    trace_hash_events, trace_hash_total = InternalEventService().list_events(
        {"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "trace_hash": _hash16(raw_trace_id)}
    )
    runs, run_total = _runs(events[0].event_id)
    event = events[0]
    broadcast_payload = event.payload_json["broadcast_task"]
    payload_text = (
        str(event.payload_json)
        + str(event.payload_summary_json)
        + str(event.source_command_id)
        + str(event.trace_id)
        + str(event.correlation_id)
    )

    assert first["status"] == "emitted"
    assert second["status"] == "emitted"
    assert second["event_id"] == first["event_id"]
    assert total == 1
    assert raw_trace_total == 1
    assert raw_trace_events[0].event_id == event.event_id
    assert trace_hash_total == 1
    assert trace_hash_events[0].event_id == event.event_id
    assert raw_source_id not in payload_text
    assert raw_external_userid not in payload_text
    assert raw_mobile not in payload_text
    assert raw_openid not in payload_text
    assert raw_unionid not in payload_text
    assert raw_trace_id not in payload_text
    assert raw_idempotency_key not in payload_text
    assert broadcast_payload["source_id"].startswith("source_ref:")
    assert broadcast_payload["source_id_redacted"] == broadcast_payload["source_id"]
    assert len(broadcast_payload["source_id_hash"]) == 16
    assert broadcast_payload["source_id_present"] is True
    assert broadcast_payload["trace_id"] == "broadcast_task.created:broadcast-direct-1"
    assert broadcast_payload["trace_id"] != raw_trace_id
    assert broadcast_payload["original_trace_ref"] == f"trace_ref:{_hash16(raw_trace_id)}"
    assert broadcast_payload["original_trace_present"] is True
    assert broadcast_payload["original_trace_hash"] == _hash16(raw_trace_id)
    assert len(broadcast_payload["original_trace_hash"]) == 16
    assert broadcast_payload["trace_id_present"] is True
    assert len(broadcast_payload["trace_id_hash"]) == 16
    assert broadcast_payload["original_idempotency_key_present"] is True
    assert broadcast_payload["original_idempotency_key_hash"] == _hash16(raw_idempotency_key)
    assert len(broadcast_payload["original_idempotency_key_hash"]) == 16
    assert broadcast_payload["idempotency_key_present"] is True
    assert len(broadcast_payload["idempotency_key_hash"]) == 16
    assert broadcast_payload["command_id"] == "broadcast_task.created:broadcast-direct-1"
    assert broadcast_payload["command_id"] != raw_source_id
    assert event.trace_id == "broadcast_task.created:broadcast-direct-1"
    assert event.trace_id != raw_trace_id
    assert event.correlation_id == "broadcast_task.created:broadcast-direct-1"
    assert event.correlation_id != raw_idempotency_key
    assert event.source_command_id == "broadcast_task.created:broadcast-direct-1"
    assert event.source_command_id != raw_source_id
    assert event.idempotency_key == "broadcast_task.created:broadcast-direct-1"
    assert run_total == 4
    assert sorted(run.consumer_name for run in runs) == BROADCAST_TASK_CONSUMERS


def test_broadcast_task_trace_lookup_handles_16_hex_raw_trace(monkeypatch) -> None:
    _configure(monkeypatch)
    raw_trace_id = "abcdef1234567890"
    raw_trace_hash = _hash16(raw_trace_id)
    job = {
        "id": "broadcast-hex-trace",
        "source_type": "unit_test",
        "source_id": raw_trace_id,
        "trace_id": raw_trace_id,
        "idempotency_key": raw_trace_id,
        "target_count": 1,
        "created_by": "pytest",
    }

    result = emit_broadcast_task_created_shadow_event(
        job=job,
        source_module="tests.broadcast_task_slice",
        source_route="/tests/broadcast-task",
        operator="pytest",
        source="unit_test",
    )
    events, total = InternalEventService().list_events({"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "aggregate_id": "broadcast-hex-trace"})
    raw_original_events, raw_original_total = InternalEventService().list_events(
        {"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "original_trace_hash": raw_trace_id}
    )
    raw_trace_events, raw_trace_total = InternalEventService().list_events(
        {"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "trace_hash": raw_trace_id}
    )
    hashed_events, hashed_total = InternalEventService().list_events(
        {"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "trace_hash": raw_trace_hash}
    )
    event = events[0]
    payload_text = str(event.payload_json) + str(event.payload_summary_json) + event.trace_id + event.correlation_id + event.source_command_id

    assert result["status"] == "emitted"
    assert total == 1
    assert event.payload_json["broadcast_task"]["original_trace_hash"] == raw_trace_hash
    assert event.payload_json["broadcast_task"]["trace_id"] == "broadcast_task.created:broadcast-hex-trace"
    assert event.trace_id == "broadcast_task.created:broadcast-hex-trace"
    assert raw_trace_id not in payload_text
    assert raw_original_total == 1
    assert raw_original_events[0].event_id == event.event_id
    assert raw_trace_total == 1
    assert raw_trace_events[0].event_id == event.event_id
    assert hashed_total == 1
    assert hashed_events[0].event_id == event.event_id
    runs, run_total = _runs(event.event_id)
    assert run_total == 4
    assert sorted(run.consumer_name for run in runs) == BROADCAST_TASK_CONSUMERS


def test_broadcast_task_consumers_are_noop_or_skipped_without_external_work(monkeypatch) -> None:
    _configure(monkeypatch)
    _approve_recipient()
    event = _event()
    queue = _run_consumer(event.event_id, "broadcast_queue_projection_consumer")
    push_center = _run_consumer(event.event_id, "push_center_link_consumer")
    ai_assist = _run_consumer(event.event_id, "broadcast_task_ai_assist_notify_consumer")
    audit = _run_consumer(event.event_id, "audit_projection_consumer")
    _jobs, job_total = ExternalEffectService().list_jobs({})

    assert queue["consumer_run"]["status"] == "succeeded"
    assert queue["attempt"]["response_summary_json"]["broadcast_queue_projection"] == "broadcast_task_created_recorded"
    assert push_center["consumer_run"]["status"] == "succeeded"
    assert push_center["attempt"]["response_summary_json"]["push_center_link"] == "shadow_only"
    assert ai_assist["consumer_run"]["status"] == "skipped"
    assert ai_assist["attempt"]["response_summary_json"]["reason"] == "broadcast_task_ai_assist_notify_not_configured"
    assert audit["consumer_run"]["status"] == "succeeded"
    assert audit["attempt"]["response_summary_json"]["audit_projection"] == "broadcast_task_created_recorded"
    assert job_total == 0


def test_broadcast_task_legacy_ai_assist_alias_dispatches_without_new_fanout(monkeypatch) -> None:
    _configure(monkeypatch)
    _approve_recipient()
    event = _event()
    repo = build_internal_event_repository()
    legacy_run = repo.create_consumer_run(event=event, consumer_name="ai_assist_notify_consumer", consumer_type="orchestration")

    result = InternalEventWorker().dispatch_one_consumer(
        event.event_id,
        "ai_assist_notify_consumer",
        dry_run=False,
        force=False,
        reason="broadcast_task_legacy_alias_unit_test",
    )

    assert legacy_run.consumer_name == "ai_assist_notify_consumer"
    assert result["consumer_run"]["status"] == "skipped"
    assert result["attempt"]["response_summary_json"]["reason"] == "broadcast_task_legacy_ai_assist_notify_not_configured"
    runs, run_total = _runs(event.event_id)
    assert run_total == 5
    assert sorted(run.consumer_name for run in runs) == sorted([*BROADCAST_TASK_CONSUMERS, "ai_assist_notify_consumer"])


def test_broadcast_task_admin_api_redacts_summary_and_hides_payload_json(monkeypatch) -> None:
    client = _configure(monkeypatch)
    plan_id = "p0-1284-plan-probe"
    plan_trace_ref = f"trace_ref:{_hash16(plan_id)}"
    _approve_recipient(plan_id)
    event = _event()

    list_payload = client.get("/api/admin/internal-events", params={"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE}).json()
    original_trace_lookup_payload = client.get(
        "/api/admin/internal-events",
        params={"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "original_trace_hash": plan_id},
    ).json()
    raw_trace_lookup_payload = client.get(
        "/api/admin/internal-events",
        params={"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "trace_hash": plan_id},
    ).json()
    hashed_trace_lookup_payload = client.get(
        "/api/admin/internal-events",
        params={"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "trace_hash": _hash16(plan_id)},
    ).json()
    diagnostics_payload = client.get(
        "/api/admin/internal-events/diagnostics",
        params={"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "original_trace_hash": plan_id},
    ).json()
    detail_payload = client.get(f"/api/admin/internal-events/{event.event_id}").json()

    assert list_payload["ok"] is True
    assert original_trace_lookup_payload["ok"] is True
    assert original_trace_lookup_payload["total"] == 1
    assert original_trace_lookup_payload["items"][0]["event_id"] == event.event_id
    assert original_trace_lookup_payload["filters"]["original_trace_hash"] == plan_trace_ref
    assert original_trace_lookup_payload["filters"]["original_trace_hash"] != plan_id
    assert raw_trace_lookup_payload["ok"] is True
    assert raw_trace_lookup_payload["total"] == 1
    assert raw_trace_lookup_payload["items"][0]["event_id"] == event.event_id
    assert raw_trace_lookup_payload["filters"]["trace_hash"] == plan_trace_ref
    assert raw_trace_lookup_payload["filters"]["trace_hash"] != plan_id
    assert hashed_trace_lookup_payload["ok"] is True
    assert hashed_trace_lookup_payload["total"] == 1
    assert hashed_trace_lookup_payload["items"][0]["event_id"] == event.event_id
    assert hashed_trace_lookup_payload["filters"]["trace_hash"] == f"trace_ref:{_hash16(_hash16(plan_id))}"
    assert diagnostics_payload["filters"]["original_trace_hash"] == plan_trace_ref
    assert "payload_json" not in list_payload["items"][0]
    assert "payload_json" not in detail_payload
    raw_filter_response_text = str(original_trace_lookup_payload) + str(raw_trace_lookup_payload) + str(hashed_trace_lookup_payload) + str(diagnostics_payload)
    payload_text = (
        str(list_payload)
        + str(original_trace_lookup_payload)
        + str(raw_trace_lookup_payload)
        + str(hashed_trace_lookup_payload)
        + str(detail_payload)
    )
    assert plan_id not in raw_filter_response_text
    assert plan_id not in payload_text
    assert "wm_a" not in payload_text
    assert "13800138000" not in payload_text
    assert "完整消息正文" not in payload_text
    assert "webhook" not in payload_text.lower()


def test_broadcast_task_admin_api_redacts_16_hex_filter_echo(monkeypatch) -> None:
    client = _configure(monkeypatch)
    raw_trace_id = "abcdef1234567890"
    job = {
        "id": "broadcast-api-hex-trace",
        "source_type": "unit_test",
        "source_id": raw_trace_id,
        "trace_id": raw_trace_id,
        "idempotency_key": raw_trace_id,
        "target_count": 1,
        "created_by": "pytest",
    }
    emit_broadcast_task_created_shadow_event(
        job=job,
        source_module="tests.broadcast_task_slice",
        source_route="/tests/broadcast-task",
        operator="pytest",
        source="unit_test",
    )
    event = _event()

    original_trace_lookup_payload = client.get(
        "/api/admin/internal-events",
        params={"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "original_trace_hash": raw_trace_id},
    ).json()
    raw_trace_lookup_payload = client.get(
        "/api/admin/internal-events",
        params={"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "trace_hash": raw_trace_id},
    ).json()
    hashed_trace_lookup_payload = client.get(
        "/api/admin/internal-events",
        params={"event_type": BROADCAST_TASK_CREATED_EVENT_TYPE, "trace_hash": _hash16(raw_trace_id)},
    ).json()

    assert original_trace_lookup_payload["ok"] is True
    assert original_trace_lookup_payload["total"] == 1
    assert original_trace_lookup_payload["items"][0]["event_id"] == event.event_id
    assert original_trace_lookup_payload["filters"]["original_trace_hash"] == f"trace_ref:{_hash16(raw_trace_id)}"
    assert original_trace_lookup_payload["filters"]["original_trace_hash"] != raw_trace_id
    assert raw_trace_lookup_payload["ok"] is True
    assert raw_trace_lookup_payload["total"] == 1
    assert raw_trace_lookup_payload["items"][0]["event_id"] == event.event_id
    assert raw_trace_lookup_payload["filters"]["trace_hash"] == f"trace_ref:{_hash16(raw_trace_id)}"
    assert raw_trace_lookup_payload["filters"]["trace_hash"] != raw_trace_id
    assert hashed_trace_lookup_payload["ok"] is True
    assert hashed_trace_lookup_payload["total"] == 1
    assert hashed_trace_lookup_payload["items"][0]["event_id"] == event.event_id
    response_text = str(original_trace_lookup_payload) + str(raw_trace_lookup_payload) + str(hashed_trace_lookup_payload)
    assert raw_trace_id not in response_text


def test_broadcast_task_pair_allowlist_blocks_auto_execute_but_single_consumer_still_works(monkeypatch) -> None:
    _configure(
        monkeypatch,
        allowed_event_types="payment.succeeded,broadcast_task.created",
        auto_execute=True,
    )
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", "order_projection_consumer,broadcast_queue_projection_consumer")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS", "payment.succeeded:order_projection_consumer")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE", "1")
    _approve_recipient()
    event = _event()
    worker = InternalEventWorker()

    preview = worker.preview_due(
        batch_size=1,
        event_types=[BROADCAST_TASK_CREATED_EVENT_TYPE],
        consumer_names=["broadcast_queue_projection_consumer", "audit_projection_consumer"],
    )
    execute = worker.run_due(
        batch_size=1,
        dry_run=False,
        event_types=[BROADCAST_TASK_CREATED_EVENT_TYPE],
        consumer_names=["broadcast_queue_projection_consumer", "audit_projection_consumer"],
    )
    manual = worker.dispatch_one_consumer(
        event.event_id,
        "broadcast_queue_projection_consumer",
        dry_run=False,
        force=False,
        reason="broadcast_task_manual_single_consumer_test",
    )
    runs, _ = _runs(event.event_id)

    assert preview["counts"]["candidate_count"] == 0
    assert preview["event_consumers"] == []
    assert execute["counts"]["processed_count"] == 0
    assert execute["event_consumers"] == []
    assert manual["consumer_run"]["status"] == "succeeded"
    assert next(run for run in runs if run.consumer_name == "broadcast_task_ai_assist_notify_consumer").status == "pending"


def test_diagnostics_exposes_broadcast_task_flag(monkeypatch) -> None:
    client = _configure(monkeypatch)

    response = client.get("/api/admin/internal-events/diagnostics")

    assert response.status_code == 200
    assert response.json()["broadcast_task_internal_events_enabled"] is True
