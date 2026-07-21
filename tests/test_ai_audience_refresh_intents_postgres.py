from __future__ import annotations

import threading
from typing import Any

import pytest
from sqlalchemy import text

from aicrm_next.ai_audience_ops.refresh_intents import (
    AudienceRefreshIntentRepository,
    AudienceRefreshIntentService,
)
from aicrm_next.shared.db_session import get_session_factory


pytestmark = pytest.mark.usefixtures("next_pg_schema")


def _create_package(*, source_type: str = "questionnaire_submission", daily_enabled: bool = False) -> int:
    with get_session_factory()() as session:
        package_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO ai_audience_package (
                        package_key, name, status, incremental_enabled, daily_enabled
                    ) VALUES (
                        'intent_pkg_' || nextval('ai_audience_package_id_seq')::text,
                        'Intent package', 'draft', TRUE, :daily_enabled
                    )
                    RETURNING id
                    """
                ),
                {"daily_enabled": bool(daily_enabled)},
            ).scalar_one()
        )
        version_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO ai_audience_package_version (
                        package_id, version_number, status,
                        incremental_sql_text, snapshot_sql_text
                    ) VALUES (
                        :package_id, 1, 'published',
                        'SELECT 1 AS identity_type',
                        'SELECT 1 AS identity_type'
                    )
                    RETURNING id
                    """
                ),
                {"package_id": package_id},
            ).scalar_one()
        )
        session.execute(
            text(
                """
                UPDATE ai_audience_package
                SET status = 'active', current_version_id = :version_id
                WHERE id = :package_id
                """
            ),
            {"package_id": package_id, "version_id": version_id},
        )
        session.execute(
            text(
                """
                INSERT INTO ai_audience_package_dependency (
                    package_id, version_id, source_type, source_key, view_name
                ) VALUES (
                    :package_id, :version_id, :source_type, '', 'audience_read.test_v1'
                )
                """
            ),
            {"package_id": package_id, "version_id": version_id, "source_type": source_type},
        )
        session.commit()
    return package_id


def _count(statement: str, params: dict[str, Any] | None = None) -> int:
    with get_session_factory()() as session:
        return int(session.execute(text(statement), params or {}).scalar_one())


def test_duplicate_source_event_is_idempotent_and_pending_events_coalesce() -> None:
    package_id = _create_package()
    repo = AudienceRefreshIntentRepository()

    first = repo.mark_source_dirty(
        source_event_key="questionnaire.submitted:101",
        source_type="questionnaire_submission",
        execution_id="exe_source_101",
    )
    duplicate = repo.mark_source_dirty(
        source_event_key="questionnaire.submitted:101",
        source_type="questionnaire_submission",
        execution_id="exe_source_101_duplicate",
    )
    second = repo.mark_source_dirty(
        source_event_key="questionnaire.submitted:102",
        source_type="questionnaire_submission",
        execution_id="exe_source_102",
    )

    assert first["updated_package_count"] == 1
    assert duplicate["updated_package_count"] == 0
    assert duplicate["deduplicated_package_count"] == 1
    assert second["updated_package_count"] == 1
    intent = repo.get(package_id)
    assert intent is not None
    assert intent["dirty_generation"] == 2
    assert intent["signal_generation"] == 1
    assert intent["status"] == "waiting"
    assert _count(
        "SELECT COUNT(*) FROM internal_event_outbox WHERE idempotency_key LIKE 'ai_audience.refresh.requested:%'"
    ) == 1
    assert _count("SELECT COUNT(*) FROM ai_audience_refresh_source_receipt WHERE package_id = :package_id", {"package_id": package_id}) == 2


