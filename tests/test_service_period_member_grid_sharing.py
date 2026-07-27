from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from aicrm_next.admin_auth.capabilities import SERVICE_PERIOD_GRID_COLLABORATOR_ROLE
from aicrm_next.admin_config.service_period_grid_accounts import (
    CollaboratorAccountDisabledError,
    ServicePeriodGridAccountService,
    SYSTEM_AUTH_SOURCE,
)
from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app
from aicrm_next.platform_foundation.auth_platform.context import AuthContext, PrincipalType
from aicrm_next.platform_foundation.auth_platform.service_period_grid_share import (
    issue_service_period_grid_share_token,
)
from aicrm_next.extensions.commerce.service_period.application import CreateServicePeriodProductCommand
from aicrm_next.extensions.commerce.service_period.dto import ServicePeriodProductCreateRequest
from aicrm_next.extensions.commerce.service_period.member_grid import empty_view_config
from aicrm_next.extensions.commerce.service_period.member_grid_access import (
    MemberGridAccessConflictError,
    MemberGridAccessDeniedError,
    MemberGridShareGoneError,
)
from aicrm_next.extensions.commerce.service_period.member_grid_sharing import (
    MemberGridShareUnauthorizedError,
    PublicServicePeriodMemberGridService,
    ServicePeriodMemberGridAccessService,
)
from aicrm_next.extensions.commerce.service_period.repo import (
    InMemoryServicePeriodRepository,
    build_service_period_repository,
    reset_service_period_fixture_state,
)
from aicrm_next.shared.errors import NotFoundError
from tests.admin_auth_test_helpers import install_admin_session


def _context(admin_user_id: str, *, super_admin: bool = False) -> AuthContext:
    capabilities = ("service_period_grid_access",)
    if super_admin:
        capabilities = (*capabilities, "service_period_grid_manage_share")
    return AuthContext(
        principal_type=PrincipalType.HUMAN,
        principal_id=f"admin-user:{admin_user_id}",
        admin_user_id=admin_user_id,
        corp_id="corp-pytest",
        capabilities=capabilities,
        scopes=("admin.read", "admin.write"),
    )


def _product(repo: InMemoryServicePeriodRepository, code: str) -> dict:
    return repo.create_service_product(
        trade_product={
            "id": code,
            "product_code": code,
            "name": f"{code} 商品",
            "title": f"{code} 商品",
            "status": "active",
            "enabled": True,
            "amount_total": 99900,
            "currency": "CNY",
        },
        duration_days=90,
        membership_config_id="sharing-vip",
        membership_config_name="分享测试会员",
        link_slug=code,
    )


@dataclass
class _AccountGateway:
    accounts: dict[str, dict] = field(default_factory=dict)
    released: list[str] = field(default_factory=list)

    def ensure_account(self, wecom_userid: str, *, operator: str) -> dict:
        del operator
        return dict(self.accounts[wecom_userid])

    def release_account(self, admin_user_id: str, *, operator: str) -> dict:
        del operator
        self.released.append(admin_user_id)
        return {"released": True, "disabled": True}

    def list_super_admins(self) -> list[dict]:
        return [
            {
                "id": "super_admin:1",
                "admin_user_id": "1",
                "wecom_userid": "root",
                "display_name": "超级管理员",
                "avatar_url": "",
                "permission": "edit",
                "version": 0,
                "implicit": True,
                "removable": False,
            }
        ]


