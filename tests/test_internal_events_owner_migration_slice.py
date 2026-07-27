from __future__ import annotations

import hashlib

import pytest

pytestmark = pytest.mark.usefixtures("composed_internal_event_registry")

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.crm.owner_migration.application import OwnerMigrationCommand, OwnerMigrationService
from aicrm_next.platform.platform_foundation.external_effects import ExternalEffectService, reset_external_effect_fixture_state
from aicrm_next.platform.platform_foundation.internal_events import InternalEventService, reset_internal_event_fixture_state
from aicrm_next.platform.platform_foundation.internal_events.repository import build_internal_event_repository
from aicrm_next.platform.platform_foundation.internal_events.shadow import OWNER_MIGRATION_EXECUTED_EVENT_TYPE, emit_owner_migration_executed_shadow_event
from aicrm_next.platform.platform_foundation.internal_events.worker import InternalEventWorker

OWNER_MIGRATION_CONSUMERS = [
    "customer_owner_projection_consumer",
    "customer_summary_mark_dirty_consumer",
    "owner_migration_ai_assist_notify_consumer",
    "webhook_owner_migration_consumer",
]


class MinimalOwnerMigrationRepo:
    source_status = "unit_test"

    def preview_owner_migration(self, *, source_owner_userid: str, target_owner_userid: str, external_userids: list[str] | None = None) -> dict:
        del source_owner_userid, target_owner_userid
        candidates = list(external_userids or ["wm_owner_a", "wm_owner_b"])
        return {
            "source_status": self.source_status,
            "candidate_count": len(candidates),
            "all_external_userids": candidates,
            "sample_external_userids": candidates[:2],
            "surface_counts": {"contacts": len(candidates)},
            "pending_review": {},
        }

    def execute_owner_migration(
        self,
        *,
        source_owner_userid: str,
        target_owner_userid: str,
        operator: str,
        external_userids: list[str] | None = None,
        target_owner_display_name: str | None = None,
    ) -> dict:
        del source_owner_userid, target_owner_userid, operator, target_owner_display_name
        touched = list(external_userids or ["wm_owner_a", "wm_owner_b"])
        return {
            "executed": True,
            "touched_count": len(touched),
            "update_counts": {"contacts": len(touched)},
            "touched_external_userids": touched,
        }

    def resolve_operation_members(self, userids: list[str]) -> dict:
        return {userid: {"user_id": userid, "display_name": userid, "status": "active"} for userid in userids}

    def lookup_customer_owners(self, external_userids: list[str]) -> dict:
        return {external_userid: {"owner_userids": ["owner_a"], "customer_name": external_userid} for external_userid in external_userids}

    def save_import_session(self, session: dict) -> None:
        del session

    def get_import_session(self, session_id: str) -> dict | None:
        del session_id
        return None

    def save_preview(self, preview: dict) -> None:
        del preview

    def get_preview(self, preview_token: str) -> dict | None:
        del preview_token
        return None

    def get_latest_preview_by_session(self, session_id: str) -> dict | None:
        del session_id
        return None

    def mark_preview_executed(self, preview_token: str, result_id: str) -> None:
        del preview_token, result_id

    def save_result(self, result: dict) -> None:
        del result

    def get_result(self, result_id: str) -> dict | None:
        del result_id
        return None

    def audit_owner_migration_event(self, event_type: str, payload: dict) -> None:
        del event_type, payload


def _reset() -> None:
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()


def _configure(
    monkeypatch,
    *,
    enabled: bool = True,
    allowed_event_types: str = OWNER_MIGRATION_EXECUTED_EVENT_TYPE,
    auto_execute: bool = False,
) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_OWNER_MIGRATION_ENABLED", "1" if enabled else "0")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", allowed_event_types)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_SHADOW_ONLY", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1" if auto_execute else "0")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", "")
    monkeypatch.setenv(
        "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS",
        "payment.succeeded:order_projection_consumer,payment.succeeded:customer_business_summary_consumer,"
        "payment.succeeded:dnd_policy_consumer,payment.succeeded:ai_assist_notify_consumer,"
        "payment.succeeded:webhook_order_paid_consumer",
    )
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "0")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", "")
    _reset()
    return TestClient(create_app(), raise_server_exceptions=False)


def _execute() -> dict:
    return OwnerMigrationService(MinimalOwnerMigrationRepo()).run(
        OwnerMigrationCommand(
            source_owner_userid="owner_a",
            target_owner_userid="owner_b",
            operator="pytest",
            execute=True,
            confirm=True,
            perform_wecom_transfer=False,
        )
    )