def test_business_row_dirty_intent_and_signal_share_one_transaction() -> None:
    package_id = _create_package()
    repo = AudienceRefreshIntentRepository()
    with get_session_factory()() as session:
        session.execute(
            text("INSERT INTO app_settings (key, value) VALUES ('audience_tx_probe', 'pending')"),
        )
        result = repo.mark_source_dirty_in_session(
            session,
            source_event_key="transactional-source-1",
            source_type="questionnaire_submission",
            execution_id="exe_transactional_source_1",
        )
        assert result["updated_package_count"] == 1
        session.rollback()

    assert _count("SELECT COUNT(*) FROM app_settings WHERE key = 'audience_tx_probe'") == 0
    assert _count("SELECT COUNT(*) FROM ai_audience_refresh_intent WHERE package_id = :package_id", {"package_id": package_id}) == 0
    assert _count("SELECT COUNT(*) FROM ai_audience_refresh_source_receipt WHERE package_id = :package_id", {"package_id": package_id}) == 0
    assert _count("SELECT COUNT(*) FROM internal_event_outbox WHERE event_type = 'ai_audience.refresh.requested'") == 0


def test_source_receipt_persists_only_opaque_identifiers() -> None:
    package_id = _create_package()
    raw_event_key = "questionnaire.submitted:mobile:13800138000"
    raw_source_key = "external_user:wm_sensitive_identity"

    result = AudienceRefreshIntentRepository().mark_source_dirty(
        source_event_key=raw_event_key,
        source_type="questionnaire_submission",
        source_key=raw_source_key,
    )

    with get_session_factory()() as session:
        receipt = session.execute(
            text(
                """
                SELECT source_event_key, source_key
                FROM ai_audience_refresh_source_receipt
                WHERE package_id = :package_id
                """
            ),
            {"package_id": package_id},
        ).mappings().one()
        last_source_event_key = str(
            session.execute(
                text("SELECT last_source_event_key FROM ai_audience_refresh_intent WHERE package_id = :package_id"),
                {"package_id": package_id},
            ).scalar_one()
        )
    assert result["source_event_key"].startswith("sha256:")
    assert receipt["source_event_key"] == result["source_event_key"]
    assert str(receipt["source_key"]).startswith("sha256:")
    assert last_source_event_key == result["source_event_key"]
    assert "13800138000" not in str(receipt)
    assert "wm_sensitive_identity" not in str(receipt)


def test_one_source_event_fans_out_unique_child_executions_to_multiple_packages() -> None:
    first_package_id = _create_package()
    second_package_id = _create_package()

    result = AudienceRefreshIntentService().request_source_change(
        {"source_type": "questionnaire_submission", "source_key": ""},
        source_event_key="shared-source-event",
        execution_id="exe_shared_source",
    )

    assert result["updated_package_count"] == 2
    assert {item["package_id"] for item in result["items"]} == {first_package_id, second_package_id}
    child_execution_ids = {item["execution_id"] for item in result["items"]}
    assert len(child_execution_ids) == 2
    assert all(item["parent_execution_id"] == "exe_shared_source" for item in result["items"])
    assert _count("SELECT COUNT(*) FROM internal_event_outbox WHERE event_type = 'ai_audience.refresh.requested'") == 2


