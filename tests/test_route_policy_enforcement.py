from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from aicrm_next.admin_auth import route_policy as route_policy_module
from aicrm_next.admin_auth.route_policy import (
    RouteRateLimiter,
    _csrf_error,
    _enforce_payment_identity_session,
)
from aicrm_next.admin_auth.service import CSRF_COOKIE, SESSION_COOKIE
from aicrm_next.main import create_app
from aicrm_next.extensions.commerce.public_product import h5_wechat_pay
from aicrm_next.shared.route_policy import RoutePolicy
from aicrm_next.shared.wechat_h5_session import WECHAT_PAYMENT_IDENTITY_COOKIE
from tests.admin_auth_test_helpers import access_token_headers, install_access_token, install_admin_session
from tests.sidebar_auth_test_helpers import install_sidebar_auth, install_sidebar_viewer_session


@pytest.fixture()
def enforced_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)
    client.app.state.test_access_tokens = {
        "mcp": install_access_token(
            client,
            audience="external_integration",
            capabilities=("mcp_read", "mcp_execute"),
            scopes=("read", "write"),
            client_id="pytest-mcp",
        ),
        "identity": install_access_token(
            client,
            audience="external_integration",
            capabilities=("identity_resolve",),
            scopes=("read",),
            client_id="pytest-identity",
        ),
    }
    return client


def _admin_client(monkeypatch: pytest.MonkeyPatch, *roles: str) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)
    install_admin_session(client, *roles)
    return client


def test_mcp_and_identity_resolve_require_internal_service_token(enforced_client: TestClient) -> None:
    missing_mcp = enforced_client.get("/mcp")
    missing_identity = enforced_client.get("/api/identity/resolve?external_userid=wx_ext_001")

    assert missing_mcp.status_code == 401
    assert missing_mcp.json()["error"] == "access_token_required"
    assert missing_identity.status_code == 401

    assert (
        enforced_client.get(
            "/mcp",
            headers=access_token_headers(enforced_client.app.state.test_access_tokens["mcp"]),
        ).status_code
        == 200
    )
    resolved = enforced_client.get(
        "/api/identity/resolve?external_userid=wx_ext_001",
        headers=access_token_headers(enforced_client.app.state.test_access_tokens["identity"]),
    )
    assert resolved.status_code == 200
    assert resolved.json()["identity"]["unionid"] == "unionid_001"


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("post", "/api/h5/wechat-pay/jsapi/orders"),
        ("get", "/api/h5/wechat-pay/orders/WXP_NO_IDENTITY"),
        ("post", "/api/h5/service-period-products/demo/wechat-pay/jsapi/orders"),
        ("get", "/api/h5/coupons/available?target_ref=opaque"),
        ("post", "/api/h5/coupons/demo/claim"),
    ),
)
def test_payment_and_coupon_self_service_routes_require_payment_identity_before_endpoint(
    enforced_client: TestClient,
    method: str,
    path: str,
) -> None:
    response = getattr(enforced_client, method)(path)

    assert response.status_code == 401
    assert response.json()["error"] == "payment_identity_required"


def _payment_identity_request(cookie_value: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/h5/coupons/available",
            "query_string": b"",
            "headers": [
                (
                    b"cookie",
                    f"{WECHAT_PAYMENT_IDENTITY_COOKIE}={cookie_value}".encode("ascii"),
                )
            ],
            "client": ("127.0.0.1", 12345),
        }
    )


def _payment_identity_policy() -> RoutePolicy:
    return RoutePolicy(
        path="/api/h5/coupons/available",
        methods=("GET",),
        route_name="api.h5_available_coupons",
        audience="public_h5",
        auth_scheme="payment_identity_session",
        capability="coupon_available_read",
        access_scope="self",
        pii_level="financial",
        csrf=False,
        rate_limit="public_strict",
        principal_types=("public",),
    )


