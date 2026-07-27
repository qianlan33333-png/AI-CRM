from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from aicrm_next.extensions.commerce.commerce.application import DeleteProductCommand, SetProductEnabledCommand, UpsertProductCommand
from aicrm_next.extensions.commerce.commerce.dto import ProductUpsertRequest
from aicrm_next.extensions.commerce.commerce.repo import build_commerce_repository
from aicrm_next.extensions.commerce.public_product import h5_wechat_pay
from aicrm_next.shared.errors import ContractError, NotFoundError

from .domain import BUY_BUTTON_TEXT, cta_text_for_status, entitlement_status, remaining_days, text, validate_duration_days
from .dto import ServicePeriodProductCreateRequest, ServicePeriodProductUpdateRequest
from .member_grid import DEFAULT_PAGE_SIZE, empty_view_config, member_grid_schema
from .repo import ServicePeriodRepository, build_service_period_repository
from .repo import reset_service_period_fixture_state as _reset_repo_fixture_state


_SERVICE_PERIOD_TRADE_METADATA = {"aicrm_product_owner": "service_period"}


def _service_period_trade_metadata(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {**(metadata or {}), **_SERVICE_PERIOD_TRADE_METADATA}


def _status_enabled(status: str) -> bool:
    return text(status).lower() == "active"


def _trade_payload_from_create(payload: ServicePeriodProductCreateRequest) -> ProductUpsertRequest:
    return ProductUpsertRequest(
        product_code=payload.product_code,
        title=payload.title,
        description=payload.description,
        price_cents=payload.price_cents,
        currency=payload.currency or "CNY",
        status=payload.status or "active",
        enabled=_status_enabled(payload.status or "active"),
        page_slug=payload.product_code,
        buy_button_text=BUY_BUTTON_TEXT,
        completion_redirect_enabled=payload.completion_redirect_enabled,
        completion_redirect_url=payload.completion_redirect_url,
        completion_target=payload.completion_target,
        metadata_json=_service_period_trade_metadata(),
        require_mobile=payload.require_mobile,
        lead_channel_id=payload.lead_channel_id,
        slices=payload.slices or [],
    )


def _trade_payload_from_update(existing: dict[str, Any], payload: ServicePeriodProductUpdateRequest) -> ProductUpsertRequest:
    trade = dict(existing.get("trade_product") or {})
    status = text(payload.status if payload.status is not None else trade.get("status")) or "active"
    metadata = trade.get("metadata_json")
    return ProductUpsertRequest(
        product_code=text(trade.get("product_code") or existing.get("product_code")),
        title=text(payload.title if payload.title is not None else trade.get("title") or trade.get("name")),
        description=text(payload.description if payload.description is not None else trade.get("description")),
        price_cents=int(payload.price_cents if payload.price_cents is not None else trade.get("price_cents") or trade.get("amount_total") or 0),
        currency=text(payload.currency if payload.currency is not None else trade.get("currency")) or "CNY",
        status=status,
        enabled=_status_enabled(status),
        page_slug=text(trade.get("page_slug") or trade.get("product_code") or existing.get("product_code")),
        buy_button_text=BUY_BUTTON_TEXT,
        completion_redirect_enabled=bool(payload.completion_redirect_enabled if payload.completion_redirect_enabled is not None else trade.get("completion_redirect_enabled")),
        completion_redirect_url=text(payload.completion_redirect_url if payload.completion_redirect_url is not None else trade.get("completion_redirect_url")),
        completion_target=payload.completion_target if payload.completion_target is not None else trade.get("completion_target_json") or trade.get("completion_target"),
        metadata_json=_service_period_trade_metadata(metadata if isinstance(metadata, dict) else {}),
        require_mobile=bool(payload.require_mobile if payload.require_mobile is not None else trade.get("require_mobile")),
        lead_channel_id=payload.lead_channel_id if payload.lead_channel_id is not None else trade.get("lead_channel_id"),
        slices=payload.slices if payload.slices is not None else trade.get("slices") or [],
    )


class ListServicePeriodProductsQuery:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return self._repo.list_products(limit=max(1, min(int(limit or 50), 200)), offset=max(0, int(offset or 0)))


class GetServicePeriodProductQuery:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, service_product_id: str) -> dict[str, Any]:
        product = self._repo.get_product(service_product_id)
        if not product:
            raise NotFoundError("service period product not found")
        return {"ok": True, "product": product}


class GetPublicServicePeriodProductQuery:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, link_slug: str) -> dict[str, Any]:
        product = self._repo.get_public_product_by_slug(link_slug)
        if not product:
            raise NotFoundError("service period product not found")
        return {"ok": True, "product": product}


class GetServicePeriodProductBySlugQuery:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, link_slug: str) -> dict[str, Any]:
        product = self._repo.get_product_by_slug(link_slug)
        if not product:
            raise NotFoundError("service period product not found")
        return {"ok": True, "product": product}


