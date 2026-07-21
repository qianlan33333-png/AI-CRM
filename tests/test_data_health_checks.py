from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture()
def client(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from aicrm_next.main import create_app

    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_data_health_summary_exposes_registered_checks(client) -> None:
    response = client.get("/api/admin/data-health/summary")

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    body = response.json()
    assert body["ok"] is True
    assert body["overall_status"] == "ok"
    check_ids = {item["check_id"] for item in body["checks"]}
    assert {
        "identity_legacy_column_guard",
        "table_lifecycle_manifest_guard",
        "retired_table_runtime_reference_guard",
        "schema_drift_guard",
        "unionid_orphan_fact_guard",
        "identity_resolution_queue_backlog",
        "projection_freshness_customer_read_model",
        "broadcast_job_blocked_backlog",
        "external_effect_failed_retryable_backlog",
        "deprecated_execution_settings_present",
        "fake_stub_route_exposed",
        "external_effect_approved_not_queued",
        "questionnaire_submission_without_user_guard",
        "payment_order_without_user_guard",
        "customer_360_freshness_guard",
    } <= check_ids
    assert body["counts"]["fail"] == 0
    assert body["counts"]["ok"] >= 3
    assert body["counts"]["not_applicable"] >= 1


def test_customer_360_freshness_guard_registers_phase4_probes(client) -> None:
    response = client.get("/api/admin/data-health/checks/customer_360_freshness_guard")
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    check = payload["check"]
    assert check["status"] == "not_applicable"
    assert check["evidence"]["freshness_probes"] == [
        "latest_identity_update",
        "latest_order",
        "latest_questionnaire",
        "latest_message",
        "latest_projection_refresh",
    ]
    assert set(check["evidence"]["source_tables"]) >= {
        "crm_user_identity",
        "questionnaire_submissions",
        "archived_messages",
        "customer_detail_snapshot_next",
    }


def test_data_health_checks_do_not_expose_raw_identity_values(client) -> None:
    response = client.get("/api/admin/data-health/checks")

    assert response.status_code == 200
    text = response.text
    for forbidden in ("external_userid_value", "openid_value", "mobile_normalized", "raw_payload_json"):
        assert forbidden not in text


def test_data_health_check_detail_and_missing_check(client) -> None:
    detail = client.get("/api/admin/data-health/checks/table_lifecycle_manifest_guard")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["ok"] is True
    assert payload["check"]["check_id"] == "table_lifecycle_manifest_guard"
    assert payload["check"]["status"] == "ok"

    missing = client.get("/api/admin/data-health/checks/not_a_check")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "data_health_check_not_found"


def test_schema_drift_guard_reports_manifest_and_live_schema_mismatches() -> None:
    from aicrm_next.data_health.schema_drift import evaluate_schema_drift

    manifest = {
        "tables": {
            "declared_missing": {
                "domain": "test",
                "lifecycle": "canonical",
                "write_owner": "tests",
                "drop_candidate": False,
            },
            "retired_still_exists": {
                "domain": "test",
                "lifecycle": "retired",
                "replacement": "declared_missing",
                "drop_candidate": False,
            },
            "canonical_without_owner": {
                "domain": "test",
                "lifecycle": "canonical",
                "write_owner": "",
                "drop_candidate": False,
            },
            "pii_without_level": {
                "domain": "test",
                "lifecycle": "canonical",
                "write_owner": "tests",
                "drop_candidate": False,
            },
            "queue_without_status_enum": {
                "domain": "test",
                "lifecycle": "queue",
                "write_owner": "tests",
                "drop_candidate": False,
            },
            "queue_with_status_enum": {
                "domain": "test",
                "lifecycle": "queue",
                "write_owner": "tests",
                "status_enum": {"column": "status"},
                "drop_candidate": False,
            },
        }
    }
    actual_schema = {
        "retired_still_exists": {"id"},
        "canonical_without_owner": {"id"},
        "pii_without_level": {"id", "mobile"},
        "queue_without_status_enum": {"id", "status"},
        "queue_with_status_enum": {"id", "status"},
        "unregistered_live_table": {"id"},
    }

    violations = evaluate_schema_drift(manifest=manifest, actual_schema=actual_schema)
    joined = "\n".join(violations)

    assert "declared_missing: manifest declares physical lifecycle=canonical but table is missing" in joined
    assert "retired_still_exists: retired table still exists in public schema" in joined
    assert "canonical_without_owner: canonical table must declare write_owner" in joined
    assert "pii_without_level: table has PII-like columns but missing pii_level" in joined
    assert "queue_without_status_enum: queue table has status/state column but missing status_enum" in joined
    assert "unregistered_live_table: table exists but is not registered in lifecycle manifest" in joined
    assert "queue_with_status_enum" not in joined


def test_migrated_schema_matches_lifecycle_manifest(next_pg_schema) -> None:
    del next_pg_schema
    from aicrm_next.data_health.schema_drift import (
        evaluate_schema_drift,
        load_table_lifecycle_manifest,
        public_schema_snapshot,
    )

    violations = evaluate_schema_drift(
        manifest=load_table_lifecycle_manifest(),
        actual_schema=public_schema_snapshot(),
    )

    assert violations == []


class _FakeResult:
    def __init__(self, row: dict):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row

    def one(self):
        return self._row


class _FakeSession:
    def __init__(self, row: dict, calls: list[str]):
        self._row = row
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement):
        self._calls.append(str(statement))
        return _FakeResult(self._row)


