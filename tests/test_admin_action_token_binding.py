from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from aicrm_next.platform.admin_auth.action_token import (
    build_admin_action_token_bundle,
    issue_action_token,
    validate_action_token,
    validate_action_token_for_request,
)
from aicrm_next.platform.platform_foundation.auth_platform.context import AuthContext
from aicrm_next.platform.shared.admin_action_runtime import ensure_admin_action_token, validate_admin_action_token
from aicrm_next.main import create_app
from tests.admin_auth_test_helpers import auth_context, install_admin_action_tokens, install_admin_session


def _context(*, username: str = "security-admin", sid: str = "session-a") -> AuthContext:
    return auth_context("super_admin", subject=f"admin:{username}", token_id=sid)


def _request(context: AuthContext, *, method: str = "POST") -> Request:
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": "/api/admin/external-effects/jobs/7/retry",
            "raw_path": b"/api/admin/external-effects/jobs/7/retry",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 443),
        }
    )
    request.state.auth_context = context
    request.state.route_policy = SimpleNamespace(
        capability="manage_operations",
        route_name="retry_external_effect_job",
        path="/api/admin/external-effects/jobs/{job_id}/retry",
    )
    return request


def _token(context: AuthContext, *, now: int = 1_000) -> str:
    return issue_action_token(
        context,
        capability="manage_operations",
        method="POST",
        action="retry_external_effect_job",
        target="/api/admin/external-effects/jobs/{job_id}/retry",
        now=now,
    )


def test_action_token_accepts_only_exact_bound_context() -> None:
    context = _context()
    token = _token(context)

    result = validate_action_token(
        token,
        context,
        capability="manage_operations",
        method="POST",
        action="retry_external_effect_job",
        target="/api/admin/external-effects/jobs/{job_id}/retry",
        now=1_001,
    )

    assert result.ok is True
    assert result.claims
    assert result.claims["act"] == "retry_external_effect_job"


def test_action_token_rejects_noncanonical_signature_alias() -> None:
    context = _context()
    token = _token(context)
    base64_alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    signature_tail_index = base64_alphabet.index(token[-1])
    assert signature_tail_index % 4 == 0
    signature_alias = base64_alphabet[signature_tail_index + 1]
    tampered = f"{token[:-1]}{signature_alias}"

    result = validate_action_token(
        tampered,
        context,
        capability="manage_operations",
        method="POST",
        action="retry_external_effect_job",
        target="/api/admin/external-effects/jobs/{job_id}/retry",
        now=1_001,
    )

    assert result.ok is False
    assert result.error == "invalid"


@pytest.mark.parametrize(
    ("context", "capability", "method", "action", "target", "error_suffix"),
    [
        (_context(username="other-admin"), "manage_operations", "POST", "retry_external_effect_job", "/api/admin/external-effects/jobs/{job_id}/retry", "sub"),
        (_context(sid="session-b"), "manage_operations", "POST", "retry_external_effect_job", "/api/admin/external-effects/jobs/{job_id}/retry", "sid"),
        (_context(), "manage_group_ops", "POST", "retry_external_effect_job", "/api/admin/external-effects/jobs/{job_id}/retry", "cap"),
        (_context(), "manage_operations", "DELETE", "retry_external_effect_job", "/api/admin/external-effects/jobs/{job_id}/retry", "m"),
        (_context(), "manage_operations", "POST", "cancel_external_effect_job", "/api/admin/external-effects/jobs/{job_id}/retry", "act"),
        (_context(), "manage_operations", "POST", "retry_external_effect_job", "/api/admin/external-effects/jobs/{job_id}/cancel", "tgt"),
    ],
)
def test_action_token_rejects_cross_context_replay(
    context: AuthContext,
    capability: str,
    method: str,
    action: str,
    target: str,
    error_suffix: str,
) -> None:
    result = validate_action_token(
        _token(_context()),
        context,
        capability=capability,
        method=method,
        action=action,
        target=target,
        now=1_001,
    )

    assert result.ok is False
    assert result.error == f"binding_mismatch:{error_suffix}"


def test_action_token_expires_and_cannot_be_minted_for_missing_capability() -> None:
    viewer = auth_context("viewer")
    with pytest.raises(PermissionError):
        _token(viewer)

    expired = validate_action_token(
        _token(_context(), now=1_000),
        _context(),
        capability="manage_operations",
        method="POST",
        action="retry_external_effect_job",
        target="/api/admin/external-effects/jobs/{job_id}/retry",
        now=1_601,
    )
    assert expired.ok is False
    assert expired.error == "expired"


def test_request_validation_uses_runtime_route_policy() -> None:
    context = _context()
    request = _request(context)
    token = _token(context, now=1_000)

    assert validate_action_token_for_request(request, token).ok is False

    current_token = issue_action_token(
        context,
        capability="manage_operations",
        method="POST",
        action="retry_external_effect_job",
        target="/api/admin/external-effects/jobs/{job_id}/retry",
    )
    assert validate_action_token_for_request(request, current_token).ok is True


def test_shell_bundle_contains_independent_tokens_for_each_unsafe_route() -> None:
    request = _request(_context(), method="GET")
    tokens = build_admin_action_token_bundle(request)

    retry_key = "POST /api/admin/external-effects/jobs/{job_id}/retry"
    cancel_key = "POST /api/admin/external-effects/jobs/{job_id}/cancel"
    assert tokens[retry_key]
    assert tokens[cancel_key]
    assert tokens[retry_key] != tokens[cancel_key]


def test_legacy_unbound_token_generator_is_empty_and_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    request = _request(_context())

    error = validate_admin_action_token(ensure_admin_action_token(), request=request)

    assert error == "缺少 admin_action_token"


def test_route_bound_token_is_used_by_backend_without_monitoring_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)
    install_admin_session(client, "super_admin")

    token = install_admin_action_tokens(
        client,
        ("POST", "/api/admin/jobs/order-identity-repair/run"),
    )[("POST", "/api/admin/jobs/order-identity-repair/run")]

    response = client.post(
        "/api/admin/jobs/order-identity-repair/run",
        headers={"X-Admin-Action-Token": token},
        json={},
    )

    assert client.get("/admin/jobs").status_code == 404
    assert response.status_code == 410
    assert response.json()["error"] == "order_identity_repair_retired"
