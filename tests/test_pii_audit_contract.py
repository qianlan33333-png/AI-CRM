from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.responses import Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from aicrm_next.platform.admin_config.pii_audit_repository import AdminConfigPiiAuditRepository
from aicrm_next.main import create_app
from aicrm_next.platform.shared.pii_audit import (
    PiiAuditEvent,
    apply_pii_audit,
    pii_audit_rule,
    pii_audit_enabled,
    set_pii_audit_result_count,
)
from aicrm_next.platform.shared.route_policy import RoutePolicy
from tests.admin_auth_test_helpers import install_admin_session


class RecordingRepository:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[PiiAuditEvent] = []

    def record_pii_access(self, event: PiiAuditEvent) -> None:
        if self.fail:
            raise RuntimeError("audit repository unavailable")
        self.events.append(event)


def _policy(
    *,
    path: str = "/api/admin/customers",
    route_name: str = "list_customers",
    pii_level: str = "customer",
    auth_scheme: str = "oauth_session",
) -> RoutePolicy:
    return RoutePolicy(
        path=path,
        methods=("GET",),
        route_name=route_name,
        audience="admin",
        auth_scheme=auth_scheme,
        capability="read_customer",
        access_scope="global",
        pii_level=pii_level,
        csrf=False,
        rate_limit="authenticated",
    )


def _request(
    policy: RoutePolicy,
    *,
    actor_type: str,
    actor_id: str,
    raw_path: str = "/api/admin/customers?external_userid=raw-sensitive-id",
    request_id: str = "pii-audit-request-001",
) -> Request:
    path, _, query = raw_path.partition("?")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query.encode("utf-8"),
            "headers": [(b"x-request-id", request_id.encode("utf-8"))],
            "client": ("198.51.100.9", 443),
        }
    )
    request.state.route_policy = policy
    request.state.pii_actor_type = actor_type
    request.state.pii_actor_id = actor_id
    request.state.pii_policy_scope = policy.access_scope
    return request


def test_five_principals_are_audited_with_fingerprints_and_no_raw_identifiers() -> None:
    actors = [
        ("user", "admin-user-42"),
        ("internal_service", "automation-internal"),
        ("scoped_service", "external-integration"),
        ("sidebar_owner", "ZhaoYanFang"),
        ("anonymous", "anonymous"),
    ]
    repository = RecordingRepository()

    for actor_type, actor_id in actors:
        request = _request(_policy(), actor_type=actor_type, actor_id=actor_id)
        set_pii_audit_result_count(request, 3)
        response = apply_pii_audit(
            request=request,
            response=Response(status_code=200),
            repository=repository,
            fingerprint_secret=b"pii-audit-fingerprint-secret",
        )
        assert response.status_code == 200

    assert [event.actor_type for event in repository.events] == [item[0] for item in actors]
    assert all(event.result_count == 3 for event in repository.events)
    assert all(event.actor_fingerprint.startswith("hmac-sha256:") for event in repository.events)
    assert len({event.actor_fingerprint for event in repository.events}) == 5
    rendered = json.dumps([event.to_dict() for event in repository.events], ensure_ascii=False, sort_keys=True)
    for _actor_type, actor_id in actors:
        if actor_id != "anonymous":
            assert actor_id not in rendered
    assert "raw-sensitive-id" not in rendered
    assert "external_userid" not in rendered


def test_rejected_sensitive_request_is_audited_without_request_or_response_body() -> None:
    class BodyTrapResponse:
        status_code = 403
        headers: dict[str, str] = {}

        @property
        def body(self):
            raise AssertionError("PII audit must not inspect response bodies")

    repository = RecordingRepository()
    request = _request(_policy(pii_level="sensitive"), actor_type="anonymous", actor_id="anonymous")

    response = apply_pii_audit(
        request=request,
        response=BodyTrapResponse(),  # type: ignore[arg-type]
        repository=repository,
        fingerprint_secret=b"pii-audit-fingerprint-secret",
    )

    assert response.status_code == 403
    assert len(repository.events) == 1
    assert repository.events[0].status_code == 403
    assert repository.events[0].result_count == 0


