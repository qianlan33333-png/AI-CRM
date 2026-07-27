from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aicrm_next.platform.platform_foundation.auth_platform.context import AuthContext
from aicrm_next.platform.platform_foundation.auth_platform.service_period_grid_share import (
    ServicePeriodGridShareClaims,
    issue_service_period_grid_share_token,
    verify_service_period_grid_share_token,
)
from aicrm_next.platform.shared.errors import NotFoundError

from .domain import text
from .member_grid import member_grid_schema
from aicrm_next.extensions.commerce.service_period_grid_ports import (
    FixtureServicePeriodGridAccountGateway,
    ServicePeriodGridAccountGateway,
)
from .member_grid_access import (
    MemberGridAccessConflictError,
    MemberGridShareGoneError,
    WorkspaceAccess,
    require_workspace_permission,
    workspace_access,
)
from .repo import ServicePeriodRepository, build_service_period_repository


class MemberGridShareUnauthorizedError(PermissionError):
    pass


def _public_schema() -> dict[str, Any]:
    schema = member_grid_schema()
    for field in schema.get("fields") or []:
        field["editable"] = False
    return schema


@dataclass
class ServicePeriodMemberGridAccessService:
    repo: ServicePeriodRepository | None = None
    account_gateway: ServicePeriodGridAccountGateway | None = None

    def __post_init__(self) -> None:
        if self.repo is None:
            self.repo = build_service_period_repository()

    def resolve(
        self,
        service_product_id: str,
        *,
        context: AuthContext | None,
        auth_enforced: bool,
    ) -> WorkspaceAccess:
        product = self.repo.get_product(service_product_id)
        if not product:
            raise NotFoundError("service period product not found")
        collaborator = None
        if context is not None and text(context.admin_user_id):
            collaborator = self.repo.get_member_grid_collaborator(service_product_id, context.admin_user_id)
        return workspace_access(context, collaborator, auth_enforced=auth_enforced)

    def require(
        self,
        service_product_id: str,
        permission: str,
        *,
        context: AuthContext | None,
        auth_enforced: bool,
    ) -> WorkspaceAccess:
        access = self.resolve(service_product_id, context=context, auth_enforced=auth_enforced)
        require_workspace_permission(access, permission)
        return access

    def access_payload(
        self,
        service_product_id: str,
        *,
        context: AuthContext | None,
        auth_enforced: bool,
    ) -> dict[str, Any]:
        access = self.require(service_product_id, "read", context=context, auth_enforced=auth_enforced)
        return {"ok": True, "access": access.to_dict()}

    def share_settings(
        self,
        service_product_id: str,
        *,
        context: AuthContext | None,
        auth_enforced: bool,
        base_url: str,
    ) -> dict[str, Any]:
        self.require(service_product_id, "share", context=context, auth_enforced=auth_enforced)
        share = self.repo.get_member_grid_share(service_product_id)
        account_gateway = self._accounts()
        super_admins = account_gateway.list_super_admins()
        return {
            "ok": True,
            "collaborators": [*super_admins, *self.repo.list_member_grid_collaborators(service_product_id)],
            "external_share": self._share_payload(base_url, share),
        }

    def create_collaborator(
        self,
        service_product_id: str,
        *,
        wecom_userid: str,
        permission: str,
        actor: str,
        context: AuthContext | None,
        auth_enforced: bool,
    ) -> dict[str, Any]:
        self.require(service_product_id, "share", context=context, auth_enforced=auth_enforced)
        account = self._accounts().ensure_account(wecom_userid, operator=actor)
        if account.get("implicit_super_admin"):
            raise MemberGridAccessConflictError("超级管理员已隐式拥有全部工作区权限")
        collaborator = self.repo.create_member_grid_collaborator(
            service_product_id,
            admin_user_id=text(account.get("admin_user_id")),
            wecom_userid=text(account.get("wecom_userid")),
            display_name=text(account.get("display_name")),
            avatar_url=text(account.get("avatar_url")),
            permission=permission,
            actor=actor,
        )
        return {"ok": True, "collaborator": collaborator}

    def update_collaborator(
        self,
        service_product_id: str,
        collaborator_id: str,
        *,
        permission: str,
        expected_version: int,
        actor: str,
        context: AuthContext | None,
        auth_enforced: bool,
    ) -> dict[str, Any]:
        self.require(service_product_id, "share", context=context, auth_enforced=auth_enforced)
        collaborator = self.repo.update_member_grid_collaborator(
            service_product_id,
            collaborator_id,
            permission=permission,
            expected_version=expected_version,
            actor=actor,
        )
        return {"ok": True, "collaborator": collaborator}

    def delete_collaborator(
        self,
        service_product_id: str,
        collaborator_id: str,
        *,
        expected_version: int,
        actor: str,
        context: AuthContext | None,
        auth_enforced: bool,
    ) -> dict[str, Any]:
        self.require(service_product_id, "share", context=context, auth_enforced=auth_enforced)
        collaborator = self.repo.delete_member_grid_collaborator(
            service_product_id,
            collaborator_id,
            expected_version=expected_version,
        )
        admin_user_id = text(collaborator.get("admin_user_id"))
        account_release = {"released": False, "disabled": False}
        if admin_user_id and self.repo.count_member_grid_collaborations(admin_user_id) == 0:
            account_release = self._accounts().release_account(admin_user_id, operator=actor)
        return {"ok": True, "deleted": True, "collaborator": collaborator, "account": account_release}

    def set_external_share(
        self,
        service_product_id: str,
        *,
        enabled: bool,
        expected_version: int,
        actor: str,
        context: AuthContext | None,
        auth_enforced: bool,
        base_url: str,
    ) -> dict[str, Any]:
        self.require(service_product_id, "share", context=context, auth_enforced=auth_enforced)
        share = self.repo.set_member_grid_share_enabled(
            service_product_id,
            enabled=enabled,
            expected_version=expected_version,
            actor=actor,
        )
        return {"ok": True, "external_share": self._share_payload(base_url, share)}

    def _accounts(self) -> ServicePeriodGridAccountGateway:
        if self.account_gateway is None:
            self.account_gateway = FixtureServicePeriodGridAccountGateway()
        return self.account_gateway

    @staticmethod
    def _share_payload(base_url: str, share: dict[str, Any]) -> dict[str, Any]:
        payload = {**share, "url": ""}
        if share.get("enabled"):
            token = issue_service_period_grid_share_token(
                service_product_id=share.get("service_product_id"),
                public_id=share.get("public_id"),
                generation=share.get("generation"),
            )
            payload["url"] = f"{str(base_url or '').rstrip('/')}/shared/service-period-member-grid#{token}"
        payload.pop("public_id", None)
        return payload


