from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlparse

import jwt
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.radar.radar_links.application import CreateRadarLinkCommand
from aicrm_next.extensions.radar.radar_links.dto import RadarLinkCreateRequest
from aicrm_next.extensions.radar.radar_links.repo import PostgresRadarLinksRepository, build_radar_links_repository
from tests.admin_auth_test_helpers import TEST_JWT_KEY, access_token_headers, install_access_token


WECHAT_UA = "Mozilla/5.0 MicroMessenger/8.0.49"


class RecordingAuditRepository:
    def __init__(self) -> None:
        self.events = []

    def record_pii_access(self, event) -> None:
        self.events.append(event)


def _client(
    monkeypatch,
    *,
    authorized: bool = True,
    capabilities: tuple[str, ...] = ("external_read",),
    audit_repository=None,
) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("AICRM_NEXT_WECHAT_OAUTH_MODE", "fake")
    monkeypatch.setenv("SECRET_KEY", "external-radar-api-test")
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    client = TestClient(create_app(pii_audit_repository=audit_repository), raise_server_exceptions=False, base_url="https://testserver")
    token = install_access_token(
        client,
        audience="external_integration",
        capabilities=capabilities,
        scopes=("read",),
        client_id="pytest-external-radar",
        purpose="external_agent",
    )
    if authorized:
        client.headers.update(access_token_headers(token))
    return client


def _create_link(*, title: str = "外部接口雷达", enabled: bool = True) -> dict:
    return CreateRadarLinkCommand()(
        RadarLinkCreateRequest(
            title=title,
            original_url="https://example.com/radar-target",
            enabled=enabled,
            auth_required=True,
        ),
        base_url="https://testserver",
    )["radar_link"]


def _oauth_state(client: TestClient, code: str) -> str:
    response = client.get(
        f"/r/{code}",
        headers={"User-Agent": WECHAT_UA},
        follow_redirects=False,
    )
    assert response.status_code == 302
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def _authorize(client: TestClient, code: str, **identity: str) -> None:
    response = client.get(
        "/api/h5/radar/oauth/callback",
        params={"state": _oauth_state(client, code), **identity},
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text


def _expired(token: str) -> str:
    claims = jwt.decode(token, TEST_JWT_KEY, algorithms=["HS256"], options={"verify_signature": True, "verify_aud": False})
    claims["iat"] = 1
    claims["exp"] = 2
    return jwt.encode(claims, TEST_JWT_KEY, algorithm="HS256")


def test_external_radar_routes_require_registered_external_read_token(monkeypatch) -> None:
    client = _client(monkeypatch, authorized=False)

    for path in ("/api/external/radar-clicks", "/api/external/radar-links"):
        missing = client.get(path)
        assert missing.status_code == 401
        assert missing.json()["error"] == "access_token_required"

        invalid = client.get(path, headers={"Authorization": "Bearer wrong-token"})
        assert invalid.status_code == 401
        assert invalid.json()["error"] == "invalid_access_token"

    token = install_access_token(
        client,
        audience="external_integration",
        capabilities=("external_read",),
        scopes=("read",),
        client_id="pytest-external-radar-expired",
        purpose="external_agent",
    )
    expired = client.get("/api/external/radar-clicks", headers=access_token_headers(_expired(token)))
    assert expired.status_code == 401
    assert expired.json()["error"] == "access_token_expired"

    denied = _client(monkeypatch, capabilities=("health_read",)).get("/api/external/radar-clicks")
    assert denied.status_code == 403
    assert denied.json()["error"] == "scope_or_capability_required"


def test_external_radar_clicks_return_one_logical_click_for_first_authorized_open(monkeypatch) -> None:
    client = _client(monkeypatch)
    link = _create_link()
    _authorize(client, link["code"], unionid="unionid_001")

    response = client.get("/api/external/radar-clicks")
    payload = response.json()

    assert response.status_code == 200, response.text
    assert payload["ok"] is True
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["source_status"] == "external_radar_clicks"
    assert payload["fallback_used"] is False
    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0] == {
        "event_id": payload["items"][0]["event_id"],
        "mobile": "13800138000",
        "unionid": "unionid_001",
        "radar_id": link["id"],
        "radar_code": link["code"],
        "clicked_at": payload["items"][0]["clicked_at"],
        "identity_status": "complete",
        "identity_matched_by": "unionid",
    }


