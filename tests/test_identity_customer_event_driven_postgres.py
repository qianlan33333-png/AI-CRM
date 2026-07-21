from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.channel_entry import application as channel_application
from aicrm_next.channel_entry import identity_external_effect
from aicrm_next.channel_entry import repo as channel_repo
from aicrm_next.channel_entry.application import process_wecom_external_contact_event
from aicrm_next.channel_entry.schemas import ProcessWeComExternalContactEventCommand
from aicrm_next.customer_read_model.refresh_intents import (
    CUSTOMER_REFRESH_COMPLETED_CONSUMER,
    CUSTOMER_REFRESH_COMPLETED_EVENT,
    CUSTOMER_REFRESH_REQUESTED_EVENT,
    CustomerReadModelRefreshIntentRepository,
    CustomerReadModelRefreshIntentService,
)
from aicrm_next.external_effect_composition import (
    IDENTITY_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
    IDENTITY_EXTERNAL_EFFECT_SETTLEMENT_CONSUMER,
    build_external_effect_continuation_consumers,
)
from aicrm_next import internal_event_composition
from aicrm_next.internal_event_composition import build_internal_event_consumer_registry
from aicrm_next.platform_foundation.external_effects.adapters import WeComExternalContactDetailAdapter
from aicrm_next.platform_foundation.external_effects.completion_events import EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE
from aicrm_next.platform_foundation.external_effects.settlement_events import EXTERNAL_EFFECT_SETTLED_EVENT_TYPE
from aicrm_next.platform_foundation.external_effects.repo import SQLAlchemyExternalEffectRepository
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform_foundation.internal_events import InternalEventService
from aicrm_next.platform_foundation.internal_events.outbox import InternalEventOutboxRelay
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker


pytestmark = pytest.mark.usefixtures("next_pg_schema")


def _database_url() -> str:
    return str(os.environ.get("DATABASE_URL") or os.environ.get("AICRM_TEST_DATABASE_URL") or "")


def _connect(*, autocommit: bool = True):
    return psycopg.connect(_database_url(), autocommit=autocommit, row_factory=dict_row)


def _identity_plan(
    external_userid: str = "wm_event_driven_001",
    *,
    event_log_id: int = 8101,
) -> dict:
    return channel_repo.enqueue_channel_entry_identity_resolution(
        corp_id="ww-event-driven",
        external_userid=external_userid,
        follow_user_userid="owner-event-driven",
        payload_json={
            "Event": "change_external_contact",
            "ChangeType": "add_external_contact",
            "ExternalUserID": external_userid,
            "UserID": "owner-event-driven",
            "corp_id": "ww-event-driven",
        },
        reason="postgres_event_driven_test",
        event_log_id=event_log_id,
    )


def test_callback_persists_one_identity_effect_without_inline_provider(monkeypatch) -> None:
    provider_calls: list[str] = []
    status_updates: list[tuple[int, str]] = []
    monkeypatch.setattr(
        channel_application.repo,
        "log_external_contact_event",
        lambda **kwargs: {"id": 8101, **kwargs},
    )
    monkeypatch.setattr(
        channel_application.repo,
        "mark_event_status",
        lambda event_id, status, error_message="": status_updates.append((event_id, status)),
    )
    monkeypatch.setattr(
        channel_application,
        "sync_external_contact_identity_for_event",
        lambda *args, **kwargs: provider_calls.append("provider") or {},
    )
    command = ProcessWeComExternalContactEventCommand(
        corp_id="ww-event-driven",
        event_data={
            "Event": "change_external_contact",
            "ChangeType": "add_external_contact",
            "ExternalUserID": "wm_callback_event_driven",
            "UserID": "owner-event-driven",
            "CreateTime": "1781800001",
        },
        payload_xml="<xml/>",
        route="/wecom/external-contact/callback",
    )

    first = process_wecom_external_contact_event(command)
    second = process_wecom_external_contact_event(command)

    with _connect() as connection:
        queue_rows = connection.execute(
            """
            SELECT id, status, lane, execution_id, external_effect_job_id
            FROM crm_user_identity_resolution_queue
            WHERE source_type = 'channel_entry'
              AND external_userid = 'wm_callback_event_driven'
            """
        ).fetchall()
        effect_rows = connection.execute(
            """
            SELECT id, effect_type, lane, status, execution_id, parent_execution_id,
                   payload_summary_json
            FROM external_effect_job
            WHERE effect_type = 'wecom.external_contact.detail.fetch'
              AND target_id = 'wm_callback_event_driven'
            """
        ).fetchall()

    assert first["identity_sync"]["status"] == "queued"
    assert second["identity_sync"]["external_effect_job_id"] == first["identity_sync"]["external_effect_job_id"]
    assert provider_calls == []
    assert len(queue_rows) == len(effect_rows) == 1
    assert queue_rows[0]["status"] == "pending"
    assert queue_rows[0]["lane"] == effect_rows[0]["lane"] == "wecom_interactive"
    assert queue_rows[0]["external_effect_job_id"] == effect_rows[0]["id"]
    assert effect_rows[0]["parent_execution_id"] == queue_rows[0]["execution_id"]
    assert effect_rows[0]["status"] == "queued"
    assert effect_rows[0]["payload_summary_json"]["external_userid_present"] is True
    assert "wm_callback_event_driven" not in json.dumps(effect_rows[0]["payload_summary_json"])
    assert status_updates == [(8101, "success"), (8101, "success")]