def test_workspace_acl_is_product_scoped_and_super_admin_only_manages_sharing() -> None:
    repo = InMemoryServicePeriodRepository()
    first = _product(repo, "grid-share-first")
    second = _product(repo, "grid-share-second")
    repo.create_member_grid_collaborator(
        first["id"],
        admin_user_id="21",
        wecom_userid="reader",
        display_name="只读协作者",
        avatar_url="",
        permission="read",
        actor="pytest",
    )
    service = ServicePeriodMemberGridAccessService(repo=repo, account_gateway=_AccountGateway())

    reader = service.require(first["id"], "read", context=_context("21"), auth_enforced=True)
    assert reader.can_read is True
    assert reader.can_edit is False
    assert reader.can_manage_share is False
    with pytest.raises(MemberGridAccessDeniedError):
        service.require(first["id"], "edit", context=_context("21"), auth_enforced=True)
    with pytest.raises(MemberGridAccessDeniedError):
        service.require(first["id"], "share", context=_context("21"), auth_enforced=True)
    with pytest.raises(MemberGridAccessDeniedError):
        service.require(second["id"], "read", context=_context("21"), auth_enforced=True)

    capability_rich_non_super = AuthContext(
        principal_type=PrincipalType.HUMAN,
        principal_id="admin-user:22",
        admin_user_id="22",
        corp_id="corp-pytest",
        capabilities=(
            "service_period_grid_access",
            "manage_admin",
            "manage_commerce",
            "manage_config",
        ),
        scopes=("admin.read", "admin.write"),
    )
    with pytest.raises(MemberGridAccessDeniedError):
        service.require(second["id"], "share", context=capability_rich_non_super, auth_enforced=True)

    super_access = service.require(second["id"], "share", context=_context("1", super_admin=True), auth_enforced=True)
    assert super_access.can_edit is True
    assert super_access.can_manage_share is True


def test_collaborator_crud_uses_optimistic_lock_and_releases_only_after_last_acl() -> None:
    repo = InMemoryServicePeriodRepository()
    first = _product(repo, "grid-collab-first")
    second = _product(repo, "grid-collab-second")
    gateway = _AccountGateway(
        accounts={
            "shangziyi": {
                "admin_user_id": "88",
                "wecom_userid": "shangziyi",
                "display_name": "ShangZiYi",
                "avatar_url": "https://example.invalid/avatar.png",
                "implicit_super_admin": False,
            }
        }
    )
    service = ServicePeriodMemberGridAccessService(repo=repo, account_gateway=gateway)
    super_context = _context("1", super_admin=True)

    first_acl = service.create_collaborator(
        first["id"],
        wecom_userid="shangziyi",
        permission="read",
        actor="pytest",
        context=super_context,
        auth_enforced=True,
    )["collaborator"]
    second_acl = service.create_collaborator(
        second["id"],
        wecom_userid="shangziyi",
        permission="edit",
        actor="pytest",
        context=super_context,
        auth_enforced=True,
    )["collaborator"]
    with pytest.raises(MemberGridAccessConflictError):
        service.update_collaborator(
            first["id"],
            first_acl["id"],
            permission="edit",
            expected_version=99,
            actor="pytest",
            context=super_context,
            auth_enforced=True,
        )

    updated = service.update_collaborator(
        first["id"],
        first_acl["id"],
        permission="edit",
        expected_version=1,
        actor="pytest",
        context=super_context,
        auth_enforced=True,
    )["collaborator"]
    assert updated["permission"] == "edit"
    assert updated["version"] == 2

    first_deleted = service.delete_collaborator(
        first["id"],
        first_acl["id"],
        expected_version=2,
        actor="pytest",
        context=super_context,
        auth_enforced=True,
    )
    assert first_deleted["account"]["released"] is False
    assert gateway.released == []
    service.delete_collaborator(
        second["id"],
        second_acl["id"],
        expected_version=1,
        actor="pytest",
        context=super_context,
        auth_enforced=True,
    )
    assert gateway.released == ["88"]


