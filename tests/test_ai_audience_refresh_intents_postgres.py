from __future__ import annotations

import threading
from typing import Any

import pytest
from sqlalchemy import text

from aicrm_next.extensions.ai.ai_audience_ops.refresh_intents import (
    AudienceRefreshIntentRepository,
    AudienceRefreshIntentService,
)
from aicrm_next.extensions.ai.ai_audience_ops.repository import build_audience_repository
from aicrm_next.internal_event_composition import build_internal_event_consumer_registry
from aicrm_next.platform.platform_foundation.internal_events.outbox import InternalEventOutboxRelay
from aicrm_next.platform.shared.db_session import get_session_factory


pytestmark = pytest.mark.usefixtures("next_pg_schema")


def _create_package(
    *,
    source_type: str = "questionnaire_submission",
    daily_enabled: bool = False,
    incremental_enabled: bool = True,
    incremental_sql_text: str = "SELECT 1 AS identity_type",
    snapshot_sql_text: str = "SELECT 1 AS identity_type",
    query_mode: str = "hybrid",
    simple_compiled_sql_text: str = "",
) -> int:
    with get_session_factory()() as session:
        package_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO ai_audience_package (
                        package_key, name, status, query_mode,
                        incremental_enabled, daily_enabled
                    ) VALUES (
                        'intent_pkg_' || nextval('ai_audience_package_id_seq')::text,
                        'Intent package', 'draft', :query_mode,
                        :incremental_enabled, :daily_enabled
                    )
                    RETURNING id
                    """
                ),
                {
                    "daily_enabled": bool(daily_enabled),
                    "incremental_enabled": bool(incremental_enabled),
                    "query_mode": query_mode,
                },
            ).scalar_one()
        )
        version_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO ai_audience_package_version (
                        package_id, version_number, status,
                        incremental_sql_text, snapshot_sql_text,
                        simple_compiled_sql_text
                    ) VALUES (
                        :package_id, 1, 'published',
                        :incremental_sql_text,
                        :snapshot_sql_text,
                        :simple_compiled_sql_text
                    )
                    RETURNING id
                    """
                ),
                {
                    "package_id": package_id,
                    "incremental_sql_text": incremental_sql_text,
                    "snapshot_sql_text": snapshot_sql_text,
                    "simple_compiled_sql_text": simple_compiled_sql_text,
                },
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


def _freeze_signal_as_pr3_history(package_id: int, *, generation: int = 1) -> None:
    relayed = InternalEventOutboxRelay(
        consumer_registry=build_internal_event_consumer_registry(),
    ).relay_due(limit=10)
    assert relayed["ok"] is True
    key = f"ai_audience.refresh.requested:{package_id}:{generation}"
    with get_session_factory()() as session:
        updated = session.execute(
            text(
                """
                UPDATE internal_event_consumer_run run
                SET status = 'pending',
                    attempt_count = 0,
                    worker_generation = 0,
                    policy_version = (
                        SELECT policy_version
                        FROM queue_runtime_control
                        WHERE singleton = TRUE
                    ),
                    hold_reason = 'history_frozen_at_pr3_generation_1',
                    hold_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                FROM internal_event_outbox outbox
                WHERE outbox.internal_event_id = run.event_id
                  AND outbox.idempotency_key = :idempotency_key
                """
            ),
            {"idempotency_key": key},
        )
        assert int(updated.rowcount or 0) == 1
        session.commit()


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