def _legacy_result_id() -> str:
    return f"legacy:{hashlib.sha256('owner_a:owner_b:pytest'.encode('utf-8')).hexdigest()[:16]}"


def _events():
    return InternalEventService().list_events({"event_type": OWNER_MIGRATION_EXECUTED_EVENT_TYPE})


def _event():
    events, total = _events()
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
        reason="owner_migration_slice_unit_test",
    )


def _safe_rows(count: int) -> list[dict]:
    return [{"external_userid": f"wm_sensitive_{index}"} for index in range(count)]


def _emit_direct_owner_migration_event(result: dict) -> dict:
    emitted = emit_owner_migration_executed_shadow_event(
        command=OwnerMigrationCommand(
            source_owner_userid="owner_a",
            target_owner_userid="owner_b",
            operator="pytest",
            execute=True,
            confirm=True,
            perform_wecom_transfer=False,
        ),
        result={
            "result_id": "omr_count_semantics",
            "source_owner_userid": "owner_a",
            "target_owner_userid": "owner_b",
            "operator": "pytest",
            **result,
        },
    )
    assert emitted["status"] == "emitted"
    return _event()


def _assert_no_sensitive_payload_text(event) -> None:
    payload_text = str(event.payload_json) + str(event.payload_summary_json)

    assert "wm_sensitive_" not in payload_text
    assert "external_userid" not in payload_text.lower()
    assert "13800138000" not in payload_text
    assert "mobile" not in payload_text.lower()
    assert "openid" not in payload_text.lower()
    assert "unionid" not in payload_text.lower()
    assert "token" not in payload_text.lower()
    assert "secret" not in payload_text.lower()


def test_owner_migration_flag_off_does_not_emit(monkeypatch) -> None:
    _configure(monkeypatch, enabled=False)

    result = _execute()
    events, total = _events()

    assert result["ok"] is True
    assert result["internal_event_status"] == "skipped"
    assert result["internal_event_reason"] == "owner_migration_internal_events_disabled"
    assert result["internal_event_id"] == ""
    assert result["internal_event_consumer_run_count"] == 0
    assert events == []
    assert total == 0


def test_owner_migration_requires_explicit_event_type_allowlist(monkeypatch) -> None:
    _configure(monkeypatch, allowed_event_types="")

    result = _execute()
    events, total = _events()

    assert result["internal_event_status"] == "skipped"
    assert result["internal_event_reason"] == "owner_migration_event_type_not_explicitly_allowed"
    assert result["internal_event_id"] == ""
    assert events == []
    assert total == 0


def test_owner_migration_executed_emits_once_with_expected_safe_schema_and_consumers(monkeypatch) -> None:
    _configure(monkeypatch)

    result = _execute()
    duplicate = _execute()
    event = _event()
    aggregate_id = _legacy_result_id()
    runs, run_total = _runs(event.event_id)
    migration_payload = event.payload_json["owner_migration"]
    payload_text = str(event.payload_json) + str(event.payload_summary_json)

    assert result["ok"] is True
    assert result["mode"] == "execute"
    assert result["internal_event_status"] == "emitted"
    assert result["internal_event_id"] == event.event_id
    assert result["internal_event_consumer_run_count"] == 4
    assert duplicate["internal_event_id"] == event.event_id
    assert event.event_type == OWNER_MIGRATION_EXECUTED_EVENT_TYPE
    assert event.aggregate_type == "owner_migration"
    assert event.aggregate_id == aggregate_id
    assert event.subject_type == "owner_migration"
    assert event.subject_id == aggregate_id
    assert event.idempotency_key == f"owner_migration.executed:{aggregate_id}"
    assert event.source_module == "owner_migration.application"
    assert event.trace_id == f"owner_migration.executed:{aggregate_id}"
    assert event.source_command_id == f"owner_migration.executed:{aggregate_id}"
    assert event.correlation_id == f"owner_migration.executed:{aggregate_id}"
    assert event.payload_summary_json["migration_id"] == aggregate_id
    assert event.payload_summary_json["customer_count"] == 2
    assert event.payload_summary_json["success_count"] == 2
    assert event.payload_summary_json["failed_count"] == 0
    assert event.payload_summary_json["skipped_count"] == 0
    assert event.payload_summary_json["count_consistency"] == "ok"
    assert event.payload_summary_json["count_source"]["success_count"] == "update_counts.contacts"
    assert event.payload_summary_json["partial_failure_present"] is False
    assert event.payload_summary_json["all_failed"] is False
    assert event.payload_summary_json["from_owner_present"] is True
    assert event.payload_summary_json["to_owner_present"] is True
    assert migration_payload["customer_scope_present"] is True
    assert len(migration_payload["customer_scope_hash"]) == 16
    assert "owner_a" not in payload_text
    assert "owner_b" not in payload_text
    assert "wm_owner_a" not in payload_text
    assert "wm_owner_b" not in payload_text
    assert "13800138000" not in payload_text
    assert "openid" not in payload_text.lower()
    assert "unionid" not in payload_text.lower()
    assert "webhook" not in payload_text.lower()
    assert "secret" not in payload_text.lower()
    assert "token" not in payload_text.lower()
    assert run_total == 4
    assert sorted(run.consumer_name for run in runs) == sorted(OWNER_MIGRATION_CONSUMERS)
    assert "ai_assist_notify_consumer" not in [run.consumer_name for run in runs]
    assert all(run.status == "pending" for run in runs)
    assert all(run.attempt_count == 0 for run in runs)