class CreateServicePeriodProductCommand:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, payload: ServicePeriodProductCreateRequest) -> dict[str, Any]:
        duration_days = validate_duration_days(payload.duration_days)
        trade_product = UpsertProductCommand()(_trade_payload_from_create(payload))["product"]
        try:
            product = self._repo.create_service_product(
                trade_product=trade_product,
                duration_days=duration_days,
                membership_config_id=payload.membership_config_id,
                membership_config_name=payload.membership_config_name,
                link_slug=trade_product["product_code"],
                metadata_json=payload.metadata_json,
            )
        except Exception:
            try:
                DeleteProductCommand()(text(trade_product.get("id")))
            except Exception:
                pass
            raise
        return {"ok": True, "product": product}


class UpdateServicePeriodProductCommand:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, service_product_id: str, payload: ServicePeriodProductUpdateRequest) -> dict[str, Any]:
        existing = self._repo.get_product(service_product_id)
        if not existing:
            raise NotFoundError("service period product not found")
        trade_payload = _trade_payload_from_update(existing, payload)
        trade_product = UpsertProductCommand()(trade_payload, text(existing["trade_product_id"]))["product"]
        product = self._repo.update_service_product(
            service_product_id,
            trade_product=trade_product,
            duration_days=validate_duration_days(payload.duration_days if payload.duration_days is not None else existing.get("duration_days")),
            membership_config_id=text(payload.membership_config_id if payload.membership_config_id is not None else existing.get("membership_config_id")),
            membership_config_name=text(payload.membership_config_name if payload.membership_config_name is not None else existing.get("membership_config_name")),
            metadata_json=payload.metadata_json if payload.metadata_json is not None else existing.get("metadata_json") or {},
        )
        return {"ok": True, "product": product}


class CopyServicePeriodProductCommand:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, service_product_id: str) -> dict[str, Any]:
        existing = self._repo.get_product(service_product_id)
        if not existing:
            raise NotFoundError("service period product not found")
        copied_trade_product = build_commerce_repository().copy_product(text(existing["trade_product_id"]))
        product = self._repo.copy_service_product(service_product_id, copied_trade_product=copied_trade_product)
        return {"ok": True, "product": product}


class SetServicePeriodProductEnabledCommand:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, service_product_id: str, *, enabled: bool) -> dict[str, Any]:
        product = self._repo.get_product(service_product_id)
        if not product:
            raise NotFoundError("service period product not found")
        result = SetProductEnabledCommand()(text(product["trade_product_id"]), enabled=enabled)
        updated = self._repo.get_product(service_product_id)
        return {"ok": True, "product": updated, "trade_product": result.get("product")}


class DeleteServicePeriodProductCommand:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, service_product_id: str) -> dict[str, Any]:
        product = self._repo.get_product(service_product_id)
        if not product:
            raise NotFoundError("service period product not found")
        product_code = text(product.get("product_code"))
        if self._repo.has_entitlements(service_product_id) or build_commerce_repository().count_orders_for_product_code(product_code) > 0:
            raise ContractError("已有订单或服务期凭证的周期商品不能硬删除，请先下架")
        deleted = self._repo.delete_service_product(service_product_id)
        trade_delete = DeleteProductCommand()(text(product["trade_product_id"]))
        return {"ok": True, "deleted": True, "service_product": deleted, "trade_product": trade_delete}


class GetServicePeriodProductStatsQuery:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, service_product_id: str) -> dict[str, Any]:
        if not self._repo.get_product(service_product_id):
            raise NotFoundError("service period product not found")
        return self._repo.stats(service_product_id)


class ListServicePeriodMembersQuery:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, service_product_id: str, *, status: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        if not self._repo.get_product(service_product_id):
            raise NotFoundError("service period product not found")
        return self._repo.members(service_product_id, status=text(status) or None, limit=max(1, min(int(limit or 50), 200)), offset=max(0, int(offset or 0)))


class GetServicePeriodMemberGridSchemaQuery:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, service_product_id: str) -> dict[str, Any]:
        if not self._repo.get_product(service_product_id):
            raise NotFoundError("service period product not found")
        return {"ok": True, "schema": member_grid_schema()}


class ListServicePeriodMemberViewsQuery:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, service_product_id: str) -> dict[str, Any]:
        return self._repo.list_member_views(service_product_id)


class CreateServicePeriodMemberViewCommand:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(
        self,
        service_product_id: str,
        *,
        name: str,
        config: dict[str, Any] | None,
        actor: str,
    ) -> dict[str, Any]:
        return self._repo.create_member_view(
            service_product_id,
            name=name,
            config=config if isinstance(config, dict) else empty_view_config(),
            actor=text(actor) or "system",
        )