def test_duplicate_clock_intent_recovers_frozen_history_without_replaying_old_run() -> None:
    package_id = _create_package(daily_enabled=True)
    repo = AudienceRefreshIntentRepository()
    first = repo.mark_package_dirty(
        package_id=package_id,
        source_event_key="daily:2026-07-24",
        source_type="daily_clock_intent",
        refresh_kind="daily",
    )
    assert first["signal_created"] is True
    _freeze_signal_as_pr3_history(package_id)

    duplicate = repo.mark_package_dirty(
        package_id=package_id,
        source_event_key="daily:2026-07-24",
        source_type="daily_clock_intent",
        refresh_kind="daily",
    )

    assert duplicate["deduplicated"] is True
    assert duplicate["signal_created"] is True
    assert duplicate["history_frozen_signal_recovered"] is True
    intent = repo.get(package_id)
    assert intent is not None
    assert intent["dirty_generation"] == 1
    assert intent["signal_generation"] == 1
    assert intent["status"] == "waiting"
    assert _count(
        "SELECT COUNT(*) FROM internal_event_outbox WHERE idempotency_key LIKE :key",
        {"key": f"ai_audience.refresh.requested:{package_id}:1%"},
    ) == 2
    assert _count(
        "SELECT COUNT(*) FROM internal_event_consumer_run WHERE hold_reason = 'history_frozen_at_pr3_generation_1'"
    ) == 1


def test_new_clock_generation_replaces_frozen_owner_but_preserves_daily_priority() -> None:
    package_id = _create_package(daily_enabled=True)
    repo = AudienceRefreshIntentRepository()
    repo.mark_package_dirty(
        package_id=package_id,
        source_event_key="daily:2026-07-24",
        source_type="daily_clock_intent",
        refresh_kind="daily",
    )
    _freeze_signal_as_pr3_history(package_id)

    recovered = repo.mark_package_dirty(
        package_id=package_id,
        source_event_key="incremental:2026-07-24T09:18:00+08:00",
        source_type="incremental_clock_intent",
        refresh_kind="incremental",
    )

    assert recovered["signal_created"] is True
    assert recovered["history_frozen_signal_recovered"] is True
    intent = repo.get(package_id)
    assert intent is not None
    assert intent["dirty_generation"] == 2
    assert intent["signal_generation"] == 2
    assert intent["target_refresh_kind"] == "daily"
    assert _count(
        "SELECT COUNT(*) FROM internal_event_outbox WHERE idempotency_key LIKE 'ai_audience.refresh.requested:%'"
    ) == 2
    assert _count(
        "SELECT COUNT(*) FROM internal_event_consumer_run WHERE hold_reason = 'history_frozen_at_pr3_generation_1'"
    ) == 1


def test_non_history_hold_remains_fail_closed_and_is_not_resignalled() -> None:
    package_id = _create_package()
    repo = AudienceRefreshIntentRepository()
    repo.mark_package_dirty(
        package_id=package_id,
        source_event_key="incremental:1",
        source_type="incremental_clock_intent",
        refresh_kind="incremental",
    )
    relayed = InternalEventOutboxRelay(
        consumer_registry=build_internal_event_consumer_registry(),
    ).relay_due(limit=10)
    assert relayed["ok"] is True
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE internal_event_consumer_run
                SET hold_reason = 'operator_hold', hold_at = CURRENT_TIMESTAMP
                WHERE consumer_name = 'ai_audience_refresh_intent_consumer'
                """
            )
        )
        session.commit()

    second = repo.mark_package_dirty(
        package_id=package_id,
        source_event_key="incremental:2",
        source_type="incremental_clock_intent",
        refresh_kind="incremental",
    )

    assert second["signal_created"] is False
    assert second["history_frozen_signal_recovered"] is False
    intent = repo.get(package_id)
    assert intent is not None
    assert intent["dirty_generation"] == 2
    assert intent["signal_generation"] == 1
    assert _count(
        "SELECT COUNT(*) FROM internal_event_outbox WHERE idempotency_key LIKE 'ai_audience.refresh.requested:%'"
    ) == 1


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


def test_source_change_skips_daily_only_package_without_incremental_sql() -> None:
    package_id = _create_package(
        daily_enabled=True,
        incremental_enabled=False,
        incremental_sql_text="",
    )

    result = AudienceRefreshIntentRepository().mark_source_dirty(
        source_event_key="daily-only-source-change",
        source_type="questionnaire_submission",
    )

    assert result["matched_package_count"] == 0
    assert result["updated_package_count"] == 0
    assert _count(
        "SELECT COUNT(*) FROM ai_audience_refresh_intent WHERE package_id = :package_id",
        {"package_id": package_id},
    ) == 0


def test_daily_clock_recovers_blocked_wrong_kind_without_replaying_old_signal() -> None:
    package_id = _create_package(
        daily_enabled=True,
        incremental_enabled=False,
        incremental_sql_text="",
    )
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                INSERT INTO ai_audience_refresh_intent (
                    package_id, dirty_generation, completed_generation,
                    signal_generation, status, target_refresh_kind,
                    attempt_count, last_error_code, last_error_message
                ) VALUES (
                    :package_id, 10, 0, 10, 'blocked', 'incremental',
                    10, 'refresh_failed', 'incremental_sql_not_configured'
                )
                """
            ),
            {"package_id": package_id},
        )
        session.commit()

    result = AudienceRefreshIntentService().request_due_refreshes(
        "daily",
        bucket="recover-blocked-wrong-kind",
    )

    recovered = next(item for item in result["items"] if item["package_id"] == package_id)
    assert recovered["blocked_incompatible_config_recovered"] is True
    assert recovered["signal_created"] is True
    intent = AudienceRefreshIntentRepository().get(package_id)
    assert intent is not None
    assert intent["status"] == "waiting"
    assert intent["target_refresh_kind"] == "daily"
    assert intent["attempt_count"] == 0
    assert intent["dirty_generation"] == 11
    assert intent["signal_generation"] == 11