def test_owner_migration_all_failed_transfer_does_not_count_as_success(monkeypatch) -> None:
    _configure(monkeypatch)

    event = _emit_direct_owner_migration_event(
        {
            "rows": _safe_rows(3),
            "wecom_transfer": {"failed_count": 3, "failed_customers": _safe_rows(3)},
        }
    )

    assert event.payload_summary_json["customer_count"] == 3
    assert event.payload_summary_json["success_count"] == 0
    assert event.payload_summary_json["failed_count"] == 3
    assert event.payload_summary_json["all_failed"] is True
    assert event.payload_summary_json["partial_failure_present"] is True
    assert event.payload_summary_json["count_consistency"] != "misleading"
    assert event.payload_json["owner_migration"]["count_source"]["success_count"] == "customer_count_minus_failed_count"
    _assert_no_sensitive_payload_text(event)


def test_owner_migration_partial_failure_infers_remaining_success_count(monkeypatch) -> None:
    _configure(monkeypatch)

    event = _emit_direct_owner_migration_event(
        {
            "rows": _safe_rows(5),
            "wecom_transfer": {"failed_count": 2},
        }
    )

    assert event.payload_summary_json["customer_count"] == 5
    assert event.payload_summary_json["success_count"] == 3
    assert event.payload_summary_json["failed_count"] == 2
    assert event.payload_summary_json["all_failed"] is False
    assert event.payload_summary_json["partial_failure_present"] is True
    assert event.payload_summary_json["count_consistency"] == "inferred_from_customer_minus_failed"


def test_owner_migration_explicit_success_count_wins_when_consistent(monkeypatch) -> None:
    _configure(monkeypatch)

    event = _emit_direct_owner_migration_event(
        {
            "rows": _safe_rows(5),
            "success_count": 4,
            "failed_count": 1,
        }
    )

    assert event.payload_summary_json["customer_count"] == 5
    assert event.payload_summary_json["success_count"] == 4
    assert event.payload_summary_json["failed_count"] == 1
    assert event.payload_summary_json["count_consistency"] == "ok"
    assert event.payload_summary_json["count_source"]["success_count"] == "result.success_count"
    assert event.payload_summary_json["partial_failure_present"] is True


def test_owner_migration_zero_crm_update_does_not_fallback_to_customer_count(monkeypatch) -> None:
    _configure(monkeypatch)

    event = _emit_direct_owner_migration_event(
        {
            "candidate_count": 3,
            "crm_updated": 0,
            "failed_count": 0,
        }
    )

    assert event.payload_summary_json["customer_count"] == 3
    assert event.payload_summary_json["success_count"] == 0
    assert event.payload_summary_json["failed_count"] == 0
    assert event.payload_summary_json["count_consistency"] == "ok"
    assert event.payload_summary_json["count_source"]["success_count"] == "result.crm_updated"
    assert event.payload_summary_json["partial_failure_present"] is False
    assert event.payload_summary_json["all_failed"] is False


def test_owner_migration_admin_api_redacts_summary_and_hides_payload_json(monkeypatch) -> None:
    client = _configure(monkeypatch)
    _execute()
    event = _event()

    list_payload = client.get("/api/admin/internal-events", params={"event_type": OWNER_MIGRATION_EXECUTED_EVENT_TYPE}).json()
    detail_payload = client.get(f"/api/admin/internal-events/{event.event_id}").json()
    response_text = str(list_payload) + str(detail_payload)

    assert "owner_a" not in response_text
    assert "owner_b" not in response_text
    assert "wm_owner_a" not in response_text
    assert "wm_owner_b" not in response_text
    assert "13800138000" not in response_text
    assert "payload_json" not in list_payload["items"][0]
    assert "payload_json" not in detail_payload
    assert "payload_json" not in detail_payload["event"]


