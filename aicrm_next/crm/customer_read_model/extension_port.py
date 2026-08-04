from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class SidebarExtensionPort(Protocol):
    def build_commerce_repository(self) -> Any: ...
    def list_products(self, repo: Any, *, limit: int, offset: int) -> dict[str, Any]: ...
    def list_service_period_products(self, *, limit: int, offset: int) -> dict[str, Any]: ...
    def list_media_items(
        self,
        kind: str,
        *,
        limit: int,
        offset: int,
        filters: dict[str, Any],
    ) -> dict[str, Any]: ...
    def get_image_thumbnail(
        self,
        image_id: str,
        size: int,
        *,
        enabled_only: bool = False,
    ) -> dict[str, Any]: ...
    def generate_missing_image_variants(self, image_id: str) -> None: ...
    def update_service_period_member_remark(
        self,
        service_product_id: str,
        unionid: str,
        *,
        remark: str,
    ) -> dict[str, Any]: ...
    def service_period_entitlement_status(self, end_at: Any, status: Any) -> str: ...
    def service_period_isoformat(self, value: Any) -> str: ...
    def service_period_remaining_days(self, value: Any) -> int: ...
    def huangyoucan_usage_match_joins(self, *, unionid_sql: str, mobile_sql: str) -> str: ...
    def huangyoucan_usage_select_fields(self) -> str: ...
    def public_huangyoucan_usage_fields(self, row: dict[str, Any]) -> dict[str, Any]: ...


SidebarExtensionPortFactory = Callable[[], SidebarExtensionPort]
_FACTORY: SidebarExtensionPortFactory | None = None


def configure_sidebar_extension_port(factory: SidebarExtensionPortFactory) -> None:
    global _FACTORY
    _FACTORY = factory


def build_sidebar_extension_port() -> SidebarExtensionPort:
    if _FACTORY is None:
        raise RuntimeError("sidebar_extension_port_not_configured")
    return _FACTORY()


def build_commerce_repository() -> Any:
    return build_sidebar_extension_port().build_commerce_repository()


class ListProductsQuery:
    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def __call__(self, *, limit: int, offset: int) -> dict[str, Any]:
        return build_sidebar_extension_port().list_products(self._repo, limit=limit, offset=offset)


class ListServicePeriodProductsQuery:
    def __call__(self, *, limit: int, offset: int) -> dict[str, Any]:
        return build_sidebar_extension_port().list_service_period_products(limit=limit, offset=offset)


class ListMediaItemsQuery:
    def __init__(self, kind: str) -> None:
        self._kind = kind

    def __call__(self, *, limit: int, offset: int, filters: dict[str, Any]) -> dict[str, Any]:
        return build_sidebar_extension_port().list_media_items(
            self._kind,
            limit=limit,
            offset=offset,
            filters=filters,
        )


class GetImageThumbnailQuery:
    def __call__(self, image_id: str, size: int, *, enabled_only: bool = False) -> dict[str, Any]:
        return build_sidebar_extension_port().get_image_thumbnail(
            image_id,
            size,
            enabled_only=enabled_only,
        )


def generate_missing_image_variants(image_id: str) -> None:
    build_sidebar_extension_port().generate_missing_image_variants(image_id)


class UpdateServicePeriodMemberRemarkCommand:
    def __call__(
        self,
        service_product_id: str,
        unionid: str,
        *,
        remark: str,
    ) -> dict[str, Any]:
        return build_sidebar_extension_port().update_service_period_member_remark(
            service_product_id,
            unionid,
            remark=remark,
        )


def service_period_entitlement_status(end_at: Any, status: Any) -> str:
    return build_sidebar_extension_port().service_period_entitlement_status(end_at, status)


def service_period_isoformat(value: Any) -> str:
    return build_sidebar_extension_port().service_period_isoformat(value)


def service_period_remaining_days(value: Any) -> int:
    return build_sidebar_extension_port().service_period_remaining_days(value)


def huangyoucan_usage_match_joins(*, unionid_sql: str, mobile_sql: str) -> str:
    return build_sidebar_extension_port().huangyoucan_usage_match_joins(
        unionid_sql=unionid_sql,
        mobile_sql=mobile_sql,
    )


def huangyoucan_usage_select_fields() -> str:
    return build_sidebar_extension_port().huangyoucan_usage_select_fields()


def public_huangyoucan_usage_fields(row: dict[str, Any]) -> dict[str, Any]:
    return build_sidebar_extension_port().public_huangyoucan_usage_fields(row)


__all__ = [
    "GetImageThumbnailQuery",
    "ListMediaItemsQuery",
    "ListProductsQuery",
    "ListServicePeriodProductsQuery",
    "SidebarExtensionPort",
    "UpdateServicePeriodMemberRemarkCommand",
    "build_commerce_repository",
    "build_sidebar_extension_port",
    "configure_sidebar_extension_port",
    "generate_missing_image_variants",
    "huangyoucan_usage_match_joins",
    "huangyoucan_usage_select_fields",
    "public_huangyoucan_usage_fields",
    "service_period_entitlement_status",
    "service_period_isoformat",
    "service_period_remaining_days",
]