def test_valid_payment_identity_installs_hashed_public_principal(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    token = h5_wechat_pay._signed_blob(
        {"openid": "openid-route-policy", "unionid": "unionid-route-policy"}
    )
    request = _payment_identity_request(token)

    assert _enforce_payment_identity_session(request, _payment_identity_policy()) is None
    assert request.state.payment_identity["openid"] == "openid-route-policy"
    assert request.state.auth_context.principal_id.startswith("wechat-payment:")
    assert "openid-route-policy" not in request.state.auth_context.principal_id
    assert request.state.pii_actor_id == request.state.auth_context.principal_id


@pytest.mark.parametrize("tamper", [False, True])
def test_expired_or_tampered_payment_identity_is_rejected(monkeypatch, tamper: bool) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    now = int(datetime.now(timezone.utc).timestamp())
    token = h5_wechat_pay._signed_blob(
        {
            "openid": "openid-invalid-route-policy",
            "iat": now - (60 if tamper else 600),
            "exp": now + 3600 if tamper else now - 1,
        }
    )
    if tamper:
        token = token[:-1] + ("0" if token[-1] != "0" else "1")

    response = _enforce_payment_identity_session(
        _payment_identity_request(token),
        _payment_identity_policy(),
    )

    assert response is not None
    assert response.status_code == 401


def test_mcp_accepts_its_scoped_service_token_without_granting_identity_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    client = TestClient(create_app(), raise_server_exceptions=False)
    token = install_access_token(
        client,
        audience="external_integration",
        capabilities=("mcp_read", "mcp_execute"),
        scopes=("read", "write"),
        client_id="pytest-mcp-only",
    )
    headers = access_token_headers(token)

    assert client.get("/mcp", headers=headers).status_code == 200
    identity = client.get("/api/identity/resolve?external_userid=wx_ext_001", headers=headers)
    assert identity.status_code == 403
    assert identity.json()["error"] == "client_purpose_forbidden"


def test_sidebar_customer_routes_require_signed_owner_context(enforced_client: TestClient) -> None:
    missing = enforced_client.get("/api/sidebar/profile?external_userid=wx_ext_001")
    assert missing.status_code == 401
    assert missing.json()["error"] == "sidebar_context_required"

    headers = install_sidebar_auth(
        enforced_client,
        viewer_userid="ZhaoYanFang",
        external_userid="wx_ext_001",
    )
    allowed = enforced_client.get(
        "/api/sidebar/profile?external_userid=wx_ext_001&owner_userid=ZhaoYanFang",
        headers=headers,
    )
    assert allowed.status_code == 200
    assert allowed.json()["route_owner"] == "ai_crm_next"

    cross_owner = enforced_client.get(
        "/api/sidebar/profile?external_userid=wx_ext_001&owner_userid=LiuXiao",
        headers=headers,
    )
    assert cross_owner.status_code == 403
    assert cross_owner.json()["error"] == "sidebar_owner_scope_forbidden"


def test_sidebar_write_uses_signed_owner_and_rejects_body_impersonation(enforced_client: TestClient) -> None:
    headers = install_sidebar_auth(
        enforced_client,
        viewer_userid="ZhaoYanFang",
        external_userid="wx_ext_001",
    )
    headers["Idempotency-Key"] = "route-policy-sidebar-write"

    rejected = enforced_client.post(
        "/api/sidebar/lead-pool/upsert-class-term",
        headers=headers,
        json={
            "external_userid": "wx_ext_001",
            "owner_userid": "LiuXiao",
            "class_term": "term-2026-07",
            "status": "active",
        },
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"] == "sidebar_owner_scope_forbidden"

    allowed = enforced_client.post(
        "/api/sidebar/lead-pool/upsert-class-term",
        headers=headers,
        json={
            "external_userid": "wx_ext_001",
            "class_term": "term-2026-07",
            "status": "active",
        },
    )
    assert allowed.status_code == 200
    assert allowed.json()["ok"] is True


def test_sidebar_context_rejects_cross_customer_query_token_and_new_session_replay(
    enforced_client: TestClient,
) -> None:
    headers = install_sidebar_auth(
        enforced_client,
        viewer_userid="ZhaoYanFang",
        external_userid="wx_ext_001",
        session_id="original-session",
    )
    token = headers["X-AICRM-Sidebar-Owner-Token"]

    cross_customer = enforced_client.get(
        "/api/sidebar/profile?external_userid=wx_ext_002",
        headers=headers,
    )
    query_token = enforced_client.get(
        "/api/sidebar/profile?external_userid=wx_ext_001",
        params={"sidebar_owner_token": token},
    )
    enforced_client.cookies.clear()
    install_sidebar_viewer_session(
        enforced_client,
        viewer_userid="ZhaoYanFang",
        external_userid="wx_ext_001",
        session_id="replacement-session",
    )
    replay = enforced_client.get(
        "/api/sidebar/profile?external_userid=wx_ext_001",
        headers=headers,
    )

    assert cross_customer.status_code == 403
    assert query_token.status_code == 401
    assert replay.status_code == 403
    for response in (cross_customer, query_token, replay):
        assert all(marker not in response.text for marker in ("13800138000", "union_customer_001", "重点跟进", "q_activation"))


def test_customer_detail_aliases_require_admin_capability_before_pii_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    anonymous = TestClient(create_app(), raise_server_exceptions=False)
    no_capability = _admin_client(monkeypatch, "unknown_role")
    viewer = _admin_client(monkeypatch, "viewer")
    routes = (
        "/api/customers/wx_ext_001",
        "/api/users/union_customer_001",
        "/api/admin/customers/profile?mobile=13800138000",
    )

    assert [anonymous.get(route).status_code for route in routes] == [401, 401, 401]
    denied = [no_capability.get(route) for route in routes]
    assert [response.status_code for response in denied] == [403, 403, 403]
    assert all(marker not in response.text for response in denied for marker in ("13800138000", "重点跟进", "q_activation"))
    assert [viewer.get(route).status_code for route in routes] == [200, 200, 200]


def test_viewer_can_read_but_cannot_write_group_ops_control_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _admin_client(monkeypatch, "viewer")

    listed = client.get("/api/admin/automation-conversion/group-ops/plans")
    created = client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={"name": "viewer must not create", "type": "standard"},
    )

    assert listed.status_code == 200
    assert created.status_code == 403
    assert created.json()["error"] == "admin_capability_required"
    assert created.json()["required_capability"] == "manage_group_ops"


def test_automation_admin_can_use_authenticated_group_ops_control_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _admin_client(monkeypatch, "automation_admin")

    response = client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={
            "name": "authenticated formal plan",
            "type": "standard",
            "operatorMemberId": "HuangYouCan",
        },
    )

    assert response.status_code == 201
    assert response.json()["name"] == "authenticated formal plan"