def test_identity_detail_is_idempotent_per_event_and_new_after_readd() -> None:
    external_userid = "wm_identity_readd_001"
    first = _identity_plan(external_userid, event_log_id=8101)
    with _connect() as connection:
        connection.execute(
            """
            UPDATE crm_user_identity_resolution_queue
            SET status = 'resolved', updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (int(first["queue_id"]),),
        )

    duplicate = _identity_plan(external_userid, event_log_id=8101)
    readded = _identity_plan(external_userid, event_log_id=8102)

    with _connect() as connection:
        queue_rows = connection.execute(
            """
            SELECT id, source_key, external_effect_job_id,
                   payload_json ->> 'event_log_id' AS event_log_id
            FROM crm_user_identity_resolution_queue
            WHERE source_type = 'channel_entry'
              AND external_userid = %s
            ORDER BY id ASC
            """,
            (external_userid,),
        ).fetchall()
        effect_rows = connection.execute(
            """
            SELECT id, source_event_id, idempotency_key
            FROM external_effect_job
            WHERE effect_type = 'wecom.external_contact.detail.fetch'
              AND target_id = %s
            ORDER BY id ASC
            """,
            (external_userid,),
        ).fetchall()

    assert duplicate["queue_id"] == first["queue_id"]
    assert duplicate["external_effect_job_id"] == first["external_effect_job_id"]
    assert readded["queue_id"] != first["queue_id"]
    assert readded["external_effect_job_id"] != first["external_effect_job_id"]
    assert [row["event_log_id"] for row in queue_rows] == ["8101", "8102"]
    assert [row["source_event_id"] for row in effect_rows] == ["8101", "8102"]
    assert len({row["source_key"] for row in queue_rows}) == 2
    assert len({row["idempotency_key"] for row in effect_rows}) == 2


def test_terminal_identity_effect_durably_holds_parent_queue() -> None:
    planned = _identity_plan("wm_identity_terminal_001")
    repository = SQLAlchemyExternalEffectRepository()
    job = repository.get_job(int(planned["external_effect_job_id"]))
    assert job is not None
    attempt = repository.record_attempt(
        job=job,
        status="blocked",
        adapter_mode="disabled",
        request_summary={"policy_checked": True},
        response_summary={"blocked": True},
        error_code="identity_provider_disabled",
        error_message="provider disabled",
    )
    blocked = repository.mark_blocked(
        job.id,
        attempt_id=attempt.attempt_id,
        error_code="identity_provider_disabled",
        error_message="provider disabled",
    )
    assert blocked is not None and blocked.status == "blocked"

    registry = build_internal_event_consumer_registry()
    relayed = InternalEventOutboxRelay(consumer_registry=registry).relay_due(limit=10)
    assert relayed["counts"]["relayed_count"] == 1
    events, event_count = InternalEventService().list_events(
        {"event_type": EXTERNAL_EFFECT_SETTLED_EVENT_TYPE}
    )
    assert event_count == 1
    runs, run_count = InternalEventService().list_consumer_runs({"event_id": events[0].event_id})
    assert any(
        run.consumer_name == IDENTITY_EXTERNAL_EFFECT_SETTLEMENT_CONSUMER
        for run in runs
    )
    assert run_count == 5
    worker = InternalEventWorker(consumer_registry=registry)
    for run in runs:
        result = worker.dispatch_one(run)
        assert result["consumer_run"]["status"] == "succeeded"

    with _connect() as connection:
        queue = connection.execute(
            """
            SELECT status, hold_reason, next_attempt_at
            FROM crm_user_identity_resolution_queue
            WHERE id = %s
            """,
            (int(planned["queue_id"]),),
        ).fetchone()
    assert queue == {
        "status": "held",
        "hold_reason": "identity_provider_disabled",
        "next_attempt_at": None,
    }


class _Provider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_external_contact_detail(self, external_userid: str) -> dict:
        self.calls.append(external_userid)
        return {
            "errcode": 0,
            "errmsg": "ok",
            "external_contact": {
                "external_userid": external_userid,
                "unionid": "union_private_result_001",
                "openid": "openid_private_result_001",
                "name": "敏感客户姓名",
                "type": 1,
            },
            "follow_user": [
                {
                    "userid": "owner-event-driven",
                    "remark": "敏感备注",
                    "description": "敏感描述",
                    "createtime": 1781800002,
                }
            ],
        }


class _FailingIdentityBridge:
    def apply_external_contact_detail(self, **kwargs):
        raise RuntimeError("local identity projection unavailable")


class _SuccessfulIdentityBridge:
    def __init__(self) -> None:
        self.calls = 0

    def apply_external_contact_detail(self, **kwargs):
        self.calls += 1
        assert kwargs["detail_payload"]["external_contact"]["name"] == "敏感客户姓名"
        return {
            "status": "success",
            "unionid": "union_private_result_001",
            "identity_map_id": 991,
            "openid_present": True,
            "provider_result_applied": True,
            "real_external_call_executed": False,
        }


def test_provider_result_is_private_and_continuation_failure_cannot_pollute_provider_success(monkeypatch) -> None:
    planned = _identity_plan("wm_private_result_001")
    job_id = int(planned["external_effect_job_id"])
    repository = SQLAlchemyExternalEffectRepository()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE external_effect_job
            SET status = 'dispatching', lease_token = 'lease-private-result',
                lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '5 minutes',
                heartbeat_at = CURRENT_TIMESTAMP, locked_by = 'pytest',
                locked_at = CURRENT_TIMESTAMP, worker_generation = 9
            WHERE id = %s
            """,
            (job_id,),
        )

    leased = repository.get_job(job_id)
    assert leased is not None
    provider = _Provider()
    adapter = WeComExternalContactDetailAdapter(adapter_factory=lambda: provider)
    dispatch_result = adapter.dispatch(leased)
    public_dispatch = json.dumps(
        {
            "request": dispatch_result.request_summary,
            "response": dispatch_result.response_summary,
        },
        ensure_ascii=False,
    )
    for secret in (
        "wm_private_result_001",
        "union_private_result_001",
        "openid_private_result_001",
        "敏感客户姓名",
        "敏感备注",
        "敏感描述",
    ):
        assert secret not in public_dispatch
    assert dispatch_result.response_summary == {
        "errcode": 0,
        "provider_detail_present": True,
        "follow_user_count": 1,
        "real_external_call_executed": True,
        "provider_result_received": True,
    }

    begun = repository.begin_provider_attempt(job=leased, request_summary=dispatch_result.request_summary)
    assert begun is not None
    dispatching_job, _ = begun
    completed = repository.complete_dispatch(job=dispatching_job, result=dispatch_result)
    assert completed is not None
    completed_job, public_attempt = completed
    assert completed_job.status == public_attempt.status == "succeeded"
    assert provider.calls == ["wm_private_result_001"]

    public_attempt_json = json.dumps(public_attempt.to_dict(), ensure_ascii=False)
    for secret in (
        "union_private_result_001",
        "openid_private_result_001",
        "敏感客户姓名",
        "敏感备注",
        "敏感描述",
    ):
        assert secret not in public_attempt_json
    with _connect() as connection:
        private_attempt = connection.execute(
            """
            SELECT status, provider_result_json, provider_result_hash,
                   request_summary_json, response_summary_json
            FROM external_effect_attempt
            WHERE attempt_id = %s
            """,
            (public_attempt.attempt_id,),
        ).fetchone()
    assert private_attempt["provider_result_json"]["external_contact"]["name"] == "敏感客户姓名"
    assert len(private_attempt["provider_result_hash"]) == 64
    assert "敏感客户姓名" not in json.dumps(private_attempt["request_summary_json"], ensure_ascii=False)
    assert "敏感客户姓名" not in json.dumps(private_attempt["response_summary_json"], ensure_ascii=False)

    raw_result_reads: list[tuple[str, int | None]] = []

    class _CompletionRepository:
        def get_job(self, current_job_id: int):
            return repository.get_job(current_job_id)

        def get_attempt(self, attempt_id: str):
            return repository.get_attempt(attempt_id)

        def get_attempt_provider_result(self, attempt_id: str, *, job_id: int | None = None):
            raw_result_reads.append((attempt_id, job_id))
            return repository.get_attempt_provider_result(attempt_id, job_id=job_id)

    monkeypatch.setattr(
        internal_event_composition,
        "build_external_effect_repository",
        _CompletionRepository,
    )
    internal_registry = build_internal_event_consumer_registry()
    relayed = InternalEventOutboxRelay(consumer_registry=internal_registry).relay_due(limit=20)
    assert relayed["counts"]["relayed_count"] == 2
    completion_events, completion_event_count = InternalEventService().list_events(
        {"event_type": EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE}
    )
    assert completion_event_count == 1
    completion_runs, completion_run_count = InternalEventService().list_consumer_runs(
        {"event_id": completion_events[0].event_id}
    )
    assert completion_run_count == len(build_external_effect_continuation_consumers())
    identity_run = next(
        run
        for run in completion_runs
        if run.consumer_name == IDENTITY_EXTERNAL_EFFECT_CONTINUATION_CONSUMER
    )
    sibling_runs = [run for run in completion_runs if run.id != identity_run.id]
    internal_worker = InternalEventWorker(consumer_registry=internal_registry)
    sibling_results = [internal_worker.dispatch_one(run) for run in sibling_runs]
    assert {result["consumer_run"]["status"] for result in sibling_results} == {"succeeded"}
    assert raw_result_reads == []

    monkeypatch.setattr(identity_external_effect, "build_identity_bridge_service", lambda: _FailingIdentityBridge())
    failed = internal_worker.dispatch_one(identity_run)
    assert failed["consumer_run"]["status"] == "failed_retryable"
    assert raw_result_reads == [(public_attempt.attempt_id, job_id)]
    with _connect() as connection:
        failure_state = connection.execute(
            """
            SELECT job.status AS job_status, attempt.status AS attempt_status,
                   attempt.provider_result_json,
                   (SELECT COUNT(*) FROM identity_resolution_completion_receipt) AS receipt_count
            FROM external_effect_job job
            JOIN external_effect_attempt attempt ON attempt.job_id = job.id
            WHERE job.id = %s
            """,
            (job_id,),
        ).fetchone()
        private_consumer_attempts = connection.execute(
            """
            SELECT request_summary_json, response_summary_json, error_code, error_message
            FROM internal_event_consumer_attempt
            WHERE consumer_run_id = %s
            ORDER BY id
            """,
            (identity_run.id,),
        ).fetchall()
    assert failure_state["job_status"] == failure_state["attempt_status"] == "succeeded"
    assert failure_state["provider_result_json"]["external_contact"]["unionid"] == "union_private_result_001"
    assert failure_state["receipt_count"] == 0
    private_consumer_attempt_json = json.dumps(private_consumer_attempts, ensure_ascii=False, default=str)
    for secret in (
        "union_private_result_001",
        "openid_private_result_001",
        "敏感客户姓名",
        "敏感备注",
        "敏感描述",
    ):
        assert secret not in private_consumer_attempt_json

    successful_bridge = _SuccessfulIdentityBridge()
    monkeypatch.setattr(identity_external_effect, "build_identity_bridge_service", lambda: successful_bridge)
    monkeypatch.setattr(channel_application, "_record_identity_sync_result", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(
        channel_application,
        "_canonicalize_channel_entry_after_identity",
        lambda *args, **kwargs: {"status": "success"},
    )
    monkeypatch.setattr(identity_external_effect, "_emit_identity_resolved", lambda **kwargs: {"ok": True})

    manual_retry = InternalEventService().retry_consumer_run(
        completion_events[0].event_id,
        IDENTITY_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
        actor_id="pytest-operator",
        actor_type="test",
        reason="approved identity continuation retry",
    )
    assert manual_retry is not None
    assert manual_retry[0].status == "pending"
    assert manual_retry[1].status == "manual_retry"
    retried = internal_worker.dispatch_one(manual_retry[0])
    assert retried["consumer_run"]["status"] == "succeeded", retried
    assert raw_result_reads == [
        (public_attempt.attempt_id, job_id),
        (public_attempt.attempt_id, job_id),
    ]
    assert successful_bridge.calls == 1

    with _connect() as connection:
        final_state = connection.execute(
            """
            SELECT job.status AS job_status, queue.status AS queue_status,
                   attempt.provider_result_json, attempt.provider_result_consumed_at,
                   (SELECT COUNT(*) FROM identity_resolution_completion_receipt) AS receipt_count
            FROM external_effect_job job
            JOIN external_effect_attempt attempt ON attempt.job_id = job.id
            JOIN crm_user_identity_resolution_queue queue ON queue.external_effect_job_id = job.id
            WHERE job.id = %s
            """,
            (job_id,),
        ).fetchone()
    assert final_state["job_status"] == "succeeded"
    assert final_state["queue_status"] == "resolved"
    assert final_state["provider_result_json"] == {}
    assert final_state["provider_result_consumed_at"] is not None
    assert final_state["receipt_count"] == 1
    assert repository.get_attempt_provider_result(public_attempt.attempt_id) == {}
    final_runs, _ = InternalEventService().list_consumer_runs({"event_id": completion_events[0].event_id})
    assert {run.status for run in final_runs} == {"succeeded"}
    final_attempts = InternalEventService().list_attempts(event_id=completion_events[0].event_id)
    assert len(final_attempts) == len(final_runs) + 2
    identity_attempts = [
        attempt
        for attempt in final_attempts
        if attempt.consumer_name == IDENTITY_EXTERNAL_EFFECT_CONTINUATION_CONSUMER
    ]
    assert {attempt.status for attempt in identity_attempts} == {
        "failed_retryable",
        "manual_retry",
        "succeeded",
    }


def test_customer_refresh_coalesces_sources_and_preserves_dirty_during_run() -> None:
    repository = CustomerReadModelRefreshIntentRepository()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda key: repository.mark_dirty(
                    source_event_key=key,
                    source_event_type="identity.resolved",
                    parent_execution_id=f"exe_source_{key[-1]}",
                ),
                ("evt-customer-source-1", "evt-customer-source-2"),
            )
        )

    assert sorted(result["generation"] for result in results) == [1, 2]
    assert sum(bool(result["signal_created"]) for result in results) == 1
    with _connect() as connection:
        initial = connection.execute(
            "SELECT * FROM customer_read_model_refresh_intent WHERE singleton_id = 1"
        ).fetchone()
        initial_signals = connection.execute(
            "SELECT COUNT(*) AS count FROM internal_event_outbox WHERE event_type = %s",
            (CUSTOMER_REFRESH_REQUESTED_EVENT,),
        ).fetchone()["count"]
    assert initial["dirty_generation"] == 2
    assert initial["signal_generation"] in {1, 2}
    assert initial["status"] == "waiting"
    assert initial_signals == 1

    claimed = repository.claim_latest(
        signal_generation=int(initial["signal_generation"]),
        owner_consumer_run_id=9201,
        owner_lease_token="lease-customer-1",
    )
    assert claimed["claimed"] is True
    assert claimed["running_generation"] == 2
    dirty_while_running = repository.mark_dirty(
        source_event_key="evt-customer-source-3",
        source_event_type="questionnaire.submitted",
        parent_execution_id="exe_source_3",
    )
    assert dirty_while_running["generation"] == 3
    assert dirty_while_running["signal_created"] is False

    stale = repository.complete(
        generation=2,
        result={"source_count": 10, "target_count_after": 10, "duration_ms": 11},
        owner_consumer_run_id=9201,
        owner_lease_token="stale-lease",
    )
    assert stale == {"ok": True, "completed": False, "reason": "stale_completion"}
    completed = repository.complete(
        generation=2,
        result={"source_count": 10, "target_count_after": 10, "duration_ms": 11},
        owner_consumer_run_id=9201,
        owner_lease_token="lease-customer-1",
    )
    assert completed["completed"] is True
    assert completed["continuation_created"] is True

    with _connect() as connection:
        final = connection.execute(
            "SELECT * FROM customer_read_model_refresh_intent WHERE singleton_id = 1"
        ).fetchone()
        signal_count = connection.execute(
            "SELECT COUNT(*) AS count FROM internal_event_outbox WHERE event_type = %s",
            (CUSTOMER_REFRESH_REQUESTED_EVENT,),
        ).fetchone()["count"]
    assert final["dirty_generation"] == 3
    assert final["completed_generation"] == 2
    assert final["signal_generation"] == 3
    assert final["status"] == "waiting"
    assert signal_count == 2


