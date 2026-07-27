from __future__ import annotations

from typing import Any

from aicrm_next.extensions.commerce.commerce.application import ListProductsQuery
from aicrm_next.extensions.commerce.commerce.repo import build_commerce_repository
from aicrm_next.media_library.application import GetImageThumbnailQuery, ListMediaItemsQuery

from .application import ListServicePeriodProductsQuery, UpdateServicePeriodMemberRemarkCommand
from .domain import entitlement_status, isoformat, remaining_days
from .huangyoucan_usage import (
    huangyoucan_usage_match_joins,
    huangyoucan_usage_select_fields,
    public_huangyoucan_usage_fields,
)


class DefaultSidebarExtensionAdapter:
    def build_commerce_repository(self) -> Any:
        return build_commerce_repository()

    def list_products(self, repo: Any, *, limit: int, offset: int) -> dict[str, Any]:
        return ListProductsQuery(repo=repo)(limit=limit, offset=offset)

    def list_service_period_products(self, *, limit: int, offset: int) -> dict[str, Any]:
        return ListServicePeriodProductsQuery()(limit=limit, offset=offset)

    def list_media_items(
        self,
        kind: str,
        *,
        limit: int,
        offset: int,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        return ListMediaItemsQuery(kind)(limit=limit, offset=offset, filters=filters)

    def get_image_thumbnail(self, image_id: str, size: int) -> dict[str, Any]:
        return GetImageThumbnailQuery()(image_id, size)

    def update_service_period_member_remark(
        self,
        service_product_id: str,
        unionid: str,
        *,
        remark: str,
    ) -> dict[str, Any]:
        return UpdateServicePeriodMemberRemarkCommand()(service_product_id, unionid, remark=remark)

    def service_period_entitlement_status(self, end_at: Any, status: Any) -> str:
        return entitlement_status(end_at, status)

    def service_period_isoformat(self, value: Any) -> str:
        return isoformat(value)

    def service_period_remaining_days(self, value: Any) -> int:
        return remaining_days(value)

    def huangyoucan_usage_match_joins(self, *, unionid_sql: str, mobile_sql: str) -> str:
        return huangyoucan_usage_match_joins(unionid_sql=unionid_sql, mobile_sql=mobile_sql)

    def huangyoucan_usage_select_fields(self) -> str:
        return huangyoucan_usage_select_fields()

    def public_huangyoucan_usage_fields(self, row: dict[str, Any]) -> dict[str, Any]:
        return public_huangyoucan_usage_fields(row)


__all__ = ["DefaultSidebarExtensionAdapter"]