def _patch_health_db(monkeypatch, row: dict) -> list[str]:
    from aicrm_next.data_health import checks

    calls: list[str] = []
    monkeypatch.setattr(checks, "database_schema_available", lambda: True)
    monkeypatch.setattr(checks, "get_session_factory", lambda: lambda: _FakeSession(row, calls))
    return calls


def test_projection_freshness_probe_uses_live_projection_counts(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    calls = _patch_health_db(
        monkeypatch,
        {
            "list_count": 12,
            "detail_count": 0,
            "list_stale_minutes": 5,
            "detail_stale_minutes": 90,
        },
    )

    result = checks._projection_freshness_customer_read_model()

    assert result.status == "fail"
    assert result.evidence["list_count"] == 12
    assert result.evidence["detail_count"] == 0
    assert any("customer_list_index_next" in sql and "customer_detail_snapshot_next" in sql for sql in calls)
    assert "external_userid_value" not in str(result.evidence)


def test_projection_freshness_probe_accepts_managed_fresh_parity(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    _patch_health_db(
        monkeypatch,
        {
            "list_count": 12,
            "detail_count": 12,
            "refresh_state_present": True,
            "refresh_source_count": 12,
            "refresh_target_count": 12,
            "timeline_event_count": 48,
            "timeline_duplicate_event_id_count": 0,
            "refresh_age_minutes": 5,
        },
    )

    result = checks._projection_freshness_customer_read_model()

    assert result.status == "ok"
    assert result.evidence["refresh_state_present"] is True
    assert result.evidence["timeline_event_count"] == 48


def test_projection_freshness_probe_rejects_duplicate_timeline_event_ids(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    _patch_health_db(
        monkeypatch,
        {
            "list_count": 12,
            "detail_count": 12,
            "refresh_state_present": True,
            "refresh_source_count": 12,
            "refresh_target_count": 12,
            "timeline_event_count": 49,
            "timeline_duplicate_event_id_count": 1,
            "refresh_age_minutes": 5,
        },
    )

    result = checks._projection_freshness_customer_read_model()

    assert result.status == "fail"
    assert result.evidence["timeline_duplicate_event_id_count"] == 1
    assert "timeline_duplicate_event_id_count=1" in result.evidence["violations"]


def test_projection_freshness_probe_does_not_fail_on_wall_clock_age_without_source_drift(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    _patch_health_db(
        monkeypatch,
        {
            "list_count": 12,
            "detail_count": 12,
            "refresh_state_present": True,
            "refresh_source_count": 12,
            "refresh_target_count": 12,
            "refresh_age_minutes": 600,
        },
    )

    result = checks._projection_freshness_customer_read_model()

    assert result.status == "ok"
    assert result.evidence["refresh_age_minutes"] == 600
    assert result.evidence["freshness_policy"] == "source_change_lag"
    assert result.evidence["wall_clock_age_is_diagnostic"] is True
    assert "max_stale_minutes" not in result.evidence


def test_customer_360_freshness_guard_still_blocks_real_source_lag(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    _patch_health_db(
        monkeypatch,
        {
            "refresh_state_present": True,
            "refresh_age_minutes": 600,
            "identity_lag_minutes": checks.PROJECTION_FRESHNESS_MAX_MINUTES + 1,
            "order_lag_minutes": 0,
            "questionnaire_lag_minutes": 0,
            "message_lag_minutes": 0,
        },
    )

    result = checks._customer_360_freshness_guard()

    assert result.status == "fail"
    assert result.evidence["identity_lag_minutes"] == checks.PROJECTION_FRESHNESS_MAX_MINUTES + 1
    assert result.evidence["refresh_age_minutes"] == 600
    assert result.evidence["violations"] == [
        f"identity_lag_minutes={checks.PROJECTION_FRESHNESS_MAX_MINUTES + 1:.1f} exceeds {checks.PROJECTION_FRESHNESS_MAX_MINUTES}"
    ]


def test_broadcast_backlog_probe_counts_blocked_and_retryable(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    calls = _patch_health_db(
        monkeypatch,
        {
            "raw_open_count": 4,
            "held_count": 1,
            "eligible_count": 999,
            "dlq_count": 1,
            "unknown_count": 0,
            "blocked_count": 1,
            "failed_terminal_count": 0,
            "due_retryable_count": 2,
            "oldest_terminal_hours": 3.5,
        },
    )

    result = checks._broadcast_job_blocked_backlog()

    assert result.status == "fail"
    assert result.evidence["execution_owner"] == "legacy_frozen"
    assert result.evidence["execution_semantics"] == "readonly"
    assert result.evidence["raw_open_count"] == 4
    assert result.evidence["held_count"] == 1
    assert result.evidence["eligible_count"] == 0
    assert result.evidence["dlq_count"] == 1
    assert result.evidence["blocked_count"] == 1
    assert result.evidence["due_retryable_count"] == 2
    assert any("FROM broadcast_jobs" in sql for sql in calls)


def test_broadcast_backlog_probe_keeps_historical_terminal_evidence_without_permanent_failure(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    _patch_health_db(
        monkeypatch,
        {
            "raw_open_count": 14,
            "held_count": 14,
            "eligible_count": 7,
            "dlq_count": 14,
            "unknown_count": 0,
            "recent_blocked_count": 0,
            "recent_failed_terminal_count": 0,
            "historical_blocked_count": 8,
            "historical_failed_terminal_count": 6,
            "due_retryable_count": 0,
            "oldest_terminal_hours": 72,
        },
    )

    result = checks._broadcast_job_blocked_backlog()

    assert result.status == "ok"
    assert result.evidence["raw_open_count"] == 14
    assert result.evidence["held_count"] == 14
    assert result.evidence["eligible_count"] == 0
    assert result.evidence["dlq_count"] == 14
    assert result.evidence["blocked_count"] == 0
    assert result.evidence["historical_blocked_count"] == 8
    assert result.evidence["historical_failed_terminal_count"] == 6


def test_previously_placeholder_probes_are_live_and_green_with_zero_actionable_counts(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    _patch_health_db(
        monkeypatch,
        {
            "questionnaire_orphan_count": 0,
            "wechat_pay_orphan_count": 0,
            "alipay_pay_orphan_count": 0,
            "wechat_shop_orphan_count": 0,
            "broadcast_orphan_count": 0,
            "approved_not_runnable_count": 0,
            "approved_job_count": 4,
            "missing_unionid_count": 0,
            "guarded_missing_unionid_count": 0,
            "unguarded_missing_unionid_count": 0,
            "missing_continuation_guard_count": 0,
            "identity_dependent_effect_without_unionid_count": 0,
            "missing_identity_count": 0,
            "historical_pre_cutover_count": 48,
            "wechat_pay_missing_user_count": 0,
            "alipay_pay_missing_user_count": 0,
            "wechat_shop_missing_user_count": 0,
            "refresh_state_present": True,
            "refresh_age_minutes": 5,
            "identity_lag_minutes": 2,
            "order_lag_minutes": -10,
            "questionnaire_lag_minutes": 1,
            "message_lag_minutes": 3,
        },
    )

    results = [
        checks._unionid_orphan_fact_guard(),
        checks._external_effect_approved_not_queued(),
        checks._questionnaire_submission_without_user_guard(),
        checks._payment_order_without_user_guard(),
        checks._customer_360_freshness_guard(),
    ]

    assert [result.status for result in results] == ["ok"] * 5
    assert all(result.status != "not_applicable" for result in results)


def test_questionnaire_submission_guard_accepts_quarantined_missing_unionids(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    _patch_health_db(
        monkeypatch,
        {
            "missing_unionid_count": 3,
            "guarded_missing_unionid_count": 3,
            "unguarded_missing_unionid_count": 0,
            "missing_continuation_guard_count": 0,
            "identity_dependent_effect_without_unionid_count": 0,
            "missing_identity_count": 0,
            "historical_pre_cutover_count": 48,
        },
    )

    result = checks._questionnaire_submission_without_user_guard()

    assert result.status == "ok"
    assert result.evidence["missing_unionid_count"] == 3
    assert result.evidence["guarded_missing_unionid_count"] == 3
    assert result.evidence["unguarded_missing_unionid_count"] == 0


def test_questionnaire_submission_guard_rejects_unguarded_missing_unionid(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    _patch_health_db(
        monkeypatch,
        {
            "missing_unionid_count": 2,
            "guarded_missing_unionid_count": 1,
            "unguarded_missing_unionid_count": 1,
            "missing_continuation_guard_count": 1,
            "identity_dependent_effect_without_unionid_count": 0,
            "missing_identity_count": 0,
            "historical_pre_cutover_count": 48,
        },
    )

    result = checks._questionnaire_submission_without_user_guard()

    assert result.status == "fail"
    assert result.evidence["unguarded_missing_unionid_count"] == 1
    assert result.evidence["missing_continuation_guard_count"] == 1


def test_external_effect_backlog_probe_accepts_small_retryable_queue(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    calls = _patch_health_db(
        monkeypatch,
        {
            "failed_retryable_count": 2,
            "failed_terminal_count": 0,
            "blocked_count": 0,
            "due_retryable_count": 2,
            "oldest_failed_retryable_age_seconds": 120,
        },
    )

    result = checks._external_effect_failed_retryable_backlog()

    assert result.status == "ok"
    assert result.evidence["failed_retryable_count"] == 2
    assert result.evidence["oldest_failed_retryable_age_seconds"] == 120
    assert any("FROM external_effect_job" in sql for sql in calls)


def test_external_effect_backlog_keeps_historical_terminal_evidence_without_permanent_failure(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    _patch_health_db(
        monkeypatch,
        {
            "failed_retryable_count": 0,
            "recent_failed_terminal_count": 0,
            "recent_blocked_count": 0,
            "historical_failed_terminal_count": 7,
            "historical_blocked_count": 3,
            "due_retryable_count": 0,
            "oldest_failed_retryable_age_seconds": 0,
        },
    )

    result = checks._external_effect_failed_retryable_backlog()

    assert result.status == "ok"
    assert result.evidence["failed_terminal_count"] == 0
    assert result.evidence["blocked_count"] == 0
    assert result.evidence["historical_failed_terminal_count"] == 7
    assert result.evidence["historical_blocked_count"] == 3
    assert result.evidence["terminal_lookback_hours"] == 24


def test_external_effect_backlog_separates_only_strict_id_validation_canary_failures(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    calls = _patch_health_db(
        monkeypatch,
        {
            "failed_retryable_count": 0,
            "recent_failed_terminal_count": 0,
            "recent_blocked_count": 0,
            "historical_failed_terminal_count": 0,
            "historical_blocked_count": 0,
            "due_retryable_count": 0,
            "oldest_failed_retryable_age_seconds": 0,
            "canary_failed_retryable_count": 0,
            "canary_failed_terminal_count": 1,
            "canary_blocked_count": 1,
            "callback_welcome_failed_terminal_count": 1,
        },
    )

    result = checks._external_effect_failed_retryable_backlog()

    assert result.status == "ok"
    assert result.evidence["id_validation_canary"] == {
        "failed_retryable_count": 0,
        "failed_terminal_count": 1,
        "blocked_count": 1,
        "callback_welcome_failed_terminal_count": 1,
        "excluded_from_business_health": True,
        "strict_provenance_required": True,
    }
    query = "\n".join(calls)
    for required_provenance in (
        "business_type",
        "business_id",
        "source_module",
        "source_route",
        "trace_id",
        "request_id",
        "idempotency_key",
        "fairness_key",
        "actor_id",
        "actor_type",
        "risk_level",
        "execution_mode",
        "max_attempts",
    ):
        assert required_provenance in query

    for callback_proof in (
        "queue_runtime_validation_evidence",
        "wecom.welcome_message.send",
        "wecom_error_41050",
        "source_webhook_inbox_id",
        "callback_to_provider_boundary_ms",
        "provider_attempt_count",
        "provider_policy_gate_passed",
        "target_values_redacted",
    ):
        assert callback_proof in query


def test_external_effect_backlog_still_fails_for_ordinary_terminal_effect(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    _patch_health_db(
        monkeypatch,
        {
            "failed_retryable_count": 0,
            "recent_failed_terminal_count": 1,
            "recent_blocked_count": 0,
            "historical_failed_terminal_count": 1,
            "historical_blocked_count": 0,
            "due_retryable_count": 0,
            "oldest_failed_retryable_age_seconds": 0,
            "canary_failed_retryable_count": 0,
            "canary_failed_terminal_count": 0,
            "canary_blocked_count": 0,
        },
    )

    result = checks._external_effect_failed_retryable_backlog()

    assert result.status == "fail"
    assert result.evidence["failed_terminal_count"] == 1


@pytest.mark.postgres
def test_mirrored_welcome_validation_failure_is_excluded_only_with_append_only_proof(
    next_pg_schema,
) -> None:
    import psycopg

    from aicrm_next.data_health import checks

    database_url = os.environ["DATABASE_URL"]
    execution_id = "exe_data_health_mirrored_welcome"
    policy_version = "queue-v2-allowlisted-data-health-test"
    evidence = {
        "job_status": "failed_terminal",
        "job_error_code": "wecom_error_41050",
        "execution_scope": "allowlisted_canary",
        "attempt_count": 1,
        "provider_attempt_count": 1,
        "provider_attempt_status": "failed_terminal",
        "provider_attempt_error_code": "wecom_error_41050",
        "provider_adapter_mode": "execute",
        "provider_error_classification": "terminal",
        "provider_errcode": 41050,
        "callback_duplicate_count": 0,
        "source_webhook_inbox_id": 3810,
        "callback_to_provider_boundary_ms": 1948,
        "provider_boundary_started": True,
        "provider_result_received": True,
        "side_effect_executed": True,
        "duplicate_provider_call_proof": True,
        "worker_generation_matches": True,
        "evidence_type_matches": True,
        "job_policy_version_matches": True,
        "policy_proof_valid": True,
        "provider_policy_gate_passed": True,
        "test_receipt_proof_valid": True,
        "provider_blocked": False,
        "target_values_redacted": True,
        "error_messages_redacted": True,
    }
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO external_effect_job (
                    effect_type, adapter_name, operation, target_type, target_id,
                    business_type, business_id, source_module, source_route,
                    trace_id, request_id, idempotency_key, fairness_key,
                    actor_id, actor_type, risk_level, execution_mode,
                    execution_id, payload_json, status, attempt_count, max_attempts,
                    last_error_code, side_effect_executed, provider_result_received,
                    worker_generation, policy_version
                ) VALUES (
                    'wecom.welcome_message.send', 'wecom_welcome_message', 'send_welcome',
                    'external_user', 'redacted', 'channel_entry', 'redacted',
                    'aicrm_next.channel_entry', '/wecom/external-contact/callback',
                    'redacted', 'redacted', 'redacted', 'channel_entry',
                    'system', 'system', 'high', 'execute',
                    %s, '{"execution_scope":"allowlisted_canary"}'::jsonb,
                    'failed_terminal', 1, 3, 'wecom_error_41050', TRUE, TRUE, 1, %s
                )
                RETURNING id
                """,
                (execution_id, policy_version),
            )
            job_id = int(cursor.fetchone()[0])
        connection.commit()

    without_proof = checks._external_effect_failed_retryable_backlog()

    assert without_proof.status == "fail"
    assert without_proof.evidence["failed_terminal_count"] == 1

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO queue_runtime_validation_evidence (
                    evidence_id, evidence_type, release_sha, active_generation,
                    policy_version, execution_id, job_id, status, evidence_json,
                    actor, reason
                ) VALUES (
                    'qrve_data_health_mirrored_welcome', 'wecom_welcome', %s, 1,
                    %s, %s, %s, 'failed', %s::jsonb,
                    'github:data-health-test', 'guarded mirrored callback validation'
                )
                """,
                ("0" * 40, policy_version, execution_id, job_id, json.dumps(evidence)),
            )
        connection.commit()

    result = checks._external_effect_failed_retryable_backlog()

    assert result.status == "ok"
    assert result.evidence["failed_terminal_count"] == 0
    assert result.evidence["id_validation_canary"]["failed_terminal_count"] == 1
    assert result.evidence["id_validation_canary"]["callback_welcome_failed_terminal_count"] == 1


def test_wecom_media_health_separates_exclusive_canary_failure_but_keeps_evidence(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    calls = _patch_health_db(
        monkeypatch,
        {
            "total_count": 1,
            "ready_count": 0,
            "refresh_due_count": 0,
            "refreshing_count": 0,
            "failed_count": 0,
            "invalid_source_count": 0,
            "canary_failed_count": 0,
            "canary_invalid_source_count": 1,
            "expired_count": 0,
            "source_gap_count": 0,
        },
    )

    result = checks._wecom_media_lease_health()

    assert result.status == "ok"
    assert result.evidence["canary_invalid_source_count"] == 1
    query = "\n".join(calls)
    assert "id_validation_canary_referenced" in query
    assert "ordinary_job_referenced" in query
    assert "AND NOT (" in query


def test_wecom_media_health_keeps_ordinary_invalid_source_actionable(monkeypatch) -> None:
    from aicrm_next.data_health import checks

    _patch_health_db(
        monkeypatch,
        {
            "total_count": 1,
            "ready_count": 0,
            "refresh_due_count": 0,
            "refreshing_count": 0,
            "failed_count": 0,
            "invalid_source_count": 1,
            "canary_failed_count": 0,
            "canary_invalid_source_count": 0,
            "expired_count": 0,
            "source_gap_count": 0,
        },
    )

    result = checks._wecom_media_lease_health()

    assert result.status == "warn"
    assert result.evidence["invalid_source_count"] == 1


@pytest.mark.postgres
def test_id_validation_canary_health_isolation_executes_against_postgres(next_pg_schema) -> None:
    import psycopg

    from aicrm_next.data_health import checks

    database_url = os.environ["DATABASE_URL"]
    material_id = 2_147_483_647
    target_id = f"image:{material_id}:image"
    test_prefix = f"data-health-isolation-{material_id}"

    def plan(*, scenario: str, status: str, strict_canary: bool, target_type: str, target_id: str) -> None:
        source_module = "scripts.ops.plan_wecom_canary" if strict_canary else "tests.data_health"
        source_route = "scripts/ops/plan_wecom_canary.py" if strict_canary else "tests/test_data_health_checks.py"
        prefix = "id-validation-canary" if strict_canary else "ordinary"
        business_id = f"{test_prefix}-{scenario}"
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO external_effect_job (
                        effect_type, adapter_name, operation, target_type, target_id,
                        business_type, business_id, source_module, source_route,
                        trace_id, request_id, idempotency_key, fairness_key,
                        actor_id, actor_type, risk_level, execution_mode,
                        status, max_attempts
                    ) VALUES (
                        %s, 'test', 'test', %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, 'execute',
                        %s, %s
                    )
                    """,
                    (
                        "wecom.media.upload" if target_type == "media_library_material" else "wecom.profile.update",
                        target_type,
                        target_id,
                        "id_validation_canary" if strict_canary else "ordinary",
                        business_id,
                        source_module,
                        source_route,
                        f"{prefix}:postgres:{business_id}",
                        business_id,
                        f"{prefix}:postgres:{business_id}",
                        "id_validation_canary" if strict_canary else "ordinary",
                        "github:codex-test" if strict_canary else "test",
                        "operator" if strict_canary else "system",
                        "high" if strict_canary else "medium",
                        status,
                        1 if strict_canary else 5,
                    ),
                )
            connection.commit()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM wecom_media_leases WHERE tenant_id = 'aicrm' AND material_kind = 'image' AND material_id = %s AND upload_kind = 'image'",
                (material_id,),
            )
            cursor.execute(
                "INSERT INTO wecom_media_leases (tenant_id, material_kind, material_id, upload_kind, status) VALUES ('aicrm', 'image', %s, 'image', 'invalid_source')",
                (material_id,),
            )
        connection.commit()

    try:
        plan(
            scenario="media",
            status="failed_terminal",
            strict_canary=True,
            target_type="media_library_material",
            target_id=target_id,
        )
        plan(
            scenario="profile",
            status="blocked",
            strict_canary=True,
            target_type="external_user",
            target_id="redacted",
        )

        external = checks._external_effect_failed_retryable_backlog()
        media = checks._wecom_media_lease_health()
        assert external.status == "ok"
        assert external.evidence["id_validation_canary"]["failed_terminal_count"] == 1
        assert external.evidence["id_validation_canary"]["blocked_count"] == 1
        assert media.status == "ok"
        assert media.evidence["canary_invalid_source_count"] == 1

        plan(
            scenario="ordinary-terminal",
            status="failed_terminal",
            strict_canary=False,
            target_type="external_user",
            target_id="ordinary",
        )
        plan(
            scenario="ordinary-media-reference",
            status="queued",
            strict_canary=False,
            target_type="media_library_material",
            target_id=target_id,
        )

        external = checks._external_effect_failed_retryable_backlog()
        media = checks._wecom_media_lease_health()
        assert external.status == "fail"
        assert external.evidence["failed_terminal_count"] == 1
        assert media.status == "warn"
        assert media.evidence["invalid_source_count"] == 1
    finally:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM wecom_media_leases WHERE tenant_id = 'aicrm' AND material_kind = 'image' AND material_id = %s AND upload_kind = 'image'",
                    (material_id,),
                )
                cursor.execute(
                    "DELETE FROM external_effect_job WHERE business_id LIKE %s",
                    (f"{test_prefix}%",),
                )
            connection.commit()


def test_retired_runtime_reference_scan_reads_each_source_once(tmp_path, monkeypatch) -> None:
    from tools import check_data_table_lifecycle as lifecycle

    runtime_root = tmp_path / "aicrm_next"
    runtime_root.mkdir()
    first = runtime_root / "first.py"
    second = runtime_root / "second.py"
    first.write_text("SELECT * FROM RETIRED_1\n", encoding="utf-8")
    second.write_text("SELECT 1\n", encoding="utf-8")
    tables = {f"retired_{index}": {"lifecycle": "retired"} for index in range(1, 51)}

    original_read_text = Path.read_text
    read_counts: dict[Path, int] = {}

    def tracked_read_text(path: Path, *args, **kwargs) -> str:
        if path.parent == runtime_root:
            read_counts[path] = read_counts.get(path, 0) + 1
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked_read_text)

    violations = lifecycle._retired_runtime_reference_violations(tmp_path, tables)

    assert violations == ["aicrm_next/first.py references retired table retired_1"]
    assert read_counts == {first: 1, second: 1}