def test_owner_migration_consumers_are_noop_or_skipped_without_external_work(monkeypatch) -> None:
    _configure(monkeypatch)
    _execute()
    event = _event()

    owner_projection = _run_consumer(event.event_id, "customer_owner_projection_consumer")
    summary = _run_consumer(event.event_id, "customer_summary_mark_dirty_consumer")
    ai_assist = _run_consumer(event.event_id, "owner_migration_ai_assist_notify_consumer")
    webhook = _run_consumer(event.event_id, "webhook_owner_migration_consumer")
    _jobs, job_total = ExternalEffectService().list_jobs({})

    assert owner_projection["consumer_run"]["status"] == "succeeded"
    assert owner_projection["attempt"]["response_summary_json"]["customer_owner_projection"] == "owner_migration_recorded"
    assert summary["consumer_run"]["status"] == "succeeded"
    assert summary["attempt"]["response_summary_json"]["customer_summary_mark_dirty"] == "owner_migration_recorded"
    assert ai_assist["consumer_run"]["status"] == "skipped"
    assert ai_assist["attempt"]["response_summary_json"]["reason"] == "owner_migration_ai_assist_notify_not_configured"
    assert webhook["consumer_run"]["status"] == "skipped"
    assert webhook["attempt"]["response_summary_json"]["reason"] == "owner_migration_webhook_not_configured"
    assert job_total == 0


def test_owner_migration_legacy_ai_assist_consumer_run_has_dispatch_handler(monkeypatch) -> None:
    _configure(monkeypatch)
    _execute()
    event = _event()
    repo = build_internal_event_repository()
    legacy_run = repo.create_consumer_run(
        event=event,
        consumer_name="ai_assist_notify_consumer",
        consumer_type="orchestration",
    )

    result = InternalEventWorker().dispatch_one_consumer(
        event.event_id,
        "ai_assist_notify_consumer",
        dry_run=False,
        force=False,
        reason="owner_migration_legacy_consumer_compat_test",
    )
    runs, run_total = _runs(event.event_id)

    assert legacy_run.consumer_name == "ai_assist_notify_consumer"
    assert result["consumer_run"]["status"] == "skipped"
    assert result["attempt"]["response_summary_json"]["reason"] == "owner_migration_legacy_ai_assist_notify_not_configured"
    assert run_total == 5
    assert sorted(run.consumer_name for run in runs) == sorted([*OWNER_MIGRATION_CONSUMERS, "ai_assist_notify_consumer"])


def test_owner_migration_pair_allowlist_blocks_worker_but_single_consumer_still_runs(monkeypatch) -> None:
    _configure(
        monkeypatch,
        allowed_event_types="payment.succeeded,owner_migration.executed",
        auto_execute=True,
    )
    _execute()
    event = _event()
    worker = InternalEventWorker()

    preview = worker.preview_due(
        batch_size=1,
        event_types=[OWNER_MIGRATION_EXECUTED_EVENT_TYPE],
        consumer_names=OWNER_MIGRATION_CONSUMERS,
    )
    execute = worker.run_due(
        batch_size=1,
        dry_run=False,
        event_types=[OWNER_MIGRATION_EXECUTED_EVENT_TYPE],
        consumer_names=OWNER_MIGRATION_CONSUMERS,
    )
    manual = worker.dispatch_one_consumer(
        event.event_id,
        "customer_owner_projection_consumer",
        dry_run=False,
        force=False,
        reason="owner_migration_manual_single_consumer_test",
    )
    runs, _ = _runs(event.event_id)

    assert preview["counts"]["candidate_count"] == 0
    assert preview["event_consumers"] == []
    assert execute["counts"]["processed_count"] == 0
    assert execute["event_consumers"] == []
    assert manual["consumer_run"]["status"] == "succeeded"
    assert next(run for run in runs if run.consumer_name == "customer_summary_mark_dirty_consumer").status == "pending"


def test_diagnostics_exposes_owner_migration_flag(monkeypatch) -> None:
    client = _configure(monkeypatch)

    response = client.get("/api/admin/internal-events/diagnostics")

    assert response.status_code == 200
    assert response.json()["owner_migration_internal_events_enabled"] is True