def test_daily_clock_recovers_blocked_legacy_simple_shape() -> None:
    package_id = _create_package(
        daily_enabled=True,
        incremental_enabled=False,
        incremental_sql_text="SELECT 1 AS identity_type",
        snapshot_sql_text="",
        query_mode="simple_sql",
        simple_compiled_sql_text="SELECT 1 AS identity_type",
    )
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                INSERT INTO ai_audience_refresh_intent (
                    package_id, dirty_generation, completed_generation,
                    signal_generation, status, target_refresh_kind,
                    attempt_count, last_error_code, last_error_message
                ) VALUES (
                    :package_id, 3, 0, 3, 'blocked', 'daily',
                    10, 'refresh_failed', 'daily_sql_not_configured'
                )
                """
            ),
            {"package_id": package_id},
        )
        session.commit()

    result = AudienceRefreshIntentService().request_due_refreshes(
        "daily",
        bucket="recover-blocked-legacy-simple-shape",
    )

    recovered = next(item for item in result["items"] if item["package_id"] == package_id)
    assert recovered["blocked_incompatible_config_recovered"] is True
    assert recovered["signal_created"] is True
    intent = AudienceRefreshIntentRepository().get(package_id)
    assert intent is not None
    assert intent["status"] == "waiting"
    assert intent["target_refresh_kind"] == "daily"
    assert intent["attempt_count"] == 0
    assert intent["dirty_generation"] == 4
    assert intent["signal_generation"] == 4


@pytest.mark.parametrize(
    (
        "query_mode",
        "incremental_enabled",
        "incremental_sql_text",
        "snapshot_sql_text",
        "simple_compiled_sql_text",
        "blocked_kind",
        "error_message",
    ),
    [
        (
            "hybrid",
            False,
            "",
            "SELECT 1 AS identity_type",
            "",
            "incremental",
            "incremental_sql_not_configured",
        ),
        (
            "simple_sql",
            False,
            "SELECT 1 AS identity_type",
            "",
            "SELECT 1 AS identity_type",
            "daily",
            "daily_sql_not_configured",
        ),
    ],
)
def test_duplicate_daily_clock_receipt_recovers_strict_compatibility_block(
    query_mode: str,
    incremental_enabled: bool,
    incremental_sql_text: str,
    snapshot_sql_text: str,
    simple_compiled_sql_text: str,
    blocked_kind: str,
    error_message: str,
) -> None:
    package_id = _create_package(
        daily_enabled=True,
        incremental_enabled=incremental_enabled,
        incremental_sql_text=incremental_sql_text,
        snapshot_sql_text=snapshot_sql_text,
        query_mode=query_mode,
        simple_compiled_sql_text=simple_compiled_sql_text,
    )
    service = AudienceRefreshIntentService()
    first = service.request_due_refreshes("daily", bucket="2026-07-25")
    first_item = next(item for item in first["items"] if item["package_id"] == package_id)
    assert first_item["signal_created"] is True

    with get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE ai_audience_refresh_intent
                SET dirty_generation = 2,
                    completed_generation = 0,
                    signal_generation = 1,
                    status = 'blocked',
                    target_refresh_kind = :blocked_kind,
                    attempt_count = 10,
                    last_error_code = 'refresh_failed',
                    last_error_message = :error_message
                WHERE package_id = :package_id
                """
            ),
            {
                "package_id": package_id,
                "blocked_kind": blocked_kind,
                "error_message": error_message,
            },
        )
        session.commit()

    duplicate = service.request_due_refreshes("daily", bucket="2026-07-25")
    recovered = next(item for item in duplicate["items"] if item["package_id"] == package_id)

    assert recovered["deduplicated"] is True
    assert recovered["blocked_incompatible_config_recovered"] is True
    assert recovered["history_frozen_signal_recovered"] is False
    assert recovered["signal_created"] is True
    intent = AudienceRefreshIntentRepository().get(package_id)
    assert intent is not None
    assert intent["status"] == "waiting"
    assert intent["target_refresh_kind"] == "daily"
    assert intent["attempt_count"] == 0
    assert intent["dirty_generation"] == 2
    assert intent["signal_generation"] == 2
    assert _count(
        "SELECT COUNT(*) FROM internal_event_outbox WHERE idempotency_key LIKE :key",
        {"key": f"ai_audience.refresh.requested:{package_id}:%"},
    ) == 2
    assert _count("SELECT COUNT(*) FROM internal_event_consumer_run") == 0


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