def test_external_radar_clicks_count_signed_session_reopen_without_internal_stage_duplicates(monkeypatch) -> None:
    client = _client(monkeypatch)
    link = _create_link()
    _authorize(client, link["code"], unionid="unionid_001")

    reopen = client.get(f"/r/{link['code']}", follow_redirects=False)
    assert reopen.status_code == 302
    assert reopen.headers["location"] == "https://example.com/radar-target"

    payload = client.get(f"/api/external/radar-clicks?radar_code={link['code']}").json()
    assert payload["total"] == 2
    assert len(payload["items"]) == 2
    assert {item["identity_status"] for item in payload["items"]} == {"complete"}


def test_external_radar_clicks_preserve_incomplete_and_conflicting_trusted_identities(monkeypatch) -> None:
    client = _client(monkeypatch)
    missing_mobile = _create_link(title="缺手机号")
    unresolved = _create_link(title="未归一")
    conflict = _create_link(title="冲突")
    _authorize(client, missing_mobile["code"], unionid="unionid_without_mobile")
    repo = build_radar_links_repository()
    repo.record_click_event(
        {"link_id": unresolved["id"], "code": unresolved["code"], "stage": "authorized", "openid": "openid_unresolved"}
    )
    repo.record_click_event(
        {"link_id": conflict["id"], "code": conflict["code"], "stage": "authorized", "openid": "openid_conflict"}
    )

    items = client.get("/api/external/radar-clicks").json()["items"]
    by_code = {item["radar_code"]: item for item in items}

    assert by_code[missing_mobile["code"]]["unionid"] == "unionid_without_mobile"
    assert by_code[missing_mobile["code"]]["mobile"] == ""
    assert by_code[missing_mobile["code"]]["identity_status"] == "mobile_missing"
    assert by_code[unresolved["code"]]["unionid"] == ""
    assert by_code[unresolved["code"]]["mobile"] == ""
    assert by_code[unresolved["code"]]["identity_status"] == "unresolved"
    assert by_code[conflict["code"]]["unionid"] == ""
    assert by_code[conflict["code"]]["mobile"] == ""
    assert by_code[conflict["code"]]["identity_status"] == "conflict"


def test_external_radar_click_filters_and_timestamp_validation(monkeypatch) -> None:
    client = _client(monkeypatch)
    first = _create_link(title="第一条")
    second = _create_link(title="第二条")
    _authorize(client, first["code"], unionid="unionid_001")
    _authorize(client, second["code"], unionid="unionid_without_mobile")

    by_mobile = client.get("/api/external/radar-clicks?mobile=13800138000").json()
    assert [item["radar_code"] for item in by_mobile["items"]] == [first["code"]]

    by_unionid = client.get("/api/external/radar-clicks?unionid=unionid_without_mobile").json()
    assert [item["radar_code"] for item in by_unionid["items"]] == [second["code"]]

    by_radar = client.get(f"/api/external/radar-clicks?radar_id={first['id']}&radar_code={first['code']}").json()
    assert [item["radar_id"] for item in by_radar["items"]] == [first["id"]]

    milliseconds = client.get("/api/external/radar-clicks?clicked_from=1779235200000")
    assert milliseconds.status_code == 400
    assert milliseconds.json()["error_code"] == "invalid_request"

    inverted = client.get("/api/external/radar-clicks?clicked_from=1779235200&clicked_to=1")
    assert inverted.status_code == 400
    assert inverted.json()["error_code"] == "invalid_request"

    for invalid_query in ("clicked_from=not-a-number", "radar_id=zero", "limit=0", "limit=501"):
        invalid = client.get(f"/api/external/radar-clicks?{invalid_query}")
        assert invalid.status_code == 400
        assert invalid.json()["error_code"] == "invalid_request"

    invalid_mapping = client.get("/api/external/radar-links?radar_id=zero")
    assert invalid_mapping.status_code == 400
    assert invalid_mapping.json()["error_code"] == "invalid_request"

    empty = client.get("/api/external/radar-clicks?unionid=not_found").json()
    assert empty["items"] == []
    assert empty["total"] == 0


