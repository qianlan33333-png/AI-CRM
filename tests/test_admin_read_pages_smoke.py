from __future__ import annotations

import json
import os

from sqlalchemy import text

from aicrm_next.platform.platform_foundation.auth_platform.credentials import CredentialHasher
from aicrm_next.platform.platform_foundation.auth_platform.repository import PostgresAuthRepository
from aicrm_next.platform.platform_foundation.auth_platform.sessions import AuthSessionService
from aicrm_next.platform.shared.db_session import get_session_factory
from scripts.ops import check_admin_read_pages_smoke as smoke
from scripts.ops import create_deploy_smoke_session as deploy_session


def test_production_smoke_timeout_covers_observed_cold_admin_reads() -> None:
    assert smoke.DEFAULT_TIMEOUT_SECONDS == 45.0


def test_full_sidebar_smoke_covers_every_registered_navigation_destination(monkeypatch) -> None:
    monkeypatch.setattr(smoke, "_openapi_paths", lambda *args, **kwargs: set(smoke.REQUIRED_OPENAPI_PATHS))
    observed_paths: list[str] = []

    def _healthy_probe(*args, **kwargs):
        observed_paths.append(str(args[1]))
        return smoke.ProbeResult(path=str(args[1]), status_code=200, ok=True, duration_ms=1)

    monkeypatch.setattr(smoke, "_probe", _healthy_probe)

    payload = smoke.run(
        "http://127.0.0.1:5001",
        timeout=1,
        include_all_sidebar=True,
        require_all_data_health_green=True,
    )

    assert payload["ok"] is True
    assert payload["all_sidebar_required"] is True
    assert payload["all_data_health_green_required"] is True
    assert payload["sidebar_path_count"] == len(smoke.SIDEBAR_PATHS)
    assert set(smoke.SIDEBAR_PATHS).issubset(observed_paths)
    assert "/admin/automation-agents" in observed_paths
    assert smoke.DATA_HEALTH_SUMMARY_PATH in observed_paths
    assert smoke.DATA_HEALTH_SUMMARY_PATH in smoke.REQUIRED_OPENAPI_PATHS


def test_non_production_smoke_does_not_require_data_health_summary(monkeypatch) -> None:
    monkeypatch.setattr(smoke, "_openapi_paths", lambda *args, **kwargs: set(smoke.REQUIRED_OPENAPI_PATHS))
    observed_paths: list[str] = []

    def _healthy_probe(*args, **kwargs):
        observed_paths.append(str(args[1]))
        return smoke.ProbeResult(path=str(args[1]), status_code=200, ok=True, duration_ms=1)

    monkeypatch.setattr(smoke, "_probe", _healthy_probe)

    payload = smoke.run("http://127.0.0.1:5001", timeout=1)

    assert payload["ok"] is True
    assert payload["all_data_health_green_required"] is False
    assert smoke.DATA_HEALTH_SUMMARY_PATH not in observed_paths


def test_run_fails_when_required_admin_cookie_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        smoke,
        "_admin_cookie_header",
        lambda _path: ("", "admin_cookie_file_failed:FileNotFoundError"),
    )
    monkeypatch.setattr(smoke, "_openapi_paths", lambda *args, **kwargs: set(smoke.REQUIRED_OPENAPI_PATHS))
    monkeypatch.setattr(
        smoke,
        "_probe",
        lambda *args, **kwargs: smoke.ProbeResult(
            path=kwargs.get("path", "/admin/customers"),
            status_code=200,
            ok=True,
            duration_ms=1,
            body_prefix="{}",
        ),
    )

    payload = smoke.run("http://127.0.0.1:5001", timeout=1, require_admin_cookie=True)

    assert payload["ok"] is False
    assert payload["admin_cookie_supplied"] is False
    assert payload["admin_cookie_required"] is True
    assert payload["admin_cookie_error"] == "admin_cookie_file_failed:FileNotFoundError"
    assert ":admin_cookie_missing" in payload["failed_paths"]


def test_admin_cookie_file_must_be_private(tmp_path) -> None:
    cookie_file = tmp_path / "admin-cookie"
    cookie_file.write_text("aicrm_next_admin_session=ss_fake", encoding="utf-8")
    cookie_file.chmod(0o644)

    header, error = smoke._admin_cookie_header(cookie_file)

    assert header == ""
    assert error == "admin_cookie_file_failed:PermissionError"