class UpdateServicePeriodMemberViewCommand:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(
        self,
        service_product_id: str,
        view_id: str,
        *,
        name: str,
        config: dict[str, Any],
        expected_version: int,
        actor: str,
    ) -> dict[str, Any]:
        return self._repo.update_member_view(
            service_product_id,
            view_id,
            name=name,
            config=config,
            expected_version=int(expected_version or 0),
            actor=text(actor) or "system",
        )


class DeleteServicePeriodMemberViewCommand:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, service_product_id: str, view_id: str, *, expected_version: int) -> dict[str, Any]:
        return self._repo.delete_member_view(
            service_product_id,
            view_id,
            expected_version=int(expected_version or 0),
        )


class QueryServicePeriodMemberGridQuery:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(
        self,
        service_product_id: str,
        *,
        config: dict[str, Any] | None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str = "",
    ) -> dict[str, Any]:
        return self._repo.query_member_grid(
            service_product_id,
            config=config if isinstance(config, dict) else empty_view_config(),
            limit=max(1, min(int(limit or DEFAULT_PAGE_SIZE), 200)),
            cursor=text(cursor),
        )


class UpdateServicePeriodMemberRemarkCommand:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, service_product_id: str, unionid: str, *, remark: str) -> dict[str, Any]:
        if not self._repo.get_product(service_product_id):
            raise NotFoundError("service period product not found")
        normalized_remark = text(remark)[:500]
        return self._repo.update_member_remark(service_product_id, text(unionid), normalized_remark)


class UpdateServicePeriodMemberAllianceCommand:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, service_product_id: str, unionid: str, *, alliance: str) -> dict[str, Any]:
        if not self._repo.get_product(service_product_id):
            raise NotFoundError("service period product not found")
        normalized_alliance = text(alliance)[:500]
        return self._repo.update_member_alliance(service_product_id, text(unionid), normalized_alliance)


class GetServicePeriodPublicStateQuery:
    def __init__(
        self,
        repo: ServicePeriodRepository | None = None,
        lead_qr_resolver: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self._repo = repo or build_service_period_repository()
        self._lead_qr_resolver = lead_qr_resolver or h5_wechat_pay.resolve_product_lead_qr

    def __call__(self, link_slug: str, *, unionid: str = "") -> dict[str, Any]:
        product = self._repo.get_public_product_by_slug(link_slug)
        if not product:
            raise NotFoundError("service period product not found")
        entitlement = self._repo.entitlement_for_unionid(text(product["id"]), text(unionid)) if text(unionid) else None
        status = entitlement_status((entitlement or {}).get("end_at"), (entitlement or {}).get("status") or "none")
        entitlement_payload = {
            "status": status,
            "remaining_days": remaining_days((entitlement or {}).get("end_at")),
            "end_at": text((entitlement or {}).get("end_at")),
        }
        lead_qr: dict[str, Any] = {}
        if status == "active":
            try:
                resolved_lead_qr = self._lead_qr_resolver(dict(product.get("trade_product") or {}))
                if isinstance(resolved_lead_qr, dict) and text(resolved_lead_qr.get("qr_url")):
                    lead_qr = {
                        "channel_id": int(resolved_lead_qr.get("channel_id") or 0),
                        "channel_name": text(resolved_lead_qr.get("channel_name")),
                        "qr_url": text(resolved_lead_qr.get("qr_url")),
                        "status": text(resolved_lead_qr.get("status")),
                    }
            except (TypeError, ValueError, RuntimeError):
                lead_qr = {}
        return {
            "ok": True,
            "product": {
                "title": product.get("title"),
                "price_cents": int(product.get("price_cents") or 0),
                "currency": text(product.get("currency")) or "CNY",
                "duration_days": int(product.get("duration_days") or 0),
                "require_mobile": bool((product.get("trade_product") or {}).get("require_mobile") or product.get("require_mobile")),
            },
            "service_product": product,
            "entitlement": entitlement_payload,
            "lead_qr": lead_qr,
            "cta_text": cta_text_for_status(status),
            "checkout_url": f"/s/{product.get('link_slug')}/pay",
            "create_order_url": f"/api/h5/service-period-products/{product.get('link_slug')}/wechat-pay/jsapi/orders",
        }


class GrantOrRenewEntitlementCommand:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, *, order: dict[str, Any], transaction: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._repo.grant_or_renew_from_paid_order(order=order, transaction=transaction or {})


class ApplyServicePeriodRefundCommand:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, *, out_trade_no: str, refund: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._repo.apply_refund_from_order(out_trade_no=text(out_trade_no), refund=refund or {})


class ExpireDueEntitlementsCommand:
    def __init__(self, repo: ServicePeriodRepository | None = None) -> None:
        self._repo = repo or build_service_period_repository()

    def __call__(self, *, now: datetime | None = None) -> dict[str, Any]:
        return self._repo.expire_due_entitlements(now=now)


def reset_service_period_fixture_state() -> None:
    _reset_repo_fixture_state()
