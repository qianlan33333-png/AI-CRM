from __future__ import annotations

import pytest

from scripts.ops import check_wecom_callback_permanent_fix_readiness as readiness


SAMPLE_IDEMPOTENCY_KEY = "ww-test|change_external_contact|del_external_contact|wm-a|sales-a|1782530000||scene-a"
ORIGINAL_PROBE_JSON_OK = readiness.probe_json_ok


def _health() -> dict:
    return {"checked": True, "ok": True, "status_code": 200, "body": '{"ok":true}', "error": ""}


def _admin_metrics() -> dict:
    return {
        "checked": True,
        "ok": True,
        "status_code": 200,
        "body": '{"ok":true,"queue_metrics":{"provider_distribution":[],"route_distribution":[],"recent_errors":[]}}',
        "json_ok": True,
        "required_list_paths": [
            "queue_metrics.provider_distribution",
            "queue_metrics.route_distribution",
            "queue_metrics.recent_errors",
        ],
        "required_list_ok": True,
        "error": "",
    }


def _admin_items() -> dict:
    return {
        "checked": True,
        "ok": True,
        "status_code": 200,
        "body": '{"ok":true,"items":[]}',
        "json_ok": True,
        "required_list_key": "items",
        "required_list_ok": True,
        "error": "",
    }


def _admin_reconciliation() -> dict:
    return {
        "checked": True,
        "ok": True,
        "status_code": 200,
        "body": '{"ok":true,"recent_items":[]}',
        "json_ok": True,
        "required_list_key": "recent_items",
        "required_list_ok": True,
        "error": "",
    }


def _inbox() -> dict:
    return {
        "checked": True,
        "ok": True,
        "schema_present": True,
        "total_count": 3,
        "due_count": 0,
        "processing_count": 0,
        "failed_retryable_count": 0,
        "dead_letter_count": 0,
        "oldest_received_age_seconds": 0,
        "error": "",
    }


def _service(unit: str) -> dict:
    return {"checked": True, "unit": unit, "active": True, "status": "active", "error": ""}


@pytest.fixture(autouse=True)
def _stub_admin_webhook_inbox(monkeypatch) -> None:
    def fake_probe_json_ok(
        url: str,
        timeout: float,
        *,
        required_list_key: str = "",
        required_list_paths: tuple[str, ...] = (),
    ) -> dict:
        if required_list_key == "recent_items":
            return _admin_reconciliation()
        return _admin_items() if required_list_key == "items" else _admin_metrics()

    monkeypatch.setattr(readiness, "probe_json_ok", fake_probe_json_ok)


