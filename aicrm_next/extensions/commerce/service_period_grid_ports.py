from __future__ import annotations

from typing import Any, Protocol

from aicrm_next.shared.errors import ContractError


SERVICE_PERIOD_GRID_ACCESS = "service_period_grid_access"
SERVICE_PERIOD_GRID_MANAGE_SHARE = "service_period_grid_manage_share"
SERVICE_PERIOD_GRID_COLLABORATOR_ROLE = "service_period_grid_collaborator"


class CollaboratorAccountDisabledError(ContractError):
    pass


class ServicePeriodGridAccountGateway(Protocol):
    def ensure_account(self, wecom_userid: str, *, operator: str) -> dict[str, Any]: ...
    def release_account(self, admin_user_id: str, *, operator: str) -> dict[str, Any]: ...
    def list_super_admins(self) -> list[dict[str, Any]]: ...


class FixtureServicePeriodGridAccountGateway:
    """Fixture-only boundary; never provisions production administrator accounts."""

    def ensure_account(self, wecom_userid: str, *, operator: str) -> dict[str, Any]:
        del wecom_userid, operator
        raise ContractError("测试内存模式不支持开通真实后台账号")

    def release_account(self, admin_user_id: str, *, operator: str) -> dict[str, Any]:
        del admin_user_id, operator
        return {"released": False, "disabled": False}

    def list_super_admins(self) -> list[dict[str, Any]]:
        return []