def test_external_radar_click_cursor_is_keyset_stable_when_new_event_arrives(monkeypatch) -> None:
    client = _client(monkeypatch)
    link = _create_link()
    _authorize(client, link["code"], unionid="unionid_001")
    client.get(f"/r/{link['code']}", follow_redirects=False)

    first = client.get("/api/external/radar-clicks?limit=1").json()
    assert first["has_more"] is True
    assert first["next_cursor"]
    first_event_id = first["items"][0]["event_id"]

    client.get(f"/r/{link['code']}", follow_redirects=False)
    second = client.get(f"/api/external/radar-clicks?limit=1&cursor={first['next_cursor']}").json()

    assert second["items"]
    assert second["items"][0]["event_id"] < first_event_id
    assert second["items"][0]["event_id"] != first_event_id


def test_external_radar_link_mapping_is_minimal_and_includes_disabled_links(monkeypatch) -> None:
    client = _client(monkeypatch)
    enabled = _create_link(title="启用雷达")
    disabled = _create_link(title="停用雷达", enabled=False)

    payload = client.get("/api/external/radar-links?limit=1").json()
    assert payload["ok"] is True
    assert payload["total"] == 2
    assert payload["has_more"] is True
    assert payload["next_cursor"]
    assert set(payload["items"][0]) == {"radar_id", "radar_code", "title"}

    by_code = client.get(f"/api/external/radar-links?radar_code={disabled['code']}").json()
    assert by_code["items"] == [{"radar_id": disabled["id"], "radar_code": disabled["code"], "title": "停用雷达"}]

    by_id = client.get(f"/api/external/radar-links?radar_id={enabled['id']}").json()
    assert by_id["items"] == [{"radar_id": enabled["id"], "radar_code": enabled["code"], "title": "启用雷达"}]


def test_external_radar_cursor_rejects_malformed_payload(monkeypatch) -> None:
    client = _client(monkeypatch)
    malformed = base64.urlsafe_b64encode(json.dumps({"offset": 1}).encode()).decode().rstrip("=")

    clicks = client.get(f"/api/external/radar-clicks?cursor={malformed}")
    links = client.get(f"/api/external/radar-links?cursor={malformed}")

    assert clicks.status_code == 400
    assert clicks.json()["error_code"] == "invalid_request"
    assert links.status_code == 400
    assert links.json()["error_code"] == "invalid_request"

    malformed_base64 = client.get("/api/external/radar-clicks?cursor=%25%25%25")
    assert malformed_base64.status_code == 400
    assert malformed_base64.json()["error_code"] == "invalid_request"


def test_external_radar_clicks_record_sensitive_pii_result_count(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_PII_AUDIT_ENABLED", "true")
    repository = RecordingAuditRepository()
    client = _client(monkeypatch, audit_repository=repository)
    link = _create_link()
    _authorize(client, link["code"], unionid="unionid_001")

    response = client.get("/api/external/radar-clicks")
    assert response.status_code == 200

    event = next(item for item in repository.events if item.route_name == "list_external_radar_clicks")
    assert event.pii_level == "sensitive"
    assert event.result_count == 1


def test_production_click_projection_is_one_batch_and_fails_closed_on_identity_conflict(monkeypatch) -> None:
    class Rows:
        def fetchall(self):
            return [
                {
                    "event_id": 9,
                    "mobile": "",
                    "unionid": "",
                    "radar_id": 3,
                    "radar_code": "radar_3",
                    "clicked_at": "2026-07-15T08:30:12+00:00",
                    "identity_status": "conflict",
                    "identity_matched_by": "",
                    "total": 1,
                }
            ]

    class Connection:
        def __init__(self) -> None:
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def execute(self, sql, params):
            self.calls.append((sql, params))
            return Rows()

    connection = Connection()
    repository = PostgresRadarLinksRepository("postgresql://fixture")
    monkeypatch.setattr(repository, "_connect", lambda: connection)

    items, total, has_more = repository.list_external_clicks(limit=100)

    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    assert "JOIN radar_links" in sql
    assert "FROM crm_user_identity" in sql
    assert "resolution.active_candidate_count <> 1" in sql
    assert "COUNT(*) FILTER" in sql
    assert params["limit"] == 101
    assert items[0]["identity_status"] == "conflict"
    assert items[0]["mobile"] == ""
    assert items[0]["unionid"] == ""
    assert total == 1
    assert has_more is False
