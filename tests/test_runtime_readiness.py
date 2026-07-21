from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.platform_foundation import api as platform_api
from aicrm_next.platform_foundation.readiness import runtime_readiness_payload


WECOM_OK = {
    "enabled": True,
    "execution_mode": "execute",
    "execution_mode_source": "AICRM_WECOM_EXECUTION_MODE",
    "conflict": False,
    "blocking_reasons": [],
    "contact_secret": "must_not_be_returned",
}
FULL_SHA = "a" * 40


class _Result:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _Connection:
    def __init__(self, *, current_revision: str = "0104_auth_platform", queue_metrics: dict | None = None) -> None:
        self.current_revision = current_revision
        self.queue_metrics = queue_metrics or {
            "webhook_pending_count": 0,
            "webhook_oldest_pending_age_seconds": 0,
            "webhook_dead_letter_count": 0,
            "internal_event_pending_count": 0,
            "internal_event_oldest_pending_age_seconds": 0,
            "internal_event_terminal_count": 0,
            "external_effect_pending_count": 0,
            "external_effect_oldest_pending_age_seconds": 0,
            "external_effect_terminal_count": 0,
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql: str, params: dict | None = None):
        normalized = " ".join(sql.split())
        if normalized == "SELECT 1 AS ok":
            return _Result([{"ok": 1}])
        if normalized.startswith("SELECT version_num FROM alembic_version"):
            return _Result([{"version_num": self.current_revision}])
        return _Result([self.queue_metrics])


def _factory(connection: _Connection):
    return lambda _url: connection


def test_fixture_readiness_is_explicit_without_claiming_postgres() -> None:
    payload = runtime_readiness_payload(
        database_url="",
        wecom_diagnostics=WECOM_OK,
        release_sha="dev",
        production=False,
    )

    assert payload["ok"] is True
    assert payload["http_status"] == 200
    assert payload["components"]["database"]["status"] == "fixture"
    assert payload["components"]["migration"]["matches_head"] is False
    assert payload["components"]["release"]["status"] == "warning"


def test_postgres_readiness_checks_ping_migration_queues_wecom_and_release() -> None:
    payload = runtime_readiness_payload(
        database_url="postgresql://readiness",
        connection_factory=_factory(_Connection()),
        expected_heads=("0104_auth_platform",),
        wecom_diagnostics=WECOM_OK,
        release_sha=FULL_SHA,
        production=True,
    )

    assert payload["ok"] is True
    assert payload["components"]["database"]["ping"] is True
    assert payload["components"]["migration"]["matches_head"] is True
    assert payload["components"]["queues"]["metrics"]["webhook_pending_count"] == 0
    assert payload["components"]["wecom"]["execution_mode"] == "execute"
    assert payload["components"]["release"]["exact_sha"] is True
    assert "must_not_be_returned" not in str(payload)
    assert payload["pii_in_output"] is False
    assert payload["secrets_in_output"] is False


def test_connection_failure_and_migration_drift_fail_closed() -> None:
    def fail_connection(_url: str):
        raise ConnectionError("secret connection detail")

    unavailable = runtime_readiness_payload(
        database_url="postgresql://unavailable",
        connection_factory=fail_connection,
        expected_heads=("0104_auth_platform",),
        wecom_diagnostics=WECOM_OK,
        release_sha=FULL_SHA,
        production=True,
    )
    drifted = runtime_readiness_payload(
        database_url="postgresql://readiness",
        connection_factory=_factory(_Connection(current_revision="0103_old")),
        expected_heads=("0104_auth_platform",),
        wecom_diagnostics=WECOM_OK,
        release_sha=FULL_SHA,
        production=True,
    )

    assert unavailable["ok"] is False
    assert unavailable["http_status"] == 503
    assert unavailable["failed_components"] == ["database", "migration", "queues"]
    assert "secret connection detail" not in str(unavailable)
    assert drifted["ok"] is False
    assert drifted["components"]["migration"]["status"] == "failed"