def test_five_principal_permission_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    anonymous = TestClient(create_app(), raise_server_exceptions=False)
    viewer = _admin_client(monkeypatch, "viewer")
    operator = _admin_client(monkeypatch, "automation_admin")
    admin = _admin_client(monkeypatch, "super_admin")
    service = TestClient(create_app(), raise_server_exceptions=False)
    service_token = install_access_token(
        service,
        audience="external_integration",
        capabilities=("identity_resolve",),
        scopes=("read",),
        client_id="pytest-principal-matrix",
    )

    matrix = {
        "anonymous_admin_read": anonymous.get("/api/admin/automation-conversion/group-ops/plans").status_code,
        "viewer_admin_read": viewer.get("/api/admin/automation-conversion/group-ops/plans").status_code,
        "viewer_admin_write": viewer.post(
            "/api/admin/automation-conversion/group-ops/plans",
            json={"name": "viewer denied", "type": "standard"},
        ).status_code,
        "operator_scoped_write": operator.post(
            "/api/admin/automation-conversion/group-ops/plans",
            json={"name": "operator allowed", "type": "standard", "operatorMemberId": "HuangYouCan"},
        ).status_code,
        "admin_write": admin.post(
            "/api/admin/automation-conversion/group-ops/plans",
            json={"name": "admin allowed", "type": "standard", "operatorMemberId": "HuangYouCan"},
        ).status_code,
        "service_internal_read": service.get(
            "/api/identity/resolve?external_userid=wx_ext_001",
            headers=access_token_headers(service_token),
        ).status_code,
        "service_admin_read": service.get(
            "/api/admin/automation-conversion/group-ops/plans",
            headers=access_token_headers(service_token),
        ).status_code,
    }

    assert matrix == {
        "anonymous_admin_read": 401,
        "viewer_admin_read": 200,
        "viewer_admin_write": 403,
        "operator_scoped_write": 201,
        "admin_write": 201,
        "service_internal_read": 200,
        "service_admin_read": 403,
    }


