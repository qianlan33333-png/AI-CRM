from __future__ import annotations

import pytest

from aicrm_next.platform.admin_auth.action_token import issue_action_token, validate_action_token
from aicrm_next.platform.admin_auth.capabilities import capabilities_for_roles
from aicrm_next.platform.platform_foundation.auth_platform.context import AuthContext, PrincipalType


pytestmark = pytest.mark.high_risk


def _admin_context(*, request_id: str = "session-current") -> AuthContext:
    return AuthContext(
        principal_type=PrincipalType.HUMAN,
        principal_id="admin-current",
        capabilities=tuple(capabilities_for_roles(["super_admin"])),
        scopes=("admin",),
        admin_user_id="admin-current",
        request_id=request_id,
    )


def test_unsafe_action_token_is_bound_to_session_capability_route_and_method() -> None:
    context = _admin_context()
    token = issue_action_token(
        context,
        capability="manage_config",
        method="POST",
        action="update_runtime_config",
        target="/api/admin/config/runtime",
        now=1_800_000_000,
    )
    valid = validate_action_token(
        token,
        context,
        capability="manage_config",
        method="POST",
        action="update_runtime_config",
        target="/api/admin/config/runtime",
        now=1_800_000_001,
    )
    wrong_target = validate_action_token(
        token,
        context,
        capability="manage_config",
        method="POST",
        action="update_runtime_config",
        target="/api/admin/config/secrets",
        now=1_800_000_001,
    )
    wrong_session = validate_action_token(
        token,
        _admin_context(request_id="another-session"),
        capability="manage_config",
        method="POST",
        action="update_runtime_config",
        target="/api/admin/config/runtime",
        now=1_800_000_001,
    )
    assert valid.ok is True
    assert wrong_target.error == "binding_mismatch:tgt"
    assert wrong_session.error == "binding_mismatch:sid"


def test_action_token_rejects_expiry_tampering_and_safe_methods() -> None:
    context = _admin_context()
    token = issue_action_token(
        context,
        capability="manage_customer",
        method="DELETE",
        action="delete_customer_tag",
        target="/api/admin/customer-tags/1",
        now=1_800_000_000,
        ttl_seconds=30,
    )
    expired = validate_action_token(
        token,
        context,
        capability="manage_customer",
        method="DELETE",
        action="delete_customer_tag",
        target="/api/admin/customer-tags/1",
        now=1_800_000_031,
    )
    tampered = validate_action_token(
        token[:-1] + ("A" if token[-1] != "A" else "B"),
        context,
        capability="manage_customer",
        method="DELETE",
        action="delete_customer_tag",
        target="/api/admin/customer-tags/1",
        now=1_800_000_001,
    )
    assert expired.error == "expired"
    assert tampered.error == "invalid"
    with pytest.raises(ValueError, match="safe method"):
        issue_action_token(
            context,
            capability="admin_read",
            method="GET",
            action="read_customer",
            target="/api/admin/customers/1",
        )