def test_daily_clock_only_marks_packages_whose_scheduled_refresh_is_due() -> None:
    due_package_id = _create_package(daily_enabled=True, incremental_enabled=False)
    future_package_id = _create_package(daily_enabled=True, incremental_enabled=False)
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE ai_audience_package
                SET next_daily_refresh_at = CASE
                    WHEN id = :due_package_id THEN CURRENT_TIMESTAMP - INTERVAL '1 minute'
                    ELSE CURRENT_TIMESTAMP + INTERVAL '1 hour'
                END,
                    last_daily_refreshed_at = NULL
                WHERE id IN (:due_package_id, :future_package_id)
                """
            ),
            {
                "due_package_id": due_package_id,
                "future_package_id": future_package_id,
            },
        )
        session.commit()

    result = AudienceRefreshIntentService().request_due_refreshes(
        "daily",
        bucket="2026-07-25",
        actor_id="daily_timer",
    )

    assert result["candidate_count"] == 1
    assert [int(item["package_id"]) for item in result["items"]] == [due_package_id]
    assert AudienceRefreshIntentRepository().get(due_package_id) is not None
    assert AudienceRefreshIntentRepository().get(future_package_id) is None


def test_daily_due_check_honors_a_future_schedule_even_without_a_watermark() -> None:
    package_id = _create_package(daily_enabled=True, incremental_enabled=False)
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE ai_audience_package
                SET next_daily_refresh_at = CURRENT_TIMESTAMP + INTERVAL '1 hour',
                    last_daily_refreshed_at = NULL
                WHERE id = :package_id
                """
            ),
            {"package_id": package_id},
        )
        session.commit()

    assert build_audience_repository().has_refresh_due("daily") is False


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