def test_customer_refresh_replaces_a_stale_policy_signal_owner_without_inline_work() -> None:
    repository = CustomerReadModelRefreshIntentRepository()
    first = repository.mark_dirty(
        source_event_key="evt-customer-stale-policy-1",
        source_event_type="identity.resolved",
    )
    assert first["signal_created"] is True
    with _connect() as connection:
        connection.execute(
            """
            UPDATE internal_event_outbox
            SET policy_version = 'stale-policy'
            WHERE idempotency_key = %s
            """,
            (f"customer_read_model.refresh.requested:{first['generation']}",),
        )

    recovered = repository.mark_dirty(
        source_event_key="evt-customer-stale-policy-2",
        source_event_type="questionnaire.submitted",
    )

    assert recovered["signal_created"] is True
    assert recovered["missing_owner_recovered"] is True
    assert recovered["superseded_owner"] == {
        "outbox_count": 1,
        "consumer_run_count": 0,
    }
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT status, hold_reason, policy_version
            FROM internal_event_outbox
            WHERE event_type = %s
            ORDER BY id
            """,
            (CUSTOMER_REFRESH_REQUESTED_EVENT,),
        ).fetchall()
        current_policy = connection.execute(
            "SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE"
        ).fetchone()["policy_version"]
    assert len(rows) == 2
    assert rows[0]["status"] == "failed_terminal"
    assert rows[0]["hold_reason"] == "superseded_missing_signal_owner"
    assert rows[1]["status"] == "pending"
    assert rows[1]["policy_version"] == current_policy


def test_customer_refresh_requires_explicit_versioned_release_recovery_after_terminal_failure() -> None:
    repository = CustomerReadModelRefreshIntentRepository()
    first = repository.mark_dirty(
        source_event_key="evt-customer-blocked-1",
        source_event_type="identity.resolved",
    )
    first_signal_key = f"customer_read_model.refresh.requested:{first['generation']}"
    with _connect() as connection:
        connection.execute(
            """
            UPDATE customer_read_model_refresh_intent
            SET status = 'blocked', attempt_count = max_attempts,
                last_error_code = 'customer_read_model_refresh_exception',
                last_error_message = 'local projection failed',
                row_version = row_version + 1
            WHERE singleton_id = 1
            """
        )
        connection.execute(
            """
            UPDATE internal_event_outbox
            SET status = 'failed_terminal',
                hold_reason = 'terminal_local_projection_failure'
            WHERE idempotency_key = %s
            """,
            (first_signal_key,),
        )

    ordinary = repository.mark_dirty(
        source_event_key="evt-customer-blocked-2",
        source_event_type="questionnaire.submitted",
    )
    assert ordinary["signal_created"] is False
    assert ordinary["intent"]["status"] == "blocked"
    assert ordinary["blocked_recovered"] is False

    with pytest.raises(RuntimeError, match="customer_read_model_refresh_recovery_version_mismatch"):
        repository.mark_dirty(
            source_event_key="deploy-runtime-stale-version",
            source_event_type="customer_read_model.release_refresh",
            recover_blocked=True,
            expected_row_version=int(ordinary["intent"]["row_version"]) - 1,
        )

    recovered = repository.mark_dirty(
        source_event_key="deploy-runtime-current-version",
        source_event_type="customer_read_model.release_refresh",
        recover_blocked=True,
        expected_row_version=int(ordinary["intent"]["row_version"]),
    )
    assert recovered["signal_created"] is True
    assert recovered["blocked_recovered"] is True
    assert recovered["intent"]["status"] == "waiting"
    assert recovered["intent"]["attempt_count"] == 0

    with _connect() as connection:
        signals = connection.execute(
            """
            SELECT idempotency_key, status
            FROM internal_event_outbox
            WHERE event_type = %s
            ORDER BY id
            """,
            (CUSTOMER_REFRESH_REQUESTED_EVENT,),
        ).fetchall()
    assert signals == [
        {"idempotency_key": first_signal_key, "status": "failed_terminal"},
        {
            "idempotency_key": f"customer_read_model.refresh.requested:{recovered['generation']}",
            "status": "pending",
        },
    ]


def test_customer_refresh_keeps_final_attempt_with_a_live_lease_as_the_single_owner() -> None:
    repository = CustomerReadModelRefreshIntentRepository()
    first = repository.mark_dirty(
        source_event_key="evt-customer-final-attempt-1",
        source_event_type="identity.resolved",
    )
    signal_key = f"customer_read_model.refresh.requested:{first['generation']}"
    with _connect() as connection:
        connection.execute(
            """
            UPDATE internal_event_outbox
            SET status = 'running', attempt_count = max_attempts,
                lease_token = 'lease-final-outbox', locked_by = 'pytest-final-owner',
                lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '30 seconds'
            WHERE idempotency_key = %s
            """,
            (signal_key,),
        )

    while_outbox_running = repository.mark_dirty(
        source_event_key="evt-customer-final-attempt-2",
        source_event_type="questionnaire.submitted",
    )
    assert while_outbox_running["signal_created"] is False
    assert while_outbox_running["missing_owner_recovered"] is False

    with _connect() as connection:
        connection.execute(
            """
            UPDATE internal_event_outbox
            SET status = 'pending', attempt_count = 0, lease_token = '', locked_by = '',
                lease_expires_at = NULL
            WHERE idempotency_key = %s
            """,
            (signal_key,),
        )
    internal_registry = build_internal_event_consumer_registry()
    relayed = InternalEventOutboxRelay(consumer_registry=internal_registry).relay_due(limit=1)
    assert relayed["counts"]["relayed_count"] == 1
    with _connect() as connection:
        running = connection.execute(
            """
            UPDATE internal_event_consumer_run run
            SET status = 'running', attempt_count = max_attempts,
                lease_token = 'lease-final-consumer', locked_by = 'pytest-final-owner',
                lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '30 seconds'
            FROM internal_event event
            WHERE event.event_id = run.event_id
              AND event.idempotency_key = %s
              AND run.consumer_name = 'customer_read_model_refresh_intent_consumer'
            RETURNING run.id
            """,
            (signal_key,),
        ).fetchone()
    assert running is not None

    while_consumer_running = repository.mark_dirty(
        source_event_key="evt-customer-final-attempt-3",
        source_event_type="payment.succeeded",
    )
    assert while_consumer_running["signal_created"] is False
    assert while_consumer_running["missing_owner_recovered"] is False
    with _connect() as connection:
        signal_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM internal_event_outbox
            WHERE event_type = %s
            """,
            (CUSTOMER_REFRESH_REQUESTED_EVENT,),
        ).fetchone()["count"]
    assert signal_count == 1