class _AccountRepo:
    def __init__(self, *, existing: dict | None = None, roles: list[str] | None = None) -> None:
        self.directory = {
            "wecom_userid": "staff-1",
            "corp_id": "corp-1",
            "display_name": "客服一号",
            "avatar_url": "avatar",
        }
        self.user = dict(existing) if existing else None
        self.roles = list(roles or [])
        self.disabled = False

    def get_active_wecom_directory_member(self, wecom_userid: str) -> dict | None:
        return dict(self.directory) if wecom_userid == "staff-1" else None

    def get_admin_user_by_wecom_userid(self, wecom_userid: str) -> dict | None:
        return dict(self.user) if self.user and self.user["wecom_userid"] == wecom_userid else None

    def upsert_admin_user(self, payload: dict) -> dict:
        self.user = {**payload, "id": 17, "session_version": 1}
        return dict(self.user)

    def admin_user_role_codes(self, admin_user_id: int) -> list[str]:
        assert admin_user_id == 17
        return list(self.roles)

    def add_admin_user_role(self, *, admin_user_id: int, role_code: str) -> None:
        assert admin_user_id == 17
        if role_code not in self.roles:
            self.roles.append(role_code)

    def get_admin_user(self, admin_user_id: int) -> dict | None:
        return dict(self.user) if self.user and admin_user_id == 17 else None

    def remove_admin_user_role(self, *, admin_user_id: int, role_code: str) -> None:
        assert admin_user_id == 17
        self.roles = [item for item in self.roles if item != role_code]

    def disable_service_period_grid_account(self, *, admin_user_id: int, operator: str) -> None:
        assert admin_user_id == 17
        assert operator
        self.disabled = True

    def list_super_admin_users(self) -> list[dict]:
        return []


def test_restricted_account_lifecycle_preserves_existing_roles_and_never_revives_disabled_accounts() -> None:
    created_repo = _AccountRepo()
    created_service = ServicePeriodGridAccountService(repo=created_repo)  # type: ignore[arg-type]
    created = created_service.ensure_account("staff-1", operator="pytest")
    assert created["created"] is True
    assert created_repo.user["auth_source"] == SYSTEM_AUTH_SOURCE
    assert created_repo.roles == [SERVICE_PERIOD_GRID_COLLABORATOR_ROLE]
    released = created_service.release_account("17", operator="pytest")
    assert released["disabled"] is True
    assert created_repo.disabled is True

    existing_repo = _AccountRepo(
        existing={
            "id": 17,
            "wecom_userid": "staff-1",
            "display_name": "客服一号",
            "is_active": True,
            "login_enabled": True,
            "auth_source": "wecom_sso",
            "admin_level": "admin",
        },
        roles=["viewer"],
    )
    existing_service = ServicePeriodGridAccountService(repo=existing_repo)  # type: ignore[arg-type]
    existing_service.ensure_account("staff-1", operator="pytest")
    existing_service.release_account("17", operator="pytest")
    assert existing_repo.roles == ["viewer"]
    assert existing_repo.disabled is False

    disabled_repo = _AccountRepo(
        existing={
            "id": 17,
            "wecom_userid": "staff-1",
            "display_name": "客服一号",
            "is_active": False,
            "login_enabled": False,
            "auth_source": "wecom_sso",
            "admin_level": "admin",
        }
    )
    with pytest.raises(CollaboratorAccountDisabledError):
        ServicePeriodGridAccountService(repo=disabled_repo).ensure_account("staff-1", operator="pytest")  # type: ignore[arg-type]