def test_wecom_callback_permanent_fix_readiness_accepts_cutover_state(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(["--nginx-config", str(config), "--env-file", str(tmp_path / "env")])

    assert payload["ready_for_production_cutover"] is True
    assert payload["ready_for_production_completion"] is False
    assert payload["ok"] is False
    assert payload["admin_webhook_inbox_metrics"]["ok"] is True
    assert payload["admin_webhook_inbox_items"]["ok"] is True
    assert payload["admin_webhook_inbox_reconciliation"]["ok"] is True
    assert payload["webhook_inbox"]["schema_present"] is True
    assert payload["services"]["callback_ingress"]["active"] is True
    assert "pressure evidence not provided; production completion still requires 1200/min pressure evidence" in payload["warnings"]


def test_wecom_callback_permanent_fix_readiness_rejects_quick_ack(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": True, "business_processing_suppressed": True},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": False, "ready_for_cutover": False},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(["--nginx-config", str(config), "--env-file", str(tmp_path / "env")])

    assert payload["ready_for_production_cutover"] is False
    assert payload["ready_for_production_completion"] is False
    assert "emergency quick ACK is still enabled" in payload["warnings"]
    assert "nginx cutover to 5002 is not ready" in payload["warnings"]


def test_readiness_rejects_failed_quick_ack_state_check(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {
            "ok": False,
            "emergency_quick_ack_enabled": False,
            "business_processing_suppressed": False,
            "nginx_error": "nginx config not readable",
        },
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(["--nginx-config", str(config), "--env-file", str(tmp_path / "env")])

    assert payload["ready_for_production_cutover"] is False
    assert payload["ready_for_production_completion"] is False
    assert "quick ACK state check failed: nginx config not readable" in payload["warnings"]


def test_readiness_rejects_failed_admin_webhook_inbox_metrics_api(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    def fake_probe_json_ok(
        url: str,
        timeout: float,
        *,
        required_list_key: str = "",
        required_list_paths: tuple[str, ...] = (),
    ) -> dict:
        if required_list_key == "items":
            return _admin_items()
        if required_list_key == "recent_items":
            return _admin_reconciliation()
        return {"checked": True, "ok": False, "status_code": 500, "body": "{}", "json_ok": False, "error": ""}

    monkeypatch.setattr(readiness, "probe_json_ok", fake_probe_json_ok)
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(["--nginx-config", str(config), "--env-file", str(tmp_path / "env")])

    assert payload["ready_for_production_cutover"] is False
    assert payload["ready_for_production_completion"] is False
    assert payload["admin_webhook_inbox_metrics"]["status_code"] == 500
    assert "admin webhook inbox metrics API is not available: 500" in payload["warnings"]


def test_readiness_rejects_admin_webhook_inbox_metrics_without_distribution_fields(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())

    def fake_probe_json_ok(
        url: str,
        timeout: float,
        *,
        required_list_key: str = "",
        required_list_paths: tuple[str, ...] = (),
    ) -> dict:
        if required_list_key == "items":
            return _admin_items()
        if required_list_key == "recent_items":
            return _admin_reconciliation()
        return {
            "checked": True,
            "ok": False,
            "status_code": 200,
            "body": '{"ok":true,"queue_metrics":{}}',
            "json_ok": True,
            "required_list_paths": list(required_list_paths),
            "required_list_ok": False,
            "error": "queue_metrics.provider_distribution is not a list",
        }

    monkeypatch.setattr(readiness, "probe_json_ok", fake_probe_json_ok)
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(["--nginx-config", str(config), "--env-file", str(tmp_path / "env")])

    assert payload["ready_for_production_cutover"] is False
    assert payload["ready_for_production_completion"] is False
    assert payload["admin_webhook_inbox_metrics"]["required_list_ok"] is False
    assert "admin webhook inbox metrics API is not available: queue_metrics.provider_distribution is not a list" in payload["warnings"]


def test_readiness_rejects_failed_admin_webhook_inbox_items_api(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())

    def fake_probe_json_ok(
        url: str,
        timeout: float,
        *,
        required_list_key: str = "",
        required_list_paths: tuple[str, ...] = (),
    ) -> dict:
        if required_list_key == "items":
            return {
                "checked": True,
                "ok": False,
                "status_code": 200,
                "body": '{"ok":true}',
                "json_ok": True,
                "required_list_key": "items",
                "required_list_ok": False,
                "error": "items is not a list",
            }
        if required_list_key == "recent_items":
            return _admin_reconciliation()
        return _admin_metrics()

    monkeypatch.setattr(readiness, "probe_json_ok", fake_probe_json_ok)
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(["--nginx-config", str(config), "--env-file", str(tmp_path / "env")])

    assert payload["ready_for_production_cutover"] is False
    assert payload["ready_for_production_completion"] is False
    assert payload["admin_webhook_inbox_items"]["required_list_ok"] is False
    assert "admin webhook inbox items API is not available: items is not a list" in payload["warnings"]


def test_readiness_rejects_failed_admin_webhook_inbox_reconciliation_api(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())

    def fake_probe_json_ok(
        url: str,
        timeout: float,
        *,
        required_list_key: str = "",
        required_list_paths: tuple[str, ...] = (),
    ) -> dict:
        if required_list_key == "recent_items":
            return {
                "checked": True,
                "ok": False,
                "status_code": 200,
                "body": '{"ok":true}',
                "json_ok": True,
                "required_list_key": "recent_items",
                "required_list_ok": False,
                "error": "recent_items is not a list",
            }
        return _admin_items() if required_list_key == "items" else _admin_metrics()

    monkeypatch.setattr(readiness, "probe_json_ok", fake_probe_json_ok)
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(["--nginx-config", str(config), "--env-file", str(tmp_path / "env")])

    assert payload["ready_for_production_cutover"] is False
    assert payload["ready_for_production_completion"] is False
    assert payload["admin_webhook_inbox_reconciliation"]["required_list_ok"] is False
    assert "admin webhook inbox reconciliation API is not available: recent_items is not a list" in payload["warnings"]


def test_json_ok_probe_accepts_ok_payload(monkeypatch) -> None:
    class JsonResponse:
        status = 200

        def read(self, limit: int) -> bytes:
            return b'{"ok": true, "queue_metrics": {}}'

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    monkeypatch.setattr(readiness, "urlopen", lambda request, timeout: JsonResponse())

    payload = ORIGINAL_PROBE_JSON_OK("http://example.test/api/admin/webhook-inbox/metrics", 1)

    assert payload["ok"] is True
    assert payload["json_ok"] is True
    assert payload["status_code"] == 200


def test_json_ok_probe_accepts_required_list_payload(monkeypatch) -> None:
    class JsonResponse:
        status = 200

        def read(self, limit: int) -> bytes:
            return b'{"ok": true, "items": []}'

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    monkeypatch.setattr(readiness, "urlopen", lambda request, timeout: JsonResponse())

    payload = ORIGINAL_PROBE_JSON_OK("http://example.test/api/admin/webhook-inbox/items", 1, required_list_key="items")

    assert payload["ok"] is True
    assert payload["json_ok"] is True
    assert payload["required_list_ok"] is True


def test_json_ok_probe_rejects_missing_required_list(monkeypatch) -> None:
    class JsonResponse:
        status = 200

        def read(self, limit: int) -> bytes:
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    monkeypatch.setattr(readiness, "urlopen", lambda request, timeout: JsonResponse())

    payload = ORIGINAL_PROBE_JSON_OK("http://example.test/api/admin/webhook-inbox/items", 1, required_list_key="items")

    assert payload["ok"] is False
    assert payload["json_ok"] is True
    assert payload["required_list_ok"] is False
    assert payload["error"] == "items is not a list"


def test_json_ok_probe_accepts_required_nested_list_payload(monkeypatch) -> None:
    class JsonResponse:
        status = 200

        def read(self, limit: int) -> bytes:
            return b'{"ok": true, "queue_metrics": {"provider_distribution": [], "route_distribution": [], "recent_errors": []}}'

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    monkeypatch.setattr(readiness, "urlopen", lambda request, timeout: JsonResponse())

    payload = ORIGINAL_PROBE_JSON_OK(
        "http://example.test/api/admin/webhook-inbox/metrics",
        1,
        required_list_paths=(
            "queue_metrics.provider_distribution",
            "queue_metrics.route_distribution",
            "queue_metrics.recent_errors",
        ),
    )

    assert payload["ok"] is True
    assert payload["json_ok"] is True
    assert payload["required_list_ok"] is True
    assert payload["required_list_checks"]["queue_metrics.provider_distribution"] is True


def test_json_ok_probe_rejects_missing_required_nested_list(monkeypatch) -> None:
    class JsonResponse:
        status = 200

        def read(self, limit: int) -> bytes:
            return b'{"ok": true, "queue_metrics": {"provider_distribution": []}}'

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    monkeypatch.setattr(readiness, "urlopen", lambda request, timeout: JsonResponse())

    payload = ORIGINAL_PROBE_JSON_OK(
        "http://example.test/api/admin/webhook-inbox/metrics",
        1,
        required_list_paths=(
            "queue_metrics.provider_distribution",
            "queue_metrics.route_distribution",
            "queue_metrics.recent_errors",
        ),
    )

    assert payload["ok"] is False
    assert payload["json_ok"] is True
    assert payload["required_list_ok"] is False
    assert payload["required_list_checks"]["queue_metrics.provider_distribution"] is True
    assert payload["required_list_checks"]["queue_metrics.route_distribution"] is False
    assert payload["required_list_checks"]["queue_metrics.recent_errors"] is False
    assert payload["error"] == "queue_metrics.route_distribution, queue_metrics.recent_errors is not a list"


def test_json_ok_probe_rejects_non_ok_payload(monkeypatch) -> None:
    class JsonResponse:
        status = 200

        def read(self, limit: int) -> bytes:
            return b'{"ok": false}'

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    monkeypatch.setattr(readiness, "urlopen", lambda request, timeout: JsonResponse())

    payload = ORIGINAL_PROBE_JSON_OK("http://example.test/api/admin/webhook-inbox/metrics", 1)

    assert payload["ok"] is False
    assert payload["json_ok"] is False
    assert payload["status_code"] == 200


def _pressure_payload(*, ok: bool = True) -> dict:
    return {
        "ok": ok,
        "real_external_call_executed": False,
        "sample_validation": {
            "checked": True,
            "ok": ok,
            "event_summary": {"Event": "change_external_contact", "ChangeType": "del_external_contact"},
            "idempotency_key": SAMPLE_IDEMPOTENCY_KEY,
            "plain_xml_bytes": 240,
            "error": "" if ok else "invalid callback signature",
        },
        "pressure": {"requested_rate_per_minute": 1200.0, "observed_rate_per_minute": 1201.0},
        "callback": {
            "meets_status_target": ok,
            "meets_p95_target": ok,
            "meets_p99_target": ok,
            "target_p95_ms": 200,
            "target_p99_ms": 500,
            "latency_ms": {"p95": 120, "p99": 220},
        },
        "page_samples": {
            "health": {"meets_status_target": True, "meets_p95_target": True, "target_p95_ms": 100, "latency_ms": {"p95": 40}},
            "sidebar_bind_mobile": {"meets_status_target": True, "meets_p95_target": True, "target_p95_ms": 300, "latency_ms": {"p95": 180}},
            "automation_conversion_admin": {"meets_status_target": True, "meets_p95_target": True, "target_p95_ms": 500, "latency_ms": {"p95": 260}},
        },
    }


def _worker_isolation_payload(*, ok: bool = True) -> dict:
    return {
        "ok": ok,
        "real_external_call_executed": False,
        "sample_validation": {
            "checked": True,
            "ok": ok,
            "event_summary": {"Event": "change_external_contact", "ChangeType": "del_external_contact"},
            "idempotency_key": SAMPLE_IDEMPOTENCY_KEY,
            "plain_xml_bytes": 240,
            "error": "" if ok else "invalid callback signature",
        },
        "pressure": {"requested_rate_per_minute": 60000.0, "observed_rate_per_minute": 60000.0, "total_requests": 1},
        "callback": {
            "request_count": 1 if ok else 0,
            "meets_status_target": ok,
            "meets_p95_target": ok,
            "meets_p99_target": ok,
            "latency_ms": {"p95": 80, "p99": 80},
        },
        "page_samples": {},
    }


def _downstream_worker_isolation_payload(*, ok: bool = True) -> dict:
    return {
        "ok": ok,
        "real_external_call_executed": False,
        "sample_validation": {
            "checked": True,
            "ok": ok,
            "event_summary": {"Event": "change_external_contact", "ChangeType": "del_external_contact"},
            "idempotency_key": SAMPLE_IDEMPOTENCY_KEY,
            "plain_xml_bytes": 240,
            "error": "" if ok else "invalid callback signature",
        },
        "pressure": {"requested_rate_per_minute": 60000.0, "observed_rate_per_minute": 60000.0, "total_requests": 1},
        "callback": {
            "request_count": 1 if ok else 0,
            "meets_status_target": ok,
            "meets_p95_target": ok,
            "meets_p99_target": ok,
            "latency_ms": {"p95": 80, "p99": 80},
        },
        "page_samples": {
            "health": {"meets_status_target": ok, "meets_p95_target": ok},
            "sidebar_bind_mobile": {"meets_status_target": ok, "meets_p95_target": ok},
            "automation_conversion_admin": {"meets_status_target": ok, "meets_p95_target": ok},
        },
    }


def _internal_event_worker_isolation_payload(*, ok: bool = True) -> dict:
    return {
        "ok": ok,
        "real_external_call_executed": False,
        "sample_validation": {
            "checked": True,
            "ok": ok,
            "event_summary": {"Event": "change_external_contact", "ChangeType": "del_external_contact"},
            "idempotency_key": SAMPLE_IDEMPOTENCY_KEY,
            "plain_xml_bytes": 240,
            "error": "" if ok else "invalid callback signature",
        },
        "pressure": {"requested_rate_per_minute": 60000.0, "observed_rate_per_minute": 60000.0, "total_requests": 1},
        "callback": {
            "request_count": 1 if ok else 0,
            "meets_status_target": ok,
            "meets_p95_target": ok,
            "meets_p99_target": ok,
            "latency_ms": {"p95": 80, "p99": 80},
        },
        "page_samples": {
            "health": {"meets_status_target": ok, "meets_p95_target": ok},
            "sidebar_bind_mobile": {"meets_status_target": ok, "meets_p95_target": ok},
            "automation_conversion_admin": {"meets_status_target": ok, "meets_p95_target": ok},
        },
    }


def _ingestion_evidence_payload(*, ok: bool = True) -> dict:
    key = SAMPLE_IDEMPOTENCY_KEY
    return {
        "ok": ok,
        "idempotency_key": key,
        "requirements": {"provider": "wecom", "event_family": "external_contact", "max_age_seconds": 600},
        "webhook_inbox_row": {
            "found": ok,
            "id": 42 if ok else 0,
            "tenant_id": "aicrm",
            "provider": "wecom" if ok else "",
            "event_family": "external_contact" if ok else "",
            "idempotency_key": key if ok else "",
            "status": "received",
            "duplicate_count": 1,
            "age_seconds": 12,
        },
        "violations": [] if ok else ["webhook_inbox row not found"],
        "error": "" if ok else "webhook_inbox row not found for idempotency_key",
    }


def _processing_evidence_payload(*, ok: bool = True) -> dict:
    key = SAMPLE_IDEMPOTENCY_KEY
    return {
        "ok": ok,
        "idempotency_key": key,
        "requirements": {"provider": "wecom", "event_family": "external_contact", "status": "succeeded"},
        "webhook_inbox_row": {
            "found": ok,
            "id": 43 if ok else 0,
            "tenant_id": "aicrm",
            "provider": "wecom" if ok else "",
            "event_family": "external_contact" if ok else "",
            "event_type": "change_external_contact",
            "change_type": "del_external_contact",
            "idempotency_key": key if ok else "",
            "status": "succeeded" if ok else "received",
            "started_at": "2026-06-27T10:00:01+00:00" if ok else "",
            "finished_at": "2026-06-27T10:00:02+00:00" if ok else "",
            "processing_summary_json": {
                "handled": False,
                "identity_sync_status": "skipped",
                "external_effect_job_ids": [],
            }
            if ok
            else {},
        },
        "violations": [] if ok else ["status is not succeeded"],
        "error": "" if ok else "status is not succeeded",
    }


def _rollback_evidence_payload(*, ok: bool = True) -> dict:
    return {
        "ok": ok,
        "production_rollback_drill": ok,
        "rollback_ready": ok,
        "backup_path": "/etc/nginx/sites-enabled/youcangogogo.conf.bak-codex-callback-cutover-20260627T120000",
        "backup_exists": ok,
        "nginx_test_after_restore_ok": ok,
        "nginx_reload_after_restore_ok": ok,
        "web_health_after_restore": {"ok": ok, "status_code": 200 if ok else 503},
        "quick_ack_after_restore": {"ok": ok, "emergency_quick_ack_enabled": ok},
        "cutover_reapplied_after_drill": ok,
    }


def _public_state_payload(*, ok: bool = True) -> dict:
    return {
        "ok": ok,
        "base_url": "https://www.youcangogogo.com",
        "user_facing_available": ok,
        "admin_webhook_inbox_api_deployed": ok,
        "admin_webhook_inbox_detail_route_deployed": ok,
        "invalid_callback_plain_success": False if ok else True,
        "app_level_callback_signal": ok,
        "callback_route_signals": [
            {
                "path": "/wecom/external-contact/callback?msg_signature=invalid&timestamp=1&nonce=public-state-probe",
                "checked": ok,
                "plain_success": False if ok else True,
                "app_level_callback_signal": ok,
                "status_code": 400 if ok else 200,
                "error": "",
            },
            {
                "path": "/api/wecom/events?msg_signature=invalid&timestamp=1&nonce=public-state-probe-api-events",
                "checked": ok,
                "plain_success": False if ok else True,
                "app_level_callback_signal": ok,
                "status_code": 400 if ok else 200,
                "error": "",
            },
        ],
        "permanent_fix_public_signals_ready": ok,
        "warnings": [] if ok else ["invalid callback POST still returns plain success"],
    }


def _deploy_smoke_payload(*, ok: bool = True) -> dict:
    return {
        "ok": ok,
        "web_base_url": "http://127.0.0.1:5001",
        "ingress_base_url": "http://127.0.0.1:5002",
        "base_urls_distinct": ok,
        "web_health_ok": ok,
        "ingress_health_ok": ok,
        "ingress_durable_ack_ready": ok,
        "admin_api_deployed": ok,
        "admin_detail_route_deployed": ok,
        "ingress_callback_routes_ready": ok,
        "ingress_callback_route_signals": [
            {
                "path": "/wecom/external-contact/callback?msg_signature=invalid&timestamp=1&nonce=deploy-smoke-probe",
                "checked": ok,
                "plain_success": False if ok else True,
                "app_level_callback_signal": ok,
                "status_code": 400 if ok else 200,
                "error": "",
            },
            {
                "path": "/api/wecom/events?msg_signature=invalid&timestamp=1&nonce=deploy-smoke-probe-api-events",
                "checked": ok,
                "plain_success": False if ok else True,
                "app_level_callback_signal": ok,
                "status_code": 400 if ok else 200,
                "error": "",
            },
        ],
        "warnings": [] if ok else ["webhook inbox admin JSON APIs are not deployed"],
    }


def test_pressure_evidence_accepts_probe_output(tmp_path) -> None:
    evidence = tmp_path / "pressure.json"
    evidence.write_text(readiness.json.dumps(_pressure_payload()), encoding="utf-8")

    payload = readiness.read_pressure_evidence(str(evidence))

    assert payload["ok"] is True
    assert payload["requested_rate_per_minute"] == 1200.0
    assert payload["observed_rate_per_minute"] == 1201.0
    assert payload["rate_target_met"] is True
    assert payload["callback_targets"] == {"status": True, "p95": True, "p99": True}
    assert payload["missing_page_samples"] == []
    assert payload["sample_validation_target_met"] is True


def test_webhook_ingestion_evidence_accepts_recent_inbox_row(tmp_path) -> None:
    evidence = tmp_path / "ingestion.json"
    evidence.write_text(readiness.json.dumps(_ingestion_evidence_payload()), encoding="utf-8")

    payload = readiness.read_webhook_ingestion_evidence(str(evidence))

    assert payload["ok"] is True
    assert payload["webhook_inbox_row"]["found"] is True
    assert payload["webhook_inbox_row"]["provider"] == "wecom"


def test_webhook_ingestion_evidence_rejects_failed_db_proof(tmp_path) -> None:
    evidence = tmp_path / "ingestion.json"
    evidence.write_text(readiness.json.dumps(_ingestion_evidence_payload(ok=False)), encoding="utf-8")

    payload = readiness.read_webhook_ingestion_evidence(str(evidence))

    assert payload["ok"] is False
    assert "webhook_inbox row not found" in payload["error"]


def test_webhook_processing_evidence_accepts_processed_noop_canary_row(tmp_path) -> None:
    evidence = tmp_path / "processing.json"
    evidence.write_text(readiness.json.dumps(_processing_evidence_payload()), encoding="utf-8")

    payload = readiness.read_webhook_processing_evidence(str(evidence))

    assert payload["ok"] is True
    assert payload["webhook_inbox_row"]["status"] == "succeeded"
    assert payload["webhook_inbox_row"]["processing_summary_json"]["identity_sync_status"] == "skipped"


def test_same_sample_evidence_accepts_matching_pressure_ingestion_and_processing() -> None:
    payload = readiness.evaluate_same_sample_evidence(
        pressure_evidence={"checked": True, "ok": True, "sample_validation": {"idempotency_key": SAMPLE_IDEMPOTENCY_KEY}},
        webhook_ingestion_evidence={"checked": True, "ok": True, "idempotency_key": SAMPLE_IDEMPOTENCY_KEY},
        webhook_processing_evidence={"checked": True, "ok": True, "idempotency_key": SAMPLE_IDEMPOTENCY_KEY},
    )

    assert payload["ok"] is True
    assert set(payload["idempotency_keys"].values()) == {SAMPLE_IDEMPOTENCY_KEY}


def test_same_sample_evidence_rejects_mismatched_processing_key() -> None:
    payload = readiness.evaluate_same_sample_evidence(
        pressure_evidence={"checked": True, "ok": True, "sample_validation": {"idempotency_key": SAMPLE_IDEMPOTENCY_KEY}},
        webhook_ingestion_evidence={"checked": True, "ok": True, "idempotency_key": SAMPLE_IDEMPOTENCY_KEY},
        webhook_processing_evidence={"checked": True, "ok": True, "idempotency_key": "other-key"},
    )

    assert payload["ok"] is False
    assert "same idempotency_key" in payload["error"]


def test_webhook_processing_evidence_rejects_unprocessed_row(tmp_path) -> None:
    evidence = tmp_path / "processing.json"
    evidence.write_text(readiness.json.dumps(_processing_evidence_payload(ok=False)), encoding="utf-8")

    payload = readiness.read_webhook_processing_evidence(str(evidence))

    assert payload["ok"] is False
    assert "status is not succeeded" in payload["error"]


def test_webhook_processing_evidence_rejects_missing_started_at(tmp_path) -> None:
    evidence = tmp_path / "processing.json"
    payload_body = _processing_evidence_payload()
    payload_body["webhook_inbox_row"]["started_at"] = ""
    evidence.write_text(readiness.json.dumps(payload_body), encoding="utf-8")

    payload = readiness.read_webhook_processing_evidence(str(evidence))

    assert payload["ok"] is False
    assert "webhook_inbox processing evidence" in payload["error"]


def test_pressure_evidence_rejects_failed_probe_output(tmp_path) -> None:
    evidence = tmp_path / "pressure.json"
    failed = _pressure_payload(ok=False)
    failed["page_samples"]["sidebar_bind_mobile"]["meets_p95_target"] = False
    evidence.write_text(readiness.json.dumps(failed), encoding="utf-8")

    payload = readiness.read_pressure_evidence(str(evidence))

    assert payload["ok"] is False
    assert payload["page_targets"]["sidebar_bind_mobile"] is False
    assert "does not meet readiness targets" in payload["error"]


def test_pressure_evidence_rejects_below_1200_per_minute_probe_output(tmp_path) -> None:
    evidence = tmp_path / "pressure.json"
    slow = _pressure_payload()
    slow["pressure"] = {"requested_rate_per_minute": 600.0, "observed_rate_per_minute": 1199.0}
    evidence.write_text(readiness.json.dumps(slow), encoding="utf-8")

    payload = readiness.read_pressure_evidence(str(evidence))

    assert payload["ok"] is False
    assert payload["min_required_rate_per_minute"] == 1200.0
    assert payload["requested_rate_per_minute"] == 600.0
    assert payload["observed_rate_per_minute"] == 1199.0
    assert payload["rate_target_met"] is False


def test_pressure_evidence_rejects_relaxed_callback_latency_targets(tmp_path) -> None:
    evidence = tmp_path / "pressure.json"
    relaxed = _pressure_payload()
    relaxed["callback"]["target_p95_ms"] = 1000
    relaxed["callback"]["target_p99_ms"] = 2000
    relaxed["callback"]["latency_ms"] = {"p95": 800, "p99": 1500}
    evidence.write_text(readiness.json.dumps(relaxed), encoding="utf-8")

    payload = readiness.read_pressure_evidence(str(evidence))

    assert payload["ok"] is False
    assert payload["callback_targets"] == {"status": True, "p95": True, "p99": True}
    assert payload["callback_latency_target"]["ok"] is False
    assert payload["callback_latency_target"]["max_allowed_p95_ms"] == 200.0
    assert payload["callback_latency_target"]["max_allowed_p99_ms"] == 500.0


def test_pressure_evidence_rejects_relaxed_page_latency_targets(tmp_path) -> None:
    evidence = tmp_path / "pressure.json"
    relaxed = _pressure_payload()
    relaxed["page_samples"]["health"]["target_p95_ms"] = 1000
    relaxed["page_samples"]["health"]["latency_ms"] = {"p95": 800}
    evidence.write_text(readiness.json.dumps(relaxed), encoding="utf-8")

    payload = readiness.read_pressure_evidence(str(evidence))

    assert payload["ok"] is False
    assert payload["page_targets"]["health"] is True
    assert payload["page_latency_targets"]["health"]["ok"] is False
    assert payload["page_latency_targets"]["health"]["max_allowed_p95_ms"] == 100.0


def test_pressure_evidence_rejects_missing_sample_validation(tmp_path) -> None:
    evidence = tmp_path / "pressure.json"
    payload = _pressure_payload()
    payload.pop("sample_validation")
    evidence.write_text(readiness.json.dumps(payload), encoding="utf-8")

    result = readiness.read_pressure_evidence(str(evidence))

    assert result["ok"] is False
    assert result["sample_validation_target_met"] is False
    assert "does not meet readiness targets" in result["error"]


def test_worker_isolation_evidence_accepts_single_valid_callback_ack(tmp_path) -> None:
    evidence = tmp_path / "worker.json"
    evidence.write_text(readiness.json.dumps(_worker_isolation_payload()), encoding="utf-8")

    payload = readiness.read_worker_isolation_evidence(str(evidence))

    assert payload["ok"] is True
    assert payload["total_requests"] == 1
    assert payload["callback_request_count"] == 1
    assert payload["sample_validation_target_met"] is True


def test_worker_isolation_evidence_rejects_failed_canary(tmp_path) -> None:
    evidence = tmp_path / "worker.json"
    failed = _worker_isolation_payload(ok=False)
    evidence.write_text(readiness.json.dumps(failed), encoding="utf-8")

    payload = readiness.read_worker_isolation_evidence(str(evidence))

    assert payload["ok"] is False
    assert "does not meet readiness targets" in payload["error"]


def test_downstream_worker_isolation_evidence_accepts_ack_and_page_samples(tmp_path) -> None:
    evidence = tmp_path / "downstream.json"
    evidence.write_text(readiness.json.dumps(_downstream_worker_isolation_payload()), encoding="utf-8")

    payload = readiness.read_downstream_worker_isolation_evidence(str(evidence))

    assert payload["ok"] is True
    assert payload["total_requests"] == 1
    assert payload["callback_request_count"] == 1
    assert payload["missing_page_samples"] == []
    assert payload["sample_validation_target_met"] is True


def test_internal_event_worker_isolation_evidence_accepts_ack_and_page_samples(tmp_path) -> None:
    evidence = tmp_path / "internal-event.json"
    evidence.write_text(readiness.json.dumps(_internal_event_worker_isolation_payload()), encoding="utf-8")

    payload = readiness.read_internal_event_worker_isolation_evidence(str(evidence))

    assert payload["ok"] is True
    assert payload["total_requests"] == 1
    assert payload["callback_request_count"] == 1
    assert payload["missing_page_samples"] == []
    assert payload["sample_validation_target_met"] is True


def test_internal_event_worker_isolation_evidence_rejects_failed_pages(tmp_path) -> None:
    evidence = tmp_path / "internal-event.json"
    failed = _internal_event_worker_isolation_payload()
    failed["page_samples"]["automation_conversion_admin"]["meets_p95_target"] = False
    failed["ok"] = False
    evidence.write_text(readiness.json.dumps(failed), encoding="utf-8")

    payload = readiness.read_internal_event_worker_isolation_evidence(str(evidence))

    assert payload["ok"] is False
    assert payload["page_targets"]["automation_conversion_admin"] is False
    assert "does not meet readiness targets" in payload["error"]


def test_downstream_worker_isolation_evidence_rejects_failed_pages(tmp_path) -> None:
    evidence = tmp_path / "downstream.json"
    failed = _downstream_worker_isolation_payload()
    failed["page_samples"]["sidebar_bind_mobile"]["meets_status_target"] = False
    failed["ok"] = False
    evidence.write_text(readiness.json.dumps(failed), encoding="utf-8")

    payload = readiness.read_downstream_worker_isolation_evidence(str(evidence))

    assert payload["ok"] is False
    assert payload["page_targets"]["sidebar_bind_mobile"] is False
    assert "does not meet readiness targets" in payload["error"]


def test_webhook_inbox_health_accepts_threshold_overrides() -> None:
    inbox = _inbox()
    inbox["due_count"] = 8
    inbox["oldest_received_age_seconds"] = 45

    payload = readiness.evaluate_webhook_inbox_health(
        inbox,
        max_due_count=10,
        max_failed_retryable_count=0,
        max_dead_letter_count=0,
        max_oldest_age_seconds=60,
    )

    assert payload["ok"] is True
    assert payload["counts"]["due_count"] == 8
    assert payload["thresholds"]["max_due_count"] == 10
    assert payload["violations"] == []


def test_webhook_inbox_health_rejects_dead_letters_and_old_backlog() -> None:
    inbox = _inbox()
    inbox["dead_letter_count"] = 1
    inbox["oldest_received_age_seconds"] = 999

    payload = readiness.evaluate_webhook_inbox_health(inbox)

    assert payload["ok"] is False
    assert any("dead_letter_count 1 exceeds 0" in violation for violation in payload["violations"])
    assert any("oldest_received_age_seconds 999 exceeds 300" in violation for violation in payload["violations"])


def test_readiness_rejects_failed_pressure_evidence(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    evidence = tmp_path / "pressure.json"
    worker_evidence = tmp_path / "worker.json"
    downstream_evidence = tmp_path / "downstream.json"
    evidence.write_text(readiness.json.dumps(_pressure_payload(ok=False)), encoding="utf-8")
    worker_evidence.write_text(readiness.json.dumps(_worker_isolation_payload()), encoding="utf-8")
    downstream_evidence.write_text(readiness.json.dumps(_downstream_worker_isolation_payload()), encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(
        [
            "--nginx-config",
            str(config),
            "--env-file",
            str(tmp_path / "env"),
            "--pressure-evidence-file",
            str(evidence),
            "--worker-isolation-evidence-file",
            str(worker_evidence),
            "--downstream-worker-isolation-evidence-file",
            str(downstream_evidence),
        ]
    )

    assert payload["ready_for_production_cutover"] is True
    assert payload["ready_for_production_completion"] is False
    assert payload["pressure_evidence"]["ok"] is False
    assert any("pressure evidence failed" in warning for warning in payload["warnings"])


def test_readiness_rejects_unhealthy_webhook_inbox_metrics(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    evidence = tmp_path / "pressure.json"
    worker_evidence = tmp_path / "worker.json"
    downstream_evidence = tmp_path / "downstream.json"
    evidence.write_text(readiness.json.dumps(_pressure_payload()), encoding="utf-8")
    worker_evidence.write_text(readiness.json.dumps(_worker_isolation_payload()), encoding="utf-8")
    downstream_evidence.write_text(readiness.json.dumps(_downstream_worker_isolation_payload()), encoding="utf-8")
    unhealthy_inbox = _inbox()
    unhealthy_inbox["dead_letter_count"] = 1
    unhealthy_inbox["oldest_received_age_seconds"] = 999
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: unhealthy_inbox)
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(
        [
            "--nginx-config",
            str(config),
            "--env-file",
            str(tmp_path / "env"),
            "--pressure-evidence-file",
            str(evidence),
            "--worker-isolation-evidence-file",
            str(worker_evidence),
            "--downstream-worker-isolation-evidence-file",
            str(downstream_evidence),
        ]
    )

    assert payload["ready_for_production_cutover"] is True
    assert payload["ready_for_production_completion"] is False
    assert payload["webhook_inbox_health"]["ok"] is False
    assert any("webhook_inbox health failed" in warning for warning in payload["warnings"])


def test_readiness_skip_db_prevents_completion_even_with_pressure(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    evidence = tmp_path / "pressure.json"
    worker_evidence = tmp_path / "worker.json"
    downstream_evidence = tmp_path / "downstream.json"
    evidence.write_text(readiness.json.dumps(_pressure_payload()), encoding="utf-8")
    worker_evidence.write_text(readiness.json.dumps(_worker_isolation_payload()), encoding="utf-8")
    downstream_evidence.write_text(readiness.json.dumps(_downstream_worker_isolation_payload()), encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(
        [
            "--nginx-config",
            str(config),
            "--env-file",
            str(tmp_path / "env"),
            "--pressure-evidence-file",
            str(evidence),
            "--worker-isolation-evidence-file",
            str(worker_evidence),
            "--downstream-worker-isolation-evidence-file",
            str(downstream_evidence),
            "--skip-db",
        ]
    )

    assert payload["ready_for_production_cutover"] is True
    assert payload["ready_for_production_completion"] is False
    assert payload["webhook_inbox_health"]["ok"] is None
    assert any("webhook_inbox health not checked" in warning for warning in payload["warnings"])


def test_readiness_rejects_missing_worker_isolation_evidence(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    evidence = tmp_path / "pressure.json"
    downstream_evidence = tmp_path / "downstream.json"
    evidence.write_text(readiness.json.dumps(_pressure_payload()), encoding="utf-8")
    downstream_evidence.write_text(readiness.json.dumps(_downstream_worker_isolation_payload()), encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(
        [
            "--nginx-config",
            str(config),
            "--env-file",
            str(tmp_path / "env"),
            "--pressure-evidence-file",
            str(evidence),
            "--downstream-worker-isolation-evidence-file",
            str(downstream_evidence),
        ]
    )

    assert payload["ready_for_production_cutover"] is True
    assert payload["ready_for_production_completion"] is False
    assert payload["worker_isolation_evidence"]["ok"] is None
    assert any("worker isolation evidence not provided" in warning for warning in payload["warnings"])


def test_readiness_rejects_failed_worker_isolation_evidence(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    evidence = tmp_path / "pressure.json"
    worker_evidence = tmp_path / "worker.json"
    downstream_evidence = tmp_path / "downstream.json"
    evidence.write_text(readiness.json.dumps(_pressure_payload()), encoding="utf-8")
    worker_evidence.write_text(readiness.json.dumps(_worker_isolation_payload(ok=False)), encoding="utf-8")
    downstream_evidence.write_text(readiness.json.dumps(_downstream_worker_isolation_payload()), encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(
        [
            "--nginx-config",
            str(config),
            "--env-file",
            str(tmp_path / "env"),
            "--pressure-evidence-file",
            str(evidence),
            "--worker-isolation-evidence-file",
            str(worker_evidence),
            "--downstream-worker-isolation-evidence-file",
            str(downstream_evidence),
        ]
    )

    assert payload["ready_for_production_cutover"] is True
    assert payload["ready_for_production_completion"] is False
    assert payload["worker_isolation_evidence"]["ok"] is False
    assert any("worker isolation evidence failed" in warning for warning in payload["warnings"])


def test_readiness_rejects_missing_downstream_worker_isolation_evidence(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    evidence = tmp_path / "pressure.json"
    worker_evidence = tmp_path / "worker.json"
    evidence.write_text(readiness.json.dumps(_pressure_payload()), encoding="utf-8")
    worker_evidence.write_text(readiness.json.dumps(_worker_isolation_payload()), encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(
        [
            "--nginx-config",
            str(config),
            "--env-file",
            str(tmp_path / "env"),
            "--pressure-evidence-file",
            str(evidence),
            "--worker-isolation-evidence-file",
            str(worker_evidence),
        ]
    )

    assert payload["ready_for_production_cutover"] is True
    assert payload["ready_for_production_completion"] is False
    assert payload["downstream_worker_isolation_evidence"]["ok"] is None
    assert any("downstream worker isolation evidence not provided" in warning for warning in payload["warnings"])


def test_readiness_rejects_failed_downstream_worker_isolation_evidence(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    evidence = tmp_path / "pressure.json"
    worker_evidence = tmp_path / "worker.json"
    downstream_evidence = tmp_path / "downstream.json"
    evidence.write_text(readiness.json.dumps(_pressure_payload()), encoding="utf-8")
    worker_evidence.write_text(readiness.json.dumps(_worker_isolation_payload()), encoding="utf-8")
    downstream_evidence.write_text(readiness.json.dumps(_downstream_worker_isolation_payload(ok=False)), encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(
        [
            "--nginx-config",
            str(config),
            "--env-file",
            str(tmp_path / "env"),
            "--pressure-evidence-file",
            str(evidence),
            "--worker-isolation-evidence-file",
            str(worker_evidence),
            "--downstream-worker-isolation-evidence-file",
            str(downstream_evidence),
        ]
    )

    assert payload["ready_for_production_cutover"] is True
    assert payload["ready_for_production_completion"] is False
    assert payload["downstream_worker_isolation_evidence"]["ok"] is False
    assert any("downstream worker isolation evidence failed" in warning for warning in payload["warnings"])


def test_readiness_rejects_missing_webhook_ingestion_evidence(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    evidence = tmp_path / "pressure.json"
    worker_evidence = tmp_path / "worker.json"
    downstream_evidence = tmp_path / "downstream.json"
    evidence.write_text(readiness.json.dumps(_pressure_payload()), encoding="utf-8")
    worker_evidence.write_text(readiness.json.dumps(_worker_isolation_payload()), encoding="utf-8")
    downstream_evidence.write_text(readiness.json.dumps(_downstream_worker_isolation_payload()), encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(
        [
            "--nginx-config",
            str(config),
            "--env-file",
            str(tmp_path / "env"),
            "--pressure-evidence-file",
            str(evidence),
            "--worker-isolation-evidence-file",
            str(worker_evidence),
            "--downstream-worker-isolation-evidence-file",
            str(downstream_evidence),
        ]
    )

    assert payload["ready_for_production_cutover"] is True
    assert payload["ready_for_production_completion"] is False
    assert payload["webhook_ingestion_evidence"]["ok"] is None
    assert any("webhook_inbox ingestion evidence not provided" in warning for warning in payload["warnings"])


def test_readiness_rejects_missing_rollback_evidence(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    evidence = tmp_path / "pressure.json"
    ingestion_evidence = tmp_path / "ingestion.json"
    worker_evidence = tmp_path / "worker.json"
    downstream_evidence = tmp_path / "downstream.json"
    evidence.write_text(readiness.json.dumps(_pressure_payload()), encoding="utf-8")
    ingestion_evidence.write_text(readiness.json.dumps(_ingestion_evidence_payload()), encoding="utf-8")
    worker_evidence.write_text(readiness.json.dumps(_worker_isolation_payload()), encoding="utf-8")
    downstream_evidence.write_text(readiness.json.dumps(_downstream_worker_isolation_payload()), encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(
        [
            "--nginx-config",
            str(config),
            "--env-file",
            str(tmp_path / "env"),
            "--pressure-evidence-file",
            str(evidence),
            "--ingestion-evidence-file",
            str(ingestion_evidence),
            "--worker-isolation-evidence-file",
            str(worker_evidence),
            "--downstream-worker-isolation-evidence-file",
            str(downstream_evidence),
        ]
    )

    assert payload["ready_for_production_cutover"] is True
    assert payload["ready_for_production_completion"] is False
    assert payload["rollback_evidence"]["ok"] is None
    assert any("rollback evidence not provided" in warning for warning in payload["warnings"])


def test_readiness_rejects_mismatched_same_sample_evidence(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    evidence = tmp_path / "pressure.json"
    ingestion_evidence = tmp_path / "ingestion.json"
    processing_evidence = tmp_path / "processing.json"
    worker_evidence = tmp_path / "worker.json"
    downstream_evidence = tmp_path / "downstream.json"
    rollback_evidence = tmp_path / "rollback.json"
    mismatched_processing = _processing_evidence_payload()
    mismatched_processing["idempotency_key"] = "other-key"
    mismatched_processing["webhook_inbox_row"]["idempotency_key"] = "other-key"
    evidence.write_text(readiness.json.dumps(_pressure_payload()), encoding="utf-8")
    ingestion_evidence.write_text(readiness.json.dumps(_ingestion_evidence_payload()), encoding="utf-8")
    processing_evidence.write_text(readiness.json.dumps(mismatched_processing), encoding="utf-8")
    worker_evidence.write_text(readiness.json.dumps(_worker_isolation_payload()), encoding="utf-8")
    downstream_evidence.write_text(readiness.json.dumps(_downstream_worker_isolation_payload()), encoding="utf-8")
    rollback_evidence.write_text(readiness.json.dumps(_rollback_evidence_payload()), encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(
        [
            "--nginx-config",
            str(config),
            "--env-file",
            str(tmp_path / "env"),
            "--pressure-evidence-file",
            str(evidence),
            "--ingestion-evidence-file",
            str(ingestion_evidence),
            "--processing-evidence-file",
            str(processing_evidence),
            "--worker-isolation-evidence-file",
            str(worker_evidence),
            "--downstream-worker-isolation-evidence-file",
            str(downstream_evidence),
            "--rollback-evidence-file",
            str(rollback_evidence),
        ]
    )

    assert payload["ready_for_production_cutover"] is True
    assert payload["ready_for_production_completion"] is False
    assert payload["same_sample_evidence"]["ok"] is False
    assert any("same-sample pressure/ingestion/processing evidence failed" in warning for warning in payload["warnings"])


def test_readiness_rejects_missing_internal_event_worker_isolation_evidence(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    evidence = tmp_path / "pressure.json"
    ingestion_evidence = tmp_path / "ingestion.json"
    processing_evidence = tmp_path / "processing.json"
    worker_evidence = tmp_path / "worker.json"
    downstream_evidence = tmp_path / "downstream.json"
    rollback_evidence = tmp_path / "rollback.json"
    evidence.write_text(readiness.json.dumps(_pressure_payload()), encoding="utf-8")
    ingestion_evidence.write_text(readiness.json.dumps(_ingestion_evidence_payload()), encoding="utf-8")
    processing_evidence.write_text(readiness.json.dumps(_processing_evidence_payload()), encoding="utf-8")
    worker_evidence.write_text(readiness.json.dumps(_worker_isolation_payload()), encoding="utf-8")
    downstream_evidence.write_text(readiness.json.dumps(_downstream_worker_isolation_payload()), encoding="utf-8")
    rollback_evidence.write_text(readiness.json.dumps(_rollback_evidence_payload()), encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(
        [
            "--nginx-config",
            str(config),
            "--env-file",
            str(tmp_path / "env"),
            "--pressure-evidence-file",
            str(evidence),
            "--ingestion-evidence-file",
            str(ingestion_evidence),
            "--processing-evidence-file",
            str(processing_evidence),
            "--worker-isolation-evidence-file",
            str(worker_evidence),
            "--downstream-worker-isolation-evidence-file",
            str(downstream_evidence),
            "--rollback-evidence-file",
            str(rollback_evidence),
        ]
    )

    assert payload["ready_for_production_cutover"] is True
    assert payload["ready_for_production_completion"] is False
    assert payload["internal_event_worker_isolation_evidence"]["ok"] is None
    assert any("internal event worker isolation evidence not provided" in warning for warning in payload["warnings"])


def test_readiness_rejects_missing_public_state_evidence(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    evidence = tmp_path / "pressure.json"
    ingestion_evidence = tmp_path / "ingestion.json"
    processing_evidence = tmp_path / "processing.json"
    worker_evidence = tmp_path / "worker.json"
    internal_event_evidence = tmp_path / "internal-event.json"
    downstream_evidence = tmp_path / "downstream.json"
    rollback_evidence = tmp_path / "rollback.json"
    evidence.write_text(readiness.json.dumps(_pressure_payload()), encoding="utf-8")
    ingestion_evidence.write_text(readiness.json.dumps(_ingestion_evidence_payload()), encoding="utf-8")
    processing_evidence.write_text(readiness.json.dumps(_processing_evidence_payload()), encoding="utf-8")
    worker_evidence.write_text(readiness.json.dumps(_worker_isolation_payload()), encoding="utf-8")
    internal_event_evidence.write_text(readiness.json.dumps(_internal_event_worker_isolation_payload()), encoding="utf-8")
    downstream_evidence.write_text(readiness.json.dumps(_downstream_worker_isolation_payload()), encoding="utf-8")
    rollback_evidence.write_text(readiness.json.dumps(_rollback_evidence_payload()), encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(
        [
            "--nginx-config",
            str(config),
            "--env-file",
            str(tmp_path / "env"),
            "--pressure-evidence-file",
            str(evidence),
            "--ingestion-evidence-file",
            str(ingestion_evidence),
            "--processing-evidence-file",
            str(processing_evidence),
            "--worker-isolation-evidence-file",
            str(worker_evidence),
            "--internal-event-worker-isolation-evidence-file",
            str(internal_event_evidence),
            "--downstream-worker-isolation-evidence-file",
            str(downstream_evidence),
            "--rollback-evidence-file",
            str(rollback_evidence),
        ]
    )

    assert payload["ready_for_production_cutover"] is True
    assert payload["ready_for_production_completion"] is False
    assert payload["public_state_evidence"]["ok"] is None
    assert any("public state evidence not provided" in warning for warning in payload["warnings"])


def test_readiness_rejects_failed_public_state_evidence(monkeypatch, tmp_path) -> None:
    evidence = tmp_path / "public.json"
    evidence.write_text(readiness.json.dumps(_public_state_payload(ok=False)), encoding="utf-8")

    payload = readiness.read_public_state_evidence(str(evidence))

    assert payload["ok"] is False
    assert payload["checks"]["invalid_callback_plain_success"] is False
    assert "public state evidence does not prove" in payload["error"]


def test_readiness_rejects_public_state_without_detail_route_evidence(tmp_path) -> None:
    evidence = tmp_path / "public.json"
    payload_body = _public_state_payload()
    payload_body["admin_webhook_inbox_detail_route_deployed"] = False
    evidence.write_text(readiness.json.dumps(payload_body), encoding="utf-8")

    payload = readiness.read_public_state_evidence(str(evidence))

    assert payload["ok"] is False
    assert payload["checks"]["admin_webhook_inbox_detail_route_deployed"] is False
    assert "public state evidence does not prove" in payload["error"]


def test_readiness_rejects_public_state_without_dual_callback_route_evidence(tmp_path) -> None:
    evidence = tmp_path / "public.json"
    payload_body = _public_state_payload()
    payload_body["callback_route_signals"] = payload_body["callback_route_signals"][:1]
    evidence.write_text(readiness.json.dumps(payload_body), encoding="utf-8")

    payload = readiness.read_public_state_evidence(str(evidence))

    assert payload["ok"] is False
    assert payload["checks"]["dual_callback_route_signal"] is False
    assert "both callback routes" in payload["error"]


def test_readiness_rejects_public_state_with_secondary_callback_quick_ack(tmp_path) -> None:
    evidence = tmp_path / "public.json"
    payload_body = _public_state_payload()
    payload_body["invalid_callback_plain_success"] = True
    payload_body["app_level_callback_signal"] = False
    payload_body["permanent_fix_public_signals_ready"] = False
    payload_body["callback_route_signals"][1]["plain_success"] = True
    payload_body["callback_route_signals"][1]["app_level_callback_signal"] = False
    payload_body["callback_route_signals"][1]["status_code"] = 200
    evidence.write_text(readiness.json.dumps(payload_body), encoding="utf-8")

    payload = readiness.read_public_state_evidence(str(evidence))

    assert payload["ok"] is False
    assert payload["checks"]["invalid_callback_plain_success"] is False
    assert payload["checks"]["app_level_callback_signal"] is False
    assert payload["checks"]["dual_callback_route_signal"] is False
    assert payload["callback_route_checks"]["/api/wecom/events?msg_signature=invalid&timestamp=1&nonce=public-state-probe-api-events"] is False


def test_readiness_accepts_deploy_smoke_evidence(tmp_path) -> None:
    evidence = tmp_path / "deploy-smoke.json"
    evidence.write_text(readiness.json.dumps(_deploy_smoke_payload()), encoding="utf-8")

    payload = readiness.read_deploy_smoke_evidence(str(evidence))

    assert payload["ok"] is True
    assert payload["checks"]["base_urls_distinct"] is True
    assert payload["checks"]["web_health_ok"] is True
    assert payload["checks"]["ingress_durable_ack_ready"] is True
    assert payload["checks"]["admin_detail_route_deployed"] is True


def test_readiness_rejects_deploy_smoke_without_admin_detail_route(tmp_path) -> None:
    evidence = tmp_path / "deploy-smoke.json"
    payload_body = _deploy_smoke_payload()
    payload_body["admin_detail_route_deployed"] = False
    evidence.write_text(readiness.json.dumps(payload_body), encoding="utf-8")

    payload = readiness.read_deploy_smoke_evidence(str(evidence))

    assert payload["ok"] is False
    assert payload["checks"]["admin_detail_route_deployed"] is False
    assert "deploy smoke evidence does not prove" in payload["error"]


def test_readiness_rejects_deploy_smoke_without_distinct_base_urls(tmp_path) -> None:
    evidence = tmp_path / "deploy-smoke.json"
    payload_body = _deploy_smoke_payload()
    payload_body["base_urls_distinct"] = False
    payload_body["web_base_url"] = "https://www.youcangogogo.com"
    payload_body["ingress_base_url"] = "https://www.youcangogogo.com"
    evidence.write_text(readiness.json.dumps(payload_body), encoding="utf-8")

    payload = readiness.read_deploy_smoke_evidence(str(evidence))

    assert payload["ok"] is False
    assert payload["checks"]["base_urls_distinct"] is False
    assert "distinct web/ingress runtimes" in payload["error"]


def test_readiness_rejects_deploy_smoke_without_ingress_callback_routes(tmp_path) -> None:
    evidence = tmp_path / "deploy-smoke.json"
    payload_body = _deploy_smoke_payload()
    payload_body["ingress_callback_routes_ready"] = False
    payload_body["ingress_callback_route_signals"][1]["app_level_callback_signal"] = False
    payload_body["ingress_callback_route_signals"][1]["status_code"] = 404
    evidence.write_text(readiness.json.dumps(payload_body), encoding="utf-8")

    payload = readiness.read_deploy_smoke_evidence(str(evidence))

    assert payload["ok"] is False
    assert payload["checks"]["dual_ingress_callback_route_signal"] is False
    assert payload["ingress_callback_route_checks"]["/api/wecom/events?msg_signature=invalid&timestamp=1&nonce=deploy-smoke-probe-api-events"] is False


def test_readiness_rejects_missing_deploy_smoke_evidence(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    evidence = tmp_path / "pressure.json"
    ingestion_evidence = tmp_path / "ingestion.json"
    processing_evidence = tmp_path / "processing.json"
    worker_evidence = tmp_path / "worker.json"
    internal_event_evidence = tmp_path / "internal-event.json"
    downstream_evidence = tmp_path / "downstream.json"
    rollback_evidence = tmp_path / "rollback.json"
    public_evidence = tmp_path / "public.json"
    evidence.write_text(readiness.json.dumps(_pressure_payload()), encoding="utf-8")
    ingestion_evidence.write_text(readiness.json.dumps(_ingestion_evidence_payload()), encoding="utf-8")
    processing_evidence.write_text(readiness.json.dumps(_processing_evidence_payload()), encoding="utf-8")
    worker_evidence.write_text(readiness.json.dumps(_worker_isolation_payload()), encoding="utf-8")
    internal_event_evidence.write_text(readiness.json.dumps(_internal_event_worker_isolation_payload()), encoding="utf-8")
    downstream_evidence.write_text(readiness.json.dumps(_downstream_worker_isolation_payload()), encoding="utf-8")
    rollback_evidence.write_text(readiness.json.dumps(_rollback_evidence_payload()), encoding="utf-8")
    public_evidence.write_text(readiness.json.dumps(_public_state_payload()), encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(
        [
            "--nginx-config",
            str(config),
            "--env-file",
            str(tmp_path / "env"),
            "--pressure-evidence-file",
            str(evidence),
            "--ingestion-evidence-file",
            str(ingestion_evidence),
            "--processing-evidence-file",
            str(processing_evidence),
            "--worker-isolation-evidence-file",
            str(worker_evidence),
            "--internal-event-worker-isolation-evidence-file",
            str(internal_event_evidence),
            "--downstream-worker-isolation-evidence-file",
            str(downstream_evidence),
            "--rollback-evidence-file",
            str(rollback_evidence),
            "--public-state-evidence-file",
            str(public_evidence),
        ]
    )

    assert payload["ready_for_production_completion"] is False
    assert payload["deploy_smoke_evidence"]["ok"] is None
    assert any("deploy smoke evidence not provided" in warning for warning in payload["warnings"])


def test_readiness_accepts_cutover_state_with_pressure_evidence(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text("nginx", encoding="utf-8")
    evidence = tmp_path / "pressure.json"
    ingestion_evidence = tmp_path / "ingestion.json"
    processing_evidence = tmp_path / "processing.json"
    worker_evidence = tmp_path / "worker.json"
    internal_event_evidence = tmp_path / "internal-event.json"
    downstream_evidence = tmp_path / "downstream.json"
    rollback_evidence = tmp_path / "rollback.json"
    public_evidence = tmp_path / "public.json"
    deploy_smoke_evidence = tmp_path / "deploy-smoke.json"
    evidence.write_text(readiness.json.dumps(_pressure_payload()), encoding="utf-8")
    ingestion_evidence.write_text(readiness.json.dumps(_ingestion_evidence_payload()), encoding="utf-8")
    processing_evidence.write_text(readiness.json.dumps(_processing_evidence_payload()), encoding="utf-8")
    worker_evidence.write_text(readiness.json.dumps(_worker_isolation_payload()), encoding="utf-8")
    internal_event_evidence.write_text(readiness.json.dumps(_internal_event_worker_isolation_payload()), encoding="utf-8")
    downstream_evidence.write_text(readiness.json.dumps(_downstream_worker_isolation_payload()), encoding="utf-8")
    rollback_evidence.write_text(readiness.json.dumps(_rollback_evidence_payload()), encoding="utf-8")
    public_evidence.write_text(readiness.json.dumps(_public_state_payload()), encoding="utf-8")
    deploy_smoke_evidence.write_text(readiness.json.dumps(_deploy_smoke_payload()), encoding="utf-8")
    monkeypatch.setattr(readiness, "_load_env_file", lambda path: True)
    monkeypatch.setattr(
        readiness,
        "run_quick_ack_check",
        lambda argv: {"ok": True, "emergency_quick_ack_enabled": False, "business_processing_suppressed": False},
    )
    monkeypatch.setattr(
        readiness,
        "run_cutover_check",
        lambda argv: {"ok": True, "ready_for_cutover": True},
    )
    monkeypatch.setattr(readiness, "probe_health", lambda url, timeout: _health())
    monkeypatch.setattr(readiness, "read_webhook_inbox_metrics", lambda: _inbox())
    monkeypatch.setattr(readiness, "systemctl_is_active", lambda unit: _service(unit))

    payload = readiness.run(
        [
            "--nginx-config",
            str(config),
            "--env-file",
            str(tmp_path / "env"),
            "--pressure-evidence-file",
            str(evidence),
            "--ingestion-evidence-file",
            str(ingestion_evidence),
            "--processing-evidence-file",
            str(processing_evidence),
            "--worker-isolation-evidence-file",
            str(worker_evidence),
            "--internal-event-worker-isolation-evidence-file",
            str(internal_event_evidence),
            "--downstream-worker-isolation-evidence-file",
            str(downstream_evidence),
            "--rollback-evidence-file",
            str(rollback_evidence),
            "--public-state-evidence-file",
            str(public_evidence),
            "--deploy-smoke-evidence-file",
            str(deploy_smoke_evidence),
        ]
    )

    assert payload["ready_for_production_cutover"] is True
    assert payload["ready_for_production_completion"] is True
    assert payload["ok"] is True
    assert payload["webhook_inbox_health"]["ok"] is True
    assert payload["webhook_ingestion_evidence"]["ok"] is True
    assert payload["webhook_processing_evidence"]["ok"] is True
    assert payload["pressure_evidence"]["ok"] is True
    assert payload["worker_isolation_evidence"]["ok"] is True
    assert payload["internal_event_worker_isolation_evidence"]["ok"] is True
    assert payload["downstream_worker_isolation_evidence"]["ok"] is True
    assert payload["rollback_evidence"]["ok"] is True
    assert payload["public_state_evidence"]["ok"] is True
    assert payload["deploy_smoke_evidence"]["ok"] is True
    assert payload["warnings"] == []