def test_pii_request_id_is_fingerprinted_when_client_supplies_an_identifier() -> None:
    repository = RecordingRepository()
    raw_request_id = "13800138000"
    request = _request(
        _policy(),
        actor_type="user",
        actor_id="admin-user-42",
        request_id=raw_request_id,
    )

    apply_pii_audit(
        request=request,
        response=Response(status_code=200),
        repository=repository,
        fingerprint_secret=b"pii-audit-fingerprint-secret",
    )

    assert repository.events[0].request_id.startswith("hmac-sha256:")
    assert raw_request_id not in json.dumps(repository.events[0].to_dict(), sort_keys=True)


def test_production_cannot_disable_pii_audit_with_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("AICRM_PII_AUDIT_ENABLED", "false")

    assert pii_audit_enabled() is True


def test_server_declared_export_purpose_fails_closed_when_durable_audit_fails() -> None:
    policy = _policy(
        path="/api/admin/questionnaires/{questionnaire_id}/export",
        route_name="export_questionnaire",
        pii_level="sensitive",
    )
    rule = pii_audit_rule(policy)
    request = _request(policy, actor_type="user", actor_id="admin-user-42")
    set_pii_audit_result_count(request, 8)

    response = apply_pii_audit(
        request=request,
        response=Response(content=b"raw-response-body-sentinel", status_code=200, media_type="text/csv"),
        repository=RecordingRepository(fail=True),
        fingerprint_secret=b"pii-audit-fingerprint-secret",
    )

    assert rule.purpose == "pii_export"
    assert rule.fail_closed is True
    assert response.status_code == 503
    assert b"raw-response-body-sentinel" not in response.body
    assert json.loads(response.body)["error"] == "pii_audit_unavailable"


def test_non_high_risk_customer_read_remains_available_when_audit_store_fails() -> None:
    policy = _policy()
    request = _request(policy, actor_type="user", actor_id="admin-user-42")
    original = Response(content=b"safe-response", status_code=200)

    response = apply_pii_audit(
        request=request,
        response=original,
        repository=RecordingRepository(fail=True),
        fingerprint_secret=b"pii-audit-fingerprint-secret",
    )

    assert pii_audit_rule(policy).fail_closed is False
    assert response is original


def test_admin_config_pii_audit_repository_persists_only_safe_event_fields(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'pii-audit.sqlite3'}", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE admin_operation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operator TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
    event = PiiAuditEvent(
        actor_type="user",
        actor_fingerprint="hmac-sha256:actor-safe",
        purpose="pii_export",
        policy_scope="global",
        pii_level="sensitive",
        result_count=2,
        route_name="export_questionnaire",
        status_code=200,
        request_id="pii-audit-request-001",
        resource_fingerprint="hmac-sha256:resource-safe",
    )

    AdminConfigPiiAuditRepository(engine=engine).record_pii_access(event)

    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    """
                SELECT operator, action_type, target_type, target_id, before_json, after_json
                FROM admin_operation_logs
                """
                )
            )
            .mappings()
            .one()
        )
    assert row["operator"] == event.actor_fingerprint
    assert row["action_type"] == "pii_access"
    assert row["target_type"] == event.route_name
    assert row["target_id"] == event.resource_fingerprint
    assert json.loads(row["before_json"]) == {}
    assert json.loads(row["after_json"])["purpose"] == "pii_export"


def test_export_endpoint_sets_result_count_without_middleware_body_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = RecordingRepository()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_PII_AUDIT_ENABLED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(
        create_app(pii_audit_repository=repository),
        raise_server_exceptions=False,
    )

    response = client.get("/api/admin/class-user-management/export")

    assert response.status_code == 200
    event = next(event for event in repository.events if event.route_name == "class_user_management_export")
    assert event.purpose == "pii_export"
    assert event.result_count == 1


def test_denied_admin_export_is_audited_with_admin_or_anonymous_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = RecordingRepository()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_PII_AUDIT_ENABLED", "true")
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(pii_audit_repository=repository), raise_server_exceptions=False)

    anonymous = client.get("/api/admin/class-user-management/export")
    install_admin_session(client, "viewer", subject="admin:pii-viewer")
    viewer = client.post("/api/admin/class-user-management/export", json={})

    assert anonymous.status_code == 401
    assert viewer.status_code == 403
    statuses = [(event.actor_type, event.status_code) for event in repository.events]
    assert ("anonymous", 401) in statuses
    assert ("human", 403) in statuses
