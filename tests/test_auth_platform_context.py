import pytest

from aicrm_next.platform.platform_foundation.auth_platform.context import AuthContext, PrincipalType


def test_auth_context_normalizes_permissions_and_enforces_owner_scope() -> None:
    context = AuthContext(
        principal_type=PrincipalType.SERVICE,
        principal_id="service:broadcast",
        client_id="broadcast-worker",
        corp_id="corp-1",
        scopes=("write", "write"),
        capabilities=("broadcast_execute",),
        owner_scope={"owner_userid": ["owner-1"], "channel": "wecom"},
        auth_version=2,
        request_id="req-1",
    )

    assert context.scopes == ("write",)
    assert context.permits(
        capability="broadcast_execute",
        scope="write",
        resource={"owner_userid": "owner-1", "channel": "wecom"},
    )
    assert not context.permits(
        capability="broadcast_execute",
        scope="write",
        resource={"owner_userid": "owner-2", "channel": "wecom"},
    )
    assert context.sub == "service:broadcast"
    assert dict(context.resource_constraints) == {"owner_userid": ["owner-1"], "channel": "wecom"}


def test_machine_context_requires_client_id_but_human_context_does_not() -> None:
    with pytest.raises(ValueError, match="client_id"):
        AuthContext(
            principal_type=PrincipalType.API_CLIENT,
            principal_id="agent:campaign",
            client_id="",
            scopes=(),
            capabilities=(),
        )

    human = AuthContext(
        principal_type=PrincipalType.HUMAN,
        principal_id="admin-user:1",
        admin_user_id="1",
        scopes=("admin.read",),
        capabilities=("admin_read",),
    )
    assert human.client_id == ""
    assert human.admin_user_id == "1"


def test_auth_context_rejects_missing_identity_or_nonpositive_version() -> None:
    with pytest.raises(ValueError, match="principal_id"):
        AuthContext(principal_type=PrincipalType.PUBLIC, principal_id="", scopes=(), capabilities=())
    with pytest.raises(ValueError, match="auth_version"):
        AuthContext(
            principal_type=PrincipalType.HUMAN,
            principal_id="admin-user:1",
            scopes=(),
            capabilities=(),
            auth_version=0,
        )