def test_customer_refresh_request_is_intent_only_until_internal_consumer() -> None:
    refresh_calls: list[bool] = []
    service = CustomerReadModelRefreshIntentService(
        refresh_runner=lambda *, dry_run: refresh_calls.append(dry_run)
        or {
            "ok": True,
            "source_count": 4,
            "target_count_before": 3,
            "target_count_after": 4,
            "duration_ms": 5,
        }
    )

    requested = service.request_refresh(
        source_event_key="evt-intent-only",
        source_event_type="payment.succeeded",
        parent_execution_id="exe_payment_parent",
    )
    assert requested["signal_created"] is True
    assert refresh_calls == []

    processed = service.process_requested(
        signal_generation=int(requested["generation"]),
        owner_consumer_run_id=9301,
        owner_lease_token="lease-customer-consumer",
    )
    assert processed["ok"] is True
    assert processed["completion"]["completed"] is True
    assert refresh_calls == [False]

    internal_registry = build_internal_event_consumer_registry()
    relayed = InternalEventOutboxRelay(consumer_registry=internal_registry).relay_due(limit=10)
    assert relayed["counts"]["failed_retryable_count"] == 0
    assert relayed["counts"]["failed_terminal_count"] == 0
    completion_events, completion_event_count = InternalEventService().list_events(
        {"event_type": CUSTOMER_REFRESH_COMPLETED_EVENT}
    )
    assert completion_event_count == 1
    completion_runs, completion_run_count = InternalEventService().list_consumer_runs(
        {"event_id": completion_events[0].event_id}
    )
    assert completion_run_count == 1
    assert completion_runs[0].consumer_name == CUSTOMER_REFRESH_COMPLETED_CONSUMER
    receipt = InternalEventWorker(consumer_registry=internal_registry).dispatch_one(completion_runs[0])
    assert receipt["consumer_run"]["status"] == "succeeded"
