from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.platform.platform_foundation.internal_events import legacy_path_markers
from aicrm_next.platform.platform_foundation.internal_events.legacy_path_markers import (
    legacy_path_marker_diagnostics,
    mark_legacy_path_invoked,
    reset_legacy_path_marker_state,
)


def _mark(**overrides):
    payload = {
        "legacy_path": "questionnaire.legacy_webhook_external_push",
        "replacement_event_type": "questionnaire.submitted",
        "replacement_consumer": "questionnaire_webhook_consumer",
        "source_module": "pytest",
        "source_route": "/pytest/legacy-marker",
        "aggregate_id": "wm_raw_external_userid_13800001234_openid-secret",
        "reason": "pytest_marker",
    }
    payload.update(overrides)
    return mark_legacy_path_invoked(**payload)


def test_legacy_path_marker_flag_off_does_not_record(monkeypatch) -> None:
    reset_legacy_path_marker_state()
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_LEGACY_PATH_MARKERS_ENABLED", "0")

    result = _mark()
    diagnostics = legacy_path_marker_diagnostics()

    assert result["recorded"] is False
    assert result["reason"] == "legacy_path_markers_disabled"
    assert diagnostics["legacy_path_invocation_count"] == 0
    assert diagnostics["legacy_paths"] == []


def test_legacy_path_marker_flag_on_records_structured_marker_and_redacts(monkeypatch) -> None:
    reset_legacy_path_marker_state()
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_LEGACY_PATH_MARKERS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_LEGACY_PATH_RETIRE_AFTER_DAYS", "7")
    log_extras: list[dict] = []

    def fake_info(message: str, *args, **kwargs) -> None:
        if message == "legacy_internal_event_path_invoked":
            log_extras.append(dict(kwargs.get("extra") or {}))

    monkeypatch.setattr(legacy_path_markers.LOGGER, "info", fake_info)

    result = _mark(source_route="https://hooks.example.com/raw-token-secret", reason="contains token secret")
    diagnostics = legacy_path_marker_diagnostics()
    log_record = log_extras[0]

    assert result["recorded"] is True
    assert result["event"] == "legacy_internal_event_path_invoked"
    assert result["legacy_path"] == "questionnaire.legacy_webhook_external_push"
    assert result["replacement_event_type"] == "questionnaire.submitted"
    assert result["replacement_consumer"] == "questionnaire_webhook_consumer"
    assert result["retire_after"] == "7d_after_no_hits"
    assert "13800001234" not in str(result)
    assert "openid-secret" not in str(result)
    assert "hooks.example.com" not in str(result)
    assert "token secret" not in str(result)
    assert log_record["event"] == "legacy_internal_event_path_invoked"
    assert log_record["aggregate_id"].startswith("aggregate_id_ref:")
    assert diagnostics["legacy_path_invocation_count"] == 1
    assert diagnostics["legacy_paths"][0]["legacy_path"] == "questionnaire.legacy_webhook_external_push"
    assert diagnostics["legacy_paths"][0]["last_aggregate_id_redacted"].startswith("aggregate_id_ref:")
    assert "13800001234" not in str(diagnostics)
    assert diagnostics["real_external_call_executed"] is False


def test_legacy_path_marker_does_not_change_legacy_return(monkeypatch) -> None:
    reset_legacy_path_marker_state()
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_LEGACY_PATH_MARKERS_ENABLED", "1")

    def legacy_path() -> dict[str, object]:
        marker = _mark(
            legacy_path="payment.legacy_direct_automation",
            replacement_event_type="payment.succeeded",
            replacement_consumer="ai_audience_source_poke_consumer",
        )
        assert marker["recorded"] is True
        return {"ok": True, "status": "legacy_result_unchanged"}

    assert legacy_path() == {"ok": True, "status": "legacy_result_unchanged"}


def test_internal_event_diagnostics_include_legacy_path_markers(monkeypatch, next_client: TestClient) -> None:
    reset_legacy_path_marker_state()
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_LEGACY_PATH_MARKERS_ENABLED", "1")
    _mark(legacy_path="owner_migration.legacy_webhook_notify", replacement_event_type="owner_migration.executed", replacement_consumer="webhook_owner_migration_consumer")

    response = next_client.get("/api/admin/internal-events/diagnostics")
    body = response.json()

    assert response.status_code == 200
    assert body["legacy_path_markers_enabled"] is True
    assert body["legacy_path_invocation_count"] == 1
    assert body["legacy_path_last_seen"]
    assert body["legacy_paths"][0]["legacy_path"] == "owner_migration.legacy_webhook_notify"
    assert body["legacy_paths"][0]["replacement_event_type"] == "owner_migration.executed"
    assert body["legacy_paths"][0]["replacement_consumer"] == "webhook_owner_migration_consumer"
    assert body["real_external_call_executed"] is False