def test_admin_write_requires_request_csrf_not_cookie_only(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _admin_client(monkeypatch, "automation_admin")
    del client.headers["X-CSRF-Token"]

    rejected = client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={"name": "csrf rejected", "type": "standard"},
    )

    assert rejected.status_code == 403
    assert rejected.json()["error"] == "admin_csrf_required"


def test_multipart_form_csrf_field_is_accepted_and_body_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    boundary = "route-policy-boundary"
    token = "multipart-csrf-token"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="csrf_token"\r\n\r\n'
        f"{token}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="upload"; filename="sample.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "sample\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/admin/operations/actions",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
                (b"cookie", f"{CSRF_COOKIE}={token}".encode()),
            ],
        },
        receive,
    )
    monkeypatch.setattr(
        route_policy_module,
        "auth_session_service",
        lambda _request: type(
            "CsrfVerifier",
            (),
            {"verify_csrf": staticmethod(lambda _intro, cookie, supplied: cookie == token and supplied == token)},
        )(),
    )

    assert asyncio.run(_csrf_error(request, object())) is None
    assert asyncio.run(request.body()) == body


def test_revoked_session_is_rejected_before_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _admin_client(monkeypatch, "automation_admin")
    service = client.app.state.auth_session_service
    assert service.revoke(client.cookies.get(SESSION_COOKIE), reason="pytest_revocation")

    response = client.get("/api/admin/automation-conversion/group-ops/plans")

    assert response.status_code == 401
    assert response.json()["error"] == "session_expired_or_revoked"


def test_hybrid_admin_worker_route_accepts_only_scoped_machine_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    client = TestClient(create_app(), raise_server_exceptions=False)
    wrong = install_access_token(
        client,
        audience="internal_worker",
        capabilities=("identity_resolve",),
        scopes=("write",),
        client_id="pytest-wrong-worker",
    )
    denied = client.post(
        "/api/admin/jobs/order-identity-repair/run",
        headers=access_token_headers(wrong),
        json={},
    )
    assert denied.status_code == 403
    assert denied.json()["error"] == "client_purpose_forbidden"

    worker = install_access_token(
        client,
        audience="internal_worker",
        capabilities=("jobs_execute",),
        scopes=("write",),
        client_id="pytest-jobs-worker",
    )
    allowed = client.post(
        "/api/admin/jobs/order-identity-repair/run",
        headers=access_token_headers(worker),
        json={},
    )
    assert allowed.status_code == 410
    assert allowed.json()["error"] == "order_identity_repair_retired"


def test_rate_limiter_rejects_requests_after_profile_budget() -> None:
    limiter = RouteRateLimiter()

    assert all(limiter.allow(profile="auth_strict", principal="198.51.100.2", route_key="POST /login", now=10.0) for _ in range(20))
    assert (
        limiter.allow(
            profile="auth_strict",
            principal="198.51.100.2",
            route_key="POST /login",
            now=10.0,
        )
        is False
    )
    assert (
        limiter.allow(
            profile="auth_strict",
            principal="198.51.100.2",
            route_key="POST /login",
            now=71.0,
        )
        is True
    )