def test_external_share_token_is_tamper_proof_revocable_and_rotates_on_reopen(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    repo = InMemoryServicePeriodRepository()
    product = _product(repo, "grid-public-token")
    repo.create_member_view(product["id"], name="已保存视图", config=empty_view_config(), actor="pytest")
    service = ServicePeriodMemberGridAccessService(repo=repo, account_gateway=_AccountGateway())
    public = PublicServicePeriodMemberGridService(repo=repo)
    super_context = _context("1", super_admin=True)

    enabled = service.set_external_share(
        product["id"],
        enabled=True,
        expected_version=1,
        actor="pytest",
        context=super_context,
        auth_enforced=True,
        base_url="https://crm.example.com",
    )["external_share"]
    token = enabled["url"].split("#", 1)[1]
    bootstrap = public.bootstrap(token)
    assert [view["name"] for view in bootstrap["views"]] == ["表格", "已保存视图"]
    assert all(field["editable"] is False for field in bootstrap["schema"]["fields"])
    assert public.query(token, view_id=bootstrap["views"][0]["id"], cursor="", limit=100)["rows"] == []
    deleted_view = next(view for view in repo.list_member_views(product["id"])["items"] if view["name"] == "已保存视图")
    repo.delete_member_view(product["id"], deleted_view["id"], expected_version=deleted_view["version"])
    with pytest.raises(NotFoundError):
        public.query(token, view_id=deleted_view["id"], cursor="", limit=100)
    base64_alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    signature_tail_index = base64_alphabet.index(token[-1])
    assert signature_tail_index % 4 == 0
    signature_alias = base64_alphabet[signature_tail_index + 1]
    with pytest.raises(MemberGridShareUnauthorizedError):
        public.bootstrap(f"{token[:-1]}{signature_alias}")

    disabled = service.set_external_share(
        product["id"],
        enabled=False,
        expected_version=enabled["version"],
        actor="pytest",
        context=super_context,
        auth_enforced=True,
        base_url="https://crm.example.com",
    )["external_share"]
    with pytest.raises(MemberGridShareGoneError):
        public.bootstrap(token)

    reopened = service.set_external_share(
        product["id"],
        enabled=True,
        expected_version=disabled["version"],
        actor="pytest",
        context=super_context,
        auth_enforced=True,
        base_url="https://crm.example.com",
    )["external_share"]
    reopened_token = reopened["url"].split("#", 1)[1]
    assert reopened_token != token
    assert public.bootstrap(reopened_token)["product"]["title"]
    with pytest.raises(MemberGridShareGoneError):
        public.bootstrap(token)


def _api_product_payload() -> ServicePeriodProductCreateRequest:
    return ServicePeriodProductCreateRequest(
        product_code="grid_public_api",
        title="公开网格 API 商品",
        description="只读分享测试",
        price_cents=99900,
        currency="CNY",
        status="active",
        duration_days=90,
        membership_config_id="public-grid-vip",
        membership_config_name="公开网格会员",
    )


def test_public_api_accepts_only_saved_view_query_and_never_caches(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_commerce_fixture_state()
    reset_service_period_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)
    product = CreateServicePeriodProductCommand()(_api_product_payload())["product"]
    repo = build_service_period_repository()
    share = repo.set_member_grid_share_enabled(product["id"], enabled=True, expected_version=1, actor="pytest")
    token = issue_service_period_grid_share_token(
        service_product_id=product["id"],
        public_id=share["public_id"],
        generation=share["generation"],
    )
    view = repo.list_member_views(product["id"])["items"][0]
    headers = {"X-AICRM-Grid-Share-Token": token}

    bootstrap = client.get("/api/public/service-period-member-grid/bootstrap", headers=headers)
    query = client.post(
        "/api/public/service-period-member-grid/query",
        headers=headers,
        json={"view_id": view["id"], "limit": 100},
    )
    arbitrary = client.post(
        "/api/public/service-period-member-grid/query",
        headers=headers,
        json={"view_id": view["id"], "config": empty_view_config()},
    )
    missing = client.get("/api/public/service-period-member-grid/bootstrap")

    assert bootstrap.status_code == 200
    assert query.status_code == 200
    assert arbitrary.status_code == 400
    assert missing.status_code == 401
    assert bootstrap.headers["cache-control"].startswith("no-store")
    assert query.headers["cache-control"].startswith("no-store")


def test_restricted_collaborator_uses_compact_shell_without_share_controls(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_commerce_fixture_state()
    reset_service_period_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)
    product = CreateServicePeriodProductCommand()(_api_product_payload())["product"]
    build_service_period_repository().create_member_grid_collaborator(
        product["id"],
        admin_user_id="42",
        wecom_userid="restricted-42",
        display_name="受限协作者",
        avatar_url="",
        permission="read",
        actor="pytest",
    )
    install_admin_session(
        client,
        SERVICE_PERIOD_GRID_COLLABORATOR_ROLE,
        principal_id="admin-user:42",
    )

    page = client.get(f"/admin/service-period-products/{product['id']}/data")
    assert page.status_code == 200
    assert "sp-compact-admin-shell" in page.text
    assert "退出登录" in page.text
    assert "admin-sidebar" not in page.text
    assert 'id="spShareButton"' in page.text
    share_button = page.text.split('id="spShareButton"', 1)[1].split("</button>", 1)[0]
    assert "hidden" in share_button