def test_concurrent_source_events_advance_generation_without_parallel_owner() -> None:
    package_id = _create_package()
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def mark(index: int) -> None:
        try:
            barrier.wait(timeout=5)
            AudienceRefreshIntentRepository().mark_source_dirty(
                source_event_key=f"source:{index}",
                source_type="questionnaire_submission",
                execution_id=f"exe_source_{index}",
            )
        except BaseException as exc:  # pragma: no cover - diagnostic collection
            errors.append(exc)

    threads = [threading.Thread(target=mark, args=(index,)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    intent = AudienceRefreshIntentRepository().get(package_id)
    assert intent is not None
    assert intent["dirty_generation"] == 2
    assert intent["status"] == "waiting"
    assert _count(
        "SELECT COUNT(*) FROM internal_event_outbox WHERE idempotency_key LIKE 'ai_audience.refresh.requested:%'"
    ) == 1


def test_worker_claims_latest_generation_and_old_signal_does_not_repeat() -> None:
    package_id = _create_package()
    repo = AudienceRefreshIntentRepository()
    repo.mark_source_dirty(source_event_key="source:1", source_type="questionnaire_submission")
    repo.mark_source_dirty(source_event_key="source:2", source_type="questionnaire_submission")
    calls: list[dict[str, Any]] = []

    def runner(package_id_arg: int, **kwargs: Any) -> dict[str, Any]:
        calls.append({"package_id": package_id_arg, **kwargs})
        return {"ok": True, "run": {}, "returned_count": 0, "member_event_count": 0}

    service = AudienceRefreshIntentService(repository=repo, refresh_runner=runner)
    result = service.process_requested(package_id=package_id, signal_generation=1)
    stale = service.process_requested(package_id=package_id, signal_generation=1)

    assert result["ok"] is True
    assert result["generation"] == 2
    assert len(calls) == 1
    assert stale["claimed"] is False
    assert stale["reason"] == "already_completed"
    intent = repo.get(package_id)
    assert intent is not None
    assert intent["status"] == "idle"
    assert intent["completed_generation"] == 2
    assert _count(
        "SELECT COUNT(*) FROM internal_event_outbox WHERE idempotency_key = :key",
        {"key": f"ai_audience.refresh.completed:{package_id}:2"},
    ) == 1


def test_dirty_while_running_creates_explicit_next_generation_continuation() -> None:
    package_id = _create_package()
    repo = AudienceRefreshIntentRepository()
    repo.mark_source_dirty(source_event_key="source:1", source_type="questionnaire_submission", execution_id="exe_gen_1")
    claim = repo.claim_latest(package_id=package_id, signal_generation=1)
    assert claim["claimed"] is True

    repo.mark_source_dirty(source_event_key="source:2", source_type="questionnaire_submission", execution_id="exe_gen_2")
    running = repo.get(package_id)
    assert running is not None
    assert running["status"] == "running"
    assert running["dirty_generation"] == 2
    assert _count(
        "SELECT COUNT(*) FROM internal_event_outbox WHERE idempotency_key LIKE 'ai_audience.refresh.requested:%'"
    ) == 1

    completed = repo.complete(
        package_id=package_id,
        generation=1,
        result={"ok": True, "run": {}, "member_event_count": 0},
    )
    assert completed["continuation_created"] is True
    intent = repo.get(package_id)
    assert intent is not None
    assert intent["status"] == "waiting"
    assert intent["completed_generation"] == 1
    assert intent["signal_generation"] == 2
    assert intent["execution_id"] == "exe_gen_2"
    assert _count(
        "SELECT COUNT(*) FROM internal_event_outbox WHERE idempotency_key LIKE 'ai_audience.refresh.requested:%'"
    ) == 2


def test_running_generation_keeps_a_separate_coalesced_target_with_stable_priority_payload() -> None:
    package_id = _create_package()
    repo = AudienceRefreshIntentRepository()
    repo.mark_package_dirty(
        package_id=package_id,
        source_event_key="daily:1",
        source_type="daily_clock_intent",
        refresh_kind="daily",
        execution_id="exe_daily_1",
        params={"daily_generation": 1},
        row_limit=900,
    )
    first_claim = repo.claim_latest(package_id=package_id, signal_generation=1)
    assert first_claim["claimed"] is True
    assert first_claim["running_refresh_kind"] == "daily"

    repo.mark_package_dirty(
        package_id=package_id,
        source_event_key="incremental:2",
        source_type="questionnaire_submission",
        refresh_kind="incremental",
        execution_id="exe_incremental_2",
        params={"questionnaire_id": 2},
        row_limit=120,
    )
    after_first_pending = repo.get(package_id)
    assert after_first_pending is not None
    assert after_first_pending["status"] == "running"
    assert after_first_pending["target_refresh_kind"] == "incremental"
    assert after_first_pending["target_params_json"] == {"questionnaire_id": 2}
    assert after_first_pending["target_row_limit"] == 120

    repo.mark_package_dirty(
        package_id=package_id,
        source_event_key="daily:3",
        source_type="daily_clock_intent",
        refresh_kind="daily",
        execution_id="exe_daily_3",
        params={"daily_generation": 3},
        row_limit=330,
    )
    repo.mark_package_dirty(
        package_id=package_id,
        source_event_key="incremental:4",
        source_type="questionnaire_submission",
        refresh_kind="incremental",
        execution_id="exe_incremental_4",
        params={"questionnaire_id": 4},
        row_limit=440,
    )
    coalesced = repo.get(package_id)
    assert coalesced is not None
    assert coalesced["dirty_generation"] == 4
    assert coalesced["target_refresh_kind"] == "daily"
    assert coalesced["target_params_json"] == {"daily_generation": 3}
    assert coalesced["target_row_limit"] == 330

    completed = repo.complete(
        package_id=package_id,
        generation=1,
        result={"ok": True, "run": {}, "member_event_count": 0},
    )
    assert completed["continuation_created"] is True
    continuation = repo.claim_latest(package_id=package_id, signal_generation=4)
    assert continuation["claimed"] is True
    assert continuation["running_generation"] == 4
    assert continuation["running_refresh_kind"] == "daily"
    assert continuation["running_params_json"] == {"daily_generation": 3}
    assert continuation["running_row_limit"] == 330


def test_replaced_internal_consumer_lease_recovers_running_intent_but_parallel_owner_cannot() -> None:
    package_id = _create_package()
    repo = AudienceRefreshIntentRepository()
    repo.mark_source_dirty(source_event_key="source:lease", source_type="questionnaire_submission")

    first = repo.claim_latest(
        package_id=package_id,
        signal_generation=1,
        owner_consumer_run_id=88,
        owner_lease_token="lease-old",
    )
    assert first["claimed"] is True
    parallel = repo.claim_latest(
        package_id=package_id,
        signal_generation=1,
        owner_consumer_run_id=99,
        owner_lease_token="lease-parallel",
    )
    assert parallel["claimed"] is False
    assert parallel["reason"] == "already_running"

    reclaimed = repo.claim_latest(
        package_id=package_id,
        signal_generation=1,
        owner_consumer_run_id=88,
        owner_lease_token="lease-new",
    )
    assert reclaimed["claimed"] is True
    assert reclaimed["owner_consumer_run_id"] == 88
    assert reclaimed["owner_lease_token"] == "lease-new"
    assert reclaimed["attempt_count"] == 2
    stale = repo.complete(
        package_id=package_id,
        generation=1,
        result={"ok": True, "run": {}},
        owner_consumer_run_id=88,
        owner_lease_token="lease-old",
    )
    assert stale["completed"] is False
    assert stale["reason"] == "stale_completion"
    current = repo.complete(
        package_id=package_id,
        generation=1,
        result={"ok": True, "run": {}},
        owner_consumer_run_id=88,
        owner_lease_token="lease-new",
    )
    assert current["completed"] is True


def test_failure_retries_same_owner_then_completes_without_duplicate_provider_call() -> None:
    package_id = _create_package()
    repo = AudienceRefreshIntentRepository()
    repo.mark_source_dirty(source_event_key="source:retry", source_type="questionnaire_submission")
    attempts = 0

    def runner(package_id_arg: int, **kwargs: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return {"ok": False, "error": "synthetic refresh failure for 13800138000"}
        return {"ok": True, "run": {}, "member_event_count": 0}

    service = AudienceRefreshIntentService(repository=repo, refresh_runner=runner)
    failed = service.process_requested(package_id=package_id, signal_generation=1)
    assert failed["ok"] is False
    assert "13800138000" not in failed["error"]
    assert repo.get(package_id)["status"] == "retry_wait"  # type: ignore[index]
    with get_session_factory()() as session:
        stored_error = str(
            session.execute(
                text("SELECT last_error_message FROM ai_audience_refresh_intent WHERE package_id = :package_id"),
                {"package_id": package_id},
            ).scalar_one()
        )
    assert "13800138000" not in stored_error

    too_early = service.process_requested(package_id=package_id, signal_generation=1)
    assert too_early["claimed"] is False
    assert too_early["reason"] == "not_available"
    assert attempts == 1
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE ai_audience_refresh_intent
                SET available_at = CURRENT_TIMESTAMP - INTERVAL '1 second'
                WHERE package_id = :package_id
                """
            ),
            {"package_id": package_id},
        )
        session.commit()

    succeeded = service.process_requested(package_id=package_id, signal_generation=1)
    assert succeeded["ok"] is True
    assert attempts == 2
    assert repo.get(package_id)["status"] == "idle"  # type: ignore[index]
    assert _count("SELECT COUNT(*) FROM external_effect_attempt") == 0


def test_success_resets_attempt_budget_for_each_future_generation() -> None:
    package_id = _create_package()
    repo = AudienceRefreshIntentRepository()

    for generation in range(1, 13):
        repo.mark_package_dirty(
            package_id=package_id,
            source_event_key=f"source:{generation}",
            source_type="questionnaire_submission",
            execution_id=f"exe_generation_{generation}",
        )
        claim = repo.claim_latest(package_id=package_id, signal_generation=generation)
        assert claim["claimed"] is True
        assert claim["running_generation"] == generation
        completed = repo.complete(
            package_id=package_id,
            generation=generation,
            result={"ok": True, "run": {}, "member_event_count": 0},
        )
        assert completed["completed"] is True
        intent = repo.get(package_id)
        assert intent is not None
        assert intent["status"] == "idle"
        assert intent["attempt_count"] == 0


def test_daily_clock_intent_is_idempotent_and_never_runs_refresh_inline() -> None:
    package_id = _create_package(daily_enabled=True)
    calls: list[dict[str, Any]] = []

    def runner(*args: Any, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover - must not be called
        calls.append({"args": args, "kwargs": kwargs})
        raise AssertionError("daily timer must not run refresh inline")

    service = AudienceRefreshIntentService(refresh_runner=runner)
    first = service.request_due_refreshes("daily", bucket="2026-07-17", actor_id="daily_timer")
    duplicate = service.request_due_refreshes("daily", bucket="2026-07-17", actor_id="daily_timer")

    assert first["intent_count"] == 1
    assert duplicate["intent_count"] == 0
    assert duplicate["deduplicated_count"] == 1
    assert calls == []
    intent = AudienceRefreshIntentRepository().get(package_id)
    assert intent is not None
    assert intent["target_refresh_kind"] == "daily"
    assert _count("SELECT COUNT(*) FROM ai_audience_package_run") == 0
    assert _count("SELECT COUNT(*) FROM external_effect_attempt") == 0


def test_manual_api_only_persists_and_signals(next_client) -> None:
    package_id = _create_package()

    response = next_client.post(
        f"/api/ai/audience/packages/{package_id}/refresh",
        headers={"X-Idempotency-Key": "manual-api-1", "X-AICRM-Execution-Id": "exe_manual_api_1"},
        json={"run_type": "incremental", "params": {"questionnaire_id": 42}, "row_limit": 50},
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["execution_id"] == "exe_manual_api_1"
    assert _count("SELECT COUNT(*) FROM ai_audience_package_run") == 0
    assert _count("SELECT COUNT(*) FROM external_effect_job") == 0
    intent = AudienceRefreshIntentRepository().get(package_id)
    assert intent is not None
    assert intent["status"] == "waiting"
    assert intent["target_params_json"] == {"questionnaire_id": 42}


def test_callback_only_records_and_signals_without_planning_or_provider_attempt(next_client) -> None:
    package_id = _create_package()
    with get_session_factory()() as session:
        session.execute(
            text("UPDATE ai_audience_package SET package_key = 'callback_intent_pkg' WHERE id = :id"),
            {"id": package_id},
        )
        session.commit()

    response = next_client.post(
        "/api/ai/audience/packages/callback_intent_pkg/webhook",
        json={
            "external_event_id": "callback-1",
            "status": "generated",
            "message": {"text": "test only"},
            "action": {
                "type": "send_private_message",
                "target_external_userid": "wm_test_only",
                "sender_userid": "staff_test_only",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["external_effect_job_id"] is None
    assert _count("SELECT COUNT(*) FROM ai_audience_inbound_webhook_event") == 1
    assert _count("SELECT COUNT(*) FROM external_effect_job") == 0
    assert _count("SELECT COUNT(*) FROM external_effect_attempt") == 0
    assert _count(
        "SELECT COUNT(*) FROM internal_event_outbox WHERE event_type = 'ai_audience.inbound.received'"
    ) == 1