def test_deploy_smoke_session_issue_use_and_revoke_chain(tmp_path, next_pg_schema, monkeypatch) -> None:
    database_url = os.environ["DATABASE_URL"]
    pepper = "deploy-smoke-test-pepper-at-least-32-bytes"
    with get_session_factory().begin() as session:
        admin_user_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO admin_users (
                        wecom_userid, wecom_corpid, display_name, is_active,
                        login_enabled, admin_level, session_version
                    ) VALUES (
                        'pytest-deploy-smoke', 'corp-pytest', 'Deploy smoke', TRUE,
                        TRUE, 'super_admin', 3
                    )
                    RETURNING id
                    """
                )
            ).scalar_one()
        )
    cookie_file = tmp_path / "deploy-smoke-cookie"

    issue_report = deploy_session.issue_deploy_smoke_session(
        database_url=database_url,
        pepper=pepper,
        output_file=cookie_file,
        ttl_seconds=60,
    )
    cookie_header, cookie_error = smoke._admin_cookie_header(cookie_file)
    session_cookie = deploy_session._read_session_cookie(cookie_file)
    service = AuthSessionService(PostgresAuthRepository(database_url=database_url), CredentialHasher(pepper))
    introspection = service.introspect(session_cookie)

    assert issue_report["ok"] is True
    assert issue_report["ttl_seconds"] == 60
    assert "ss_" not in json.dumps(issue_report)
    assert cookie_error == ""
    assert cookie_header.startswith("aicrm_next_admin_session=ss_")
    assert introspection.active is True
    assert introspection.context is not None
    assert introspection.context.admin_user_id == str(admin_user_id)
    captured_headers: list[str] = []
    monkeypatch.setattr(smoke, "_openapi_paths", lambda *args, **kwargs: set(smoke.REQUIRED_OPENAPI_PATHS))

    def _healthy_probe(*args, **kwargs):
        captured_headers.append(str(kwargs.get("cookie_header") or ""))
        return smoke.ProbeResult(
            path=str(args[1]),
            status_code=200,
            ok=True,
            duration_ms=1,
            body_prefix="{}",
        )

    monkeypatch.setattr(smoke, "_probe", _healthy_probe)
    smoke_report = smoke.run(
        "http://127.0.0.1:5001",
        timeout=1,
        require_admin_cookie=True,
        admin_cookie_file=cookie_file,
    )

    assert smoke_report["ok"] is True
    assert captured_headers == [cookie_header] * len(smoke.SMOKE_PATHS)
    revoke_report = deploy_session.revoke_deploy_smoke_session(
        database_url=database_url,
        pepper=pepper,
        cookie_file=cookie_file,
    )
    assert revoke_report == {
        "ok": True,
        "action": "revoke",
        "revoked": True,
        "credential_printed": False,
    }
    assert not cookie_file.exists()
    assert service.introspect(session_cookie).active is False


def test_probe_rejects_protected_api_unauthorized_when_cookie_supplied(monkeypatch) -> None:
    monkeypatch.setattr(smoke, "_fetch", lambda *args, **kwargs: (401, {}, '{"ok":false}'))

    result = smoke._probe(
        "http://127.0.0.1:5001",
        "/api/admin/automation-agents",
        timeout=1,
        cookie_header="aicrm_next_admin_session=fake",
    )

    assert result.ok is False
    assert result.error == "admin_cookie_rejected:401"


def test_probe_rejects_admin_login_page_when_cookie_supplied(monkeypatch) -> None:
    monkeypatch.setattr(smoke, "_fetch", lambda *args, **kwargs: (200, {}, "<title>后台登录 · 客户管理后台</title>"))

    result = smoke._probe(
        "http://127.0.0.1:5001",
        "/admin/automation-agents",
        timeout=1,
        cookie_header="aicrm_next_admin_session=fake",
    )

    assert result.ok is False
    assert result.error == "admin_login_page_returned"


def test_probe_rejects_degraded_admin_api_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        smoke,
        "_fetch",
        lambda *args, **kwargs: (
            200,
            {},
            '{"ok":true,"degraded":true,"error_code":"production_read_unavailable","read_model_status":"unavailable"}',
        ),
    )

    result = smoke._probe(
        "http://127.0.0.1:5001",
        "/api/admin/internal-events?limit=1",
        timeout=1,
        cookie_header="aicrm_next_admin_session=fake",
    )

    assert result.ok is False
    assert result.error == "admin_api_degraded:production_read_unavailable"


def test_probe_accepts_healthy_empty_admin_api_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        smoke,
        "_fetch",
        lambda *args, **kwargs: (
            200,
            {},
            '{"ok":true,"degraded":false,"read_model_status":"primary","items":[],"total":0}',
        ),
    )

    result = smoke._probe(
        "http://127.0.0.1:5001",
        "/api/admin/internal-events?limit=1",
        timeout=1,
        cookie_header="aicrm_next_admin_session=fake",
    )

    assert result.ok is True
    assert result.error == ""


def test_probe_rejects_data_health_summary_with_failed_check(monkeypatch) -> None:
    checks = [{"check_id": f"check_{index}", "status": "ok"} for index in range(14)]
    checks.append({"check_id": "failed_check", "status": "fail"})
    monkeypatch.setattr(
        smoke,
        "_fetch",
        lambda *args, **kwargs: (
            200,
            {},
            json.dumps(
                {
                    "ok": False,
                    "overall_status": "fail",
                    "counts": {"ok": 14, "warn": 0, "fail": 1, "not_applicable": 0},
                    "checks": checks,
                }
            ),
        ),
    )

    result = smoke._probe(
        "http://127.0.0.1:5001",
        smoke.DATA_HEALTH_SUMMARY_PATH,
        timeout=1,
        cookie_header="aicrm_next_admin_session=fake",
    )

    assert result.ok is False
    assert result.error == "data_health_checks_not_all_ok:failed_check:fail"


def test_probe_accepts_exactly_sixteen_green_data_health_checks(monkeypatch) -> None:
    observed_max_bytes: list[int] = []
    checks = [
        {"check_id": f"check_{index}", "status": "ok"}
        for index in range(smoke.EXPECTED_DATA_HEALTH_CHECK_COUNT)
    ]

    def _healthy_data_health(*args, **kwargs):
        observed_max_bytes.append(int(kwargs["max_bytes"]))
        return (
            200,
            {},
            json.dumps(
                {
                    "ok": True,
                    "overall_status": "ok",
                    "counts": {"ok": 16, "warn": 0, "fail": 0, "not_applicable": 0},
                    "checks": checks,
                }
            ),
        )

    monkeypatch.setattr(smoke, "_fetch", _healthy_data_health)

    result = smoke._probe(
        "http://127.0.0.1:5001",
        smoke.DATA_HEALTH_SUMMARY_PATH,
        timeout=1,
        cookie_header="aicrm_next_admin_session=fake",
    )

    assert result.ok is True
    assert result.error == ""
    assert observed_max_bytes == [smoke.DATA_HEALTH_RESPONSE_MAX_BYTES]