@dataclass
class PublicServicePeriodMemberGridService:
    repo: ServicePeriodRepository | None = None

    def __post_init__(self) -> None:
        if self.repo is None:
            self.repo = build_service_period_repository()

    def bootstrap(self, token: str) -> dict[str, Any]:
        claims = self._authorize(token)
        product = self.repo.get_product(claims.service_product_id)
        if not product:
            raise MemberGridShareGoneError("分享的周期商品已不存在")
        views = self.repo.list_member_views(claims.service_product_id).get("items") or []
        return {
            "ok": True,
            "service_product_id": claims.service_product_id,
            "product": {
                "title": text(product.get("title") or product.get("name") or "周期商品数据"),
            },
            "schema": _public_schema(),
            "views": [
                {
                    "id": text(view.get("id")),
                    "name": text(view.get("name")),
                    "position": int(view.get("position") or 0),
                    "is_default": bool(view.get("is_default")),
                    "version": int(view.get("version") or 1),
                }
                for view in views
            ],
        }

    def query(self, token: str, *, view_id: str, cursor: str, limit: int) -> dict[str, Any]:
        claims = self._authorize(token)
        views = self.repo.list_member_views(claims.service_product_id).get("items") or []
        view = next((item for item in views if text(item.get("id")) == text(view_id)), None)
        if not view:
            raise NotFoundError("member view not found")
        return self.repo.query_member_grid(
            claims.service_product_id,
            config=view.get("config") if isinstance(view.get("config"), dict) else {},
            limit=max(1, min(int(limit or 100), 200)),
            cursor=text(cursor),
        )

    def _authorize(self, token: str) -> ServicePeriodGridShareClaims:
        claims = verify_service_period_grid_share_token(token)
        if claims is None:
            raise MemberGridShareUnauthorizedError("外部分享链接无效")
        try:
            share = self.repo.get_member_grid_share(claims.service_product_id)
        except NotFoundError as exc:
            raise MemberGridShareGoneError("外部分享链接已失效") from exc
        if (
            not share.get("enabled")
            or text(share.get("public_id")) != claims.public_id
            or int(share.get("generation") or 0) != claims.generation
        ):
            raise MemberGridShareGoneError("外部分享链接已失效")
        return claims