def test_queue_thresholds_are_visible_warnings_not_hidden_success(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_READINESS_MAX_QUEUE_AGE_SECONDS", "60")
    monkeypatch.setenv("AICRM_READINESS_MAX_TERMINAL_COUNT", "0")
    metrics = dict(_Connection().queue_metrics)
    metrics["webhook_oldest_pending_age_seconds"] = 61
    metrics["external_effect_terminal_count"] = 1

    payload = runtime_readiness_payload(
        database_url="postgresql://readiness",
        connection_factory=_factory(_Connection(queue_metrics=metrics)),
        expected_heads=("0104_auth_platform",),
        wecom_diagnostics=WECOM_OK,
        release_sha=FULL_SHA,
        production=True,
    )

    assert payload["ok"] is True
    assert payload["components"]["queues"]["status"] == "warning"
    assert payload["components"]["queues"]["warnings"] == [
        "oldest_pending_age_exceeded",
        "terminal_or_dead_letter_count_exceeded",
    ]
    assert payload["warning_components"] == ["queues"]


def test_rollout_gated_internal_event_history_does_not_raise_business_backlog_warning(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_READINESS_MAX_QUEUE_AGE_SECONDS", "60")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS", "payment.succeeded:order_projection_consumer")
    metrics = dict(_Connection().queue_metrics)
    metrics.update(
        {
            "internal_event_pending_count": 900,
            "internal_event_oldest_pending_age_seconds": 86400,
            "internal_event_actionable_pending_count": 0,
            "internal_event_actionable_oldest_pending_age_seconds": 0,
            "internal_event_rollout_gated_pending_count": 900,
            "internal_event_rollout_gated_oldest_pending_age_seconds": 86400,
            "internal_event_actionable_terminal_count": 0,
            "internal_event_rollout_gated_terminal_count": 0,
        }
    )

    payload = runtime_readiness_payload(
        database_url="postgresql://readiness",
        connection_factory=_factory(_Connection(queue_metrics=metrics)),
        expected_heads=("0104_auth_platform",),
        wecom_diagnostics=WECOM_OK,
        release_sha=FULL_SHA,
        production=True,
    )

    queues = payload["components"]["queues"]
    assert queues["status"] == "ok"
    assert queues["warnings"] == []
    assert queues["metrics"]["internal_event_rollout_gated_pending_count"] == 900


def test_held_history_is_reported_but_only_eligible_age_drives_readiness_warning(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_READINESS_MAX_QUEUE_AGE_SECONDS", "60")
    metrics = dict(_Connection().queue_metrics)
    metrics.update(
        {
            "queue_raw_open_count": 3033,
            "queue_held_count": 3033,
            "queue_eligible_count": 0,
            "queue_scheduled_count": 0,
            "queue_retry_wait_count": 0,
            "queue_rate_limited_count": 0,
            "queue_in_flight_count": 0,
            "queue_unknown_count": 0,
            "queue_dlq_count": 0,
            "webhook_oldest_pending_age_seconds": 86400,
            "webhook_eligible_oldest_pending_age_seconds": 0,
            "external_effect_oldest_pending_age_seconds": 86400,
            "external_effect_eligible_oldest_pending_age_seconds": 0,
        }
    )

    payload = runtime_readiness_payload(
        database_url="postgresql://readiness",
        connection_factory=_factory(_Connection(queue_metrics=metrics)),
        expected_heads=("0104_auth_platform",),
        wecom_diagnostics=WECOM_OK,
        release_sha=FULL_SHA,
        production=True,
    )

    queues = payload["components"]["queues"]
    assert queues["status"] == "ok"
    assert queues["warnings"] == []
    assert queues["metrics"]["queue_raw_open_count"] == 3033
    assert queues["metrics"]["queue_held_count"] == 3033
    assert queues["metrics"]["queue_eligible_count"] == 0


def test_system_health_returns_readiness_http_status(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    healthy = TestClient(create_app()).get("/api/system/health")

    assert healthy.status_code == 200
    assert healthy.json()["components"]["database"]["status"] == "fixture"

    monkeypatch.setattr(
        platform_api,
        "runtime_readiness_payload",
        lambda: {"ok": False, "http_status": 503, "failed_components": ["database"], "components": {}},
    )
    failed = TestClient(create_app()).get("/api/system/health")

    assert failed.status_code == 503
    assert failed.json()["failed_components"] == ["database"]
