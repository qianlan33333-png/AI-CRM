from __future__ import annotations

from typing import Any

from aicrm_next.integration_ports import (
    AlipayAdapter,
    PaymentNotifyGateway,
    PaymentReturnGateway,
    ProductWriteGateway,
    WeChatPayAdapter,
    build_alipay_adapter,
    build_payment_notify_gateway,
    build_payment_return_gateway,
    build_product_write_gateway,
    build_wechat_pay_adapter,
)
from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.mobile import MOBILE_VALIDATION_MESSAGE, normalize_mainland_mobile

from .domain import completion_redirect_projection, normalize_product_completion_target, preview_product, validate_quantity
from .dto import CheckoutRequest, PaymentNotifyRequest, ProductUpsertRequest
from .repo import CommerceRepository, build_commerce_repository


def _payment_side_effect_safety() -> dict[str, Any]:
    return {
        "product_write_mode": build_product_write_gateway().mode,
        "wechat_pay_mode": build_wechat_pay_adapter().mode,
        "alipay_mode": build_alipay_adapter().mode,
        "payment_notify_mode": build_payment_notify_gateway().mode,
        "real_product_write_executed": False,
        "real_wechat_pay_executed": False,
        "real_alipay_executed": False,
        "real_payment_notify_executed": False,
        "real_payment_provider_called": False,
        "real_external_call_executed": False,
        "payment_request_executed": False,
        "payment_notify_executed": False,
        "payment_return_executed": False,
        "provider_signature_verified": False,
        "order_create_executed": False,
        "fallback_used": False,
        "side_effect_executed": False,
    }


def _product_payload_summary(payload: ProductUpsertRequest | dict[str, Any]) -> dict[str, Any]:
    data = payload.model_dump() if isinstance(payload, ProductUpsertRequest) else dict(payload)
    completion_fields = normalize_product_completion_target(data)
    completion_redirect = completion_redirect_projection(
        completion_fields.get("completion_redirect_enabled"),
        completion_fields.get("completion_redirect_url"),
    )
    return {
        "product_code": data.get("product_code", ""),
        "title": data.get("title", ""),
        "enabled": bool(data.get("enabled", False)),
        "detail_section_count": len(data.get("detail_sections") or []),
        "detail_image_count": len(data.get("detail_image_ids") or []),
        **completion_redirect,
    }


class ListProductsQuery:
    def __init__(self, repo: CommerceRepository | None = None) -> None:
        self._repo = repo or build_commerce_repository()

    def __call__(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        payload = self._repo.list_products(limit=limit, offset=offset)
        return {"ok": True, **payload}


class GetProductQuery:
    def __init__(self, repo: CommerceRepository | None = None) -> None:
        self._repo = repo or build_commerce_repository()

    def __call__(self, product_id: str) -> dict[str, Any]:
        product = self._repo.get_product(product_id)
        if not product:
            raise NotFoundError("product not found")
        return {"ok": True, "product": product}


class GetPublicProductQuery:
    def __init__(self, repo: CommerceRepository | None = None) -> None:
        self._repo = repo or build_commerce_repository()

    def __call__(self, page_slug: str) -> dict[str, Any]:
        product = self._repo.get_product_by_slug(page_slug)
        if not product or not product.get("enabled"):
            raise NotFoundError("product not found")
        return {"ok": True, "product": preview_product(product)}


class UpsertProductCommand:
    def __init__(
        self,
        repo: CommerceRepository | None = None,
        product_write_gateway: ProductWriteGateway | None = None,
    ) -> None:
        self._repo = repo or build_commerce_repository()
        self._product_write_gateway = product_write_gateway or build_product_write_gateway()

    def __call__(self, payload: ProductUpsertRequest, product_id: str | None = None) -> dict[str, Any]:
        if product_id:
            from aicrm_next.extensions.commerce.commerce.coupons.application import assert_product_price_allows_coupons

            assert_product_price_allows_coupons(product_id=product_id, new_price=int(payload.price_cents))
        completion_fields = normalize_product_completion_target(payload.model_dump())
        product_payload = {
            **payload.model_dump(),
            **completion_fields,
        }
        gateway_result = (
            self._product_write_gateway.update_product(
                product_id=product_id,
                product_code=payload.product_code,
                page_slug=payload.page_slug or payload.product_code,
                amount=payload.price_cents,
                currency=payload.currency,
                payload_summary=_product_payload_summary(product_payload),
            )
            if product_id
            else self._product_write_gateway.create_product(
                product_code=payload.product_code,
                page_slug=payload.page_slug or payload.product_code,
                amount=payload.price_cents,
                currency=payload.currency,
                payload_summary=_product_payload_summary(product_payload),
            )
        )
        if not gateway_result["ok"]:
            raise ContractError(gateway_result["error_message"] or gateway_result["error_code"])
        product = self._repo.save_product(product_payload, product_id)
        return {
            "ok": True,
            "product": product,
            "side_effect_safety": _payment_side_effect_safety(),
            "adapter_contract": {"product_write": gateway_result},
        }


class SetProductEnabledCommand:
    def __init__(
        self,
        repo: CommerceRepository | None = None,
        product_write_gateway: ProductWriteGateway | None = None,
    ) -> None:
        self._repo = repo or build_commerce_repository()
        self._product_write_gateway = product_write_gateway or build_product_write_gateway()

    def __call__(self, product_id: str, *, enabled: bool) -> dict[str, Any]:
        product = self._repo.get_product(product_id)
        if not product:
            raise NotFoundError("product not found")
        gateway_result = (
            self._product_write_gateway.enable_product(product_id=product_id, product_code=product.get("product_code", ""), page_slug=product.get("page_slug", ""))
            if enabled
            else self._product_write_gateway.disable_product(product_id=product_id, product_code=product.get("product_code", ""), page_slug=product.get("page_slug", ""))
        )
        if not gateway_result["ok"]:
            raise ContractError(gateway_result["error_message"] or gateway_result["error_code"])
        return {
            "ok": True,
            "product": self._repo.set_product_enabled(product_id, enabled),
            "side_effect_safety": _payment_side_effect_safety(),
            "adapter_contract": {"product_write": gateway_result},
        }


class DeleteProductCommand:
    def __init__(
        self,
        repo: CommerceRepository | None = None,
        product_write_gateway: ProductWriteGateway | None = None,
    ) -> None:
        self._repo = repo or build_commerce_repository()
        self._product_write_gateway = product_write_gateway or build_product_write_gateway()

    def __call__(self, product_id: str) -> dict[str, Any]:
        product = self._repo.get_product(product_id)
        if not product:
            raise NotFoundError("product not found")
        gateway_result = self._product_write_gateway.delete_product(product_id=product_id, product_code=product.get("product_code", ""), page_slug=product.get("page_slug", ""))
        if not gateway_result["ok"]:
            raise ContractError(gateway_result["error_message"] or gateway_result["error_code"])
        result = self._repo.delete_product(product_id)
        return {**result, "side_effect_safety": _payment_side_effect_safety(), "adapter_contract": {"product_write": gateway_result}}


class CheckoutCommand:
    def __init__(
        self,
        provider: str,
        repo: CommerceRepository | None = None,
        wechat_adapter: WeChatPayAdapter | None = None,
        alipay_adapter: AlipayAdapter | None = None,
    ) -> None:
        self._provider = provider
        self._repo = repo or build_commerce_repository()
        self._wechat_adapter = wechat_adapter or build_wechat_pay_adapter()
        self._alipay_adapter = alipay_adapter or build_alipay_adapter()

    def __call__(self, payload: CheckoutRequest) -> dict[str, Any]:
        quantity = validate_quantity(payload.quantity)
        product = self._repo.get_product_by_code(payload.product_code)
        if not product:
            raise NotFoundError("product not found")
        if not product.get("enabled"):
            raise ContractError("disabled product cannot checkout")
        amount = int(product["price_cents"]) * quantity
        identity = payload.buyer_identity.model_dump()
        if product.get("require_mobile"):
            mobile = normalize_mainland_mobile(identity.get("mobile"))
            if not mobile:
                raise ContractError(MOBILE_VALIDATION_MESSAGE)
            identity["mobile"] = mobile
        order = self._repo.create_order(
            {
                "payment_provider": self._provider,
                "product_code": product["product_code"],
                "product_title": product["title"],
                "buyer_mobile": identity.get("mobile") or "",
                "external_userid": identity.get("external_userid") or "",
                "openid": identity.get("openid") or "",
                "unionid": identity.get("unionid") or "",
                "amount_cents": amount,
                "currency": product.get("currency", "CNY"),
                "quantity": quantity,
                **completion_redirect_projection(
                    product.get("completion_redirect_enabled"),
                    product.get("completion_redirect_url"),
                ),
                "completion_target_json": product.get("completion_target_json") or product.get("completion_target"),
            }
        )
        if self._provider == "wechat":
            checkout_result = (
                self._wechat_adapter.create_jsapi_order(
                    order_id=order["order_no"],
                    product_id=product["id"],
                    openid=identity.get("openid") or "",
                    amount=amount,
                    currency=product.get("currency", "CNY"),
                    return_url=payload.return_url,
                )
                if identity.get("openid")
                else self._wechat_adapter.create_h5_order(
                    order_id=order["order_no"],
                    product_id=product["id"],
                    amount=amount,
                    currency=product.get("currency", "CNY"),
                    return_url=payload.return_url,
                )
            )
        elif self._provider == "alipay":
            checkout_result = self._alipay_adapter.create_wap_order(
                order_id=order["order_no"],
                product_id=product["id"],
                payer_id=identity.get("openid") or identity.get("external_userid") or "",
                amount=amount,
                currency=product.get("currency", "CNY"),
                return_url=payload.return_url,
            )
        else:
            raise ContractError("unsupported payment provider")
        if not checkout_result["ok"]:
            raise ContractError(checkout_result["error_message"] or checkout_result["error_code"])
        checkout_payload = checkout_result["result"]
        side_effect_safety = {
            **_payment_side_effect_safety(),
            "order_create_executed": "local_only",
        }
        adapter_mode = str(checkout_result.get("mode") or checkout_payload.get("mode") or self._provider)
        return {
            "ok": True,
            "order_no": order["order_no"],
            "payment_provider": self._provider,
            "amount_cents": amount,
            "payment_status": order["payment_status"],
            "route_owner": "ai_crm_next",
            "source_status": "next_checkout",
            "fallback_used": False,
            "real_external_call_executed": False,
            "payment_request_executed": False,
            "order_create_executed": "local_only",
            "adapter_mode": adapter_mode,
            **completion_redirect_projection(
                product.get("completion_redirect_enabled"),
                product.get("completion_redirect_url"),
            ),
            "completion_target": product.get("completion_target"),
            "checkout_url": checkout_payload["checkout_url"],
            "qr_code_url": checkout_payload["qr_code_url"],
            "provider_payload": {
                "provider": self._provider,
                "order_no": order["order_no"],
                "amount_cents": amount,
                "signature_verified": False,
                "source_status": "fake",
            },
            "fake_payment": True,
            "side_effect_safety": side_effect_safety,
            "adapter_contract": {"checkout": checkout_result},
        }


class GetOrderQuery:
    def __init__(self, repo: CommerceRepository | None = None) -> None:
        self._repo = repo or build_commerce_repository()

    def __call__(self, order_no: str) -> dict[str, Any]:
        order = self._repo.get_order(order_no)
        if not order:
            raise NotFoundError("order not found")
        return {
            "ok": True,
            "order": order,
            "payment_status": order["payment_status"],
            "source_status": "next_order_read",
            "route_owner": "ai_crm_next",
            "fallback_used": False,
            "real_external_call_executed": False,
        }


class NotifyPaymentCommand:
    def __init__(
        self,
        provider: str,
        repo: CommerceRepository | None = None,
        notify_gateway: PaymentNotifyGateway | None = None,
    ) -> None:
        self._provider = provider
        self._repo = repo or build_commerce_repository()
        self._notify_gateway = notify_gateway or build_payment_notify_gateway()

    def __call__(self, payload: PaymentNotifyRequest) -> dict[str, Any]:
        notify_id = str(payload.provider_payload.get("notify_id") or payload.provider_payload.get("id") or "")
        gateway_result = (
            self._notify_gateway.receive_wechat_notify(
                order_id=payload.order_no,
                transaction_id=payload.transaction_id or "",
                notify_id=notify_id,
                provider_payload=payload.provider_payload,
            )
            if self._provider == "wechat"
            else self._notify_gateway.receive_alipay_notify(
                order_id=payload.order_no,
                transaction_id=payload.transaction_id or "",
                notify_id=notify_id,
                provider_payload=payload.provider_payload,
            )
        )
        if not gateway_result["ok"]:
            raise ContractError(gateway_result["error_message"] or gateway_result["error_code"])
        order = self._repo.apply_notify(payload.order_no, self._provider, payload.payment_status, payload.transaction_id)
        status_preview = self._notify_gateway.build_order_status_update_preview(
            provider=self._provider,
            order_id=order["order_no"],
            transaction_id=order.get("transaction_id") or "",
            payment_status=order["payment_status"],
        )
        side_effect_safety = {
            **_payment_side_effect_safety(),
            "payment_notify_executed": "local_only",
            "provider_signature_verified": False,
        }
        return {
            "ok": True,
            "order_no": order["order_no"],
            "payment_provider": self._provider,
            "payment_status": order["payment_status"],
            "transaction_id": order.get("transaction_id") or "",
            "source_status": "fake_signature_not_verified",
            "route_owner": "ai_crm_next",
            "fallback_used": False,
            "real_external_call_executed": False,
            "payment_notify_executed": "local_only",
            "real_payment_notify_executed": False,
            "provider_signature_verified": False,
            "event_stub": {"would_emit": "payment_status_changed", "external_side_effect": False},
            "side_effect_safety": side_effect_safety,
            "adapter_contract": {"notify": gateway_result, "order_status_update": status_preview},
        }


class PaymentReturnCommand:
    def __init__(
        self,
        repo: CommerceRepository | None = None,
        return_gateway: PaymentReturnGateway | None = None,
    ) -> None:
        self._repo = repo or build_commerce_repository()
        self._return_gateway = return_gateway or build_payment_return_gateway()

    def __call__(self, *, order_no: str = "", status: str = "paid", provider_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        gateway_result = self._return_gateway.receive_alipay_return(order_id=order_no, status=status, provider_payload=provider_payload or {})
        if not gateway_result["ok"]:
            raise ContractError(gateway_result["error_message"] or gateway_result["error_code"])
        context_result = self._return_gateway.build_return_page_context(order_id=order_no, status=status, provider_payload=provider_payload or {})
        if not context_result["ok"]:
            raise ContractError(context_result["error_message"] or context_result["error_code"])
        order = self._repo.get_order(order_no) if order_no else None
        side_effect_safety = {
            **_payment_side_effect_safety(),
            "payment_return_executed": "fake",
        }
        return {
            "ok": True,
            "source_status": "fake_return_no_order" if not order_no else "fake_return_received",
            "order_no": order_no,
            "payment_provider": "alipay",
            "payment_status": status,
            "transaction_id": (order or {}).get("transaction_id", ""),
            "route_owner": "ai_crm_next",
            "fallback_used": False,
            "real_external_call_executed": False,
            "payment_return_executed": "fake",
            "provider_signature_verified": False,
            "side_effect_safety": side_effect_safety,
            "adapter_contract": {"return": gateway_result, "return_page_context": context_result},
        }


class ListTransactionsQuery:
    def __init__(self, provider: str, repo: CommerceRepository | None = None) -> None:
        self._provider = provider
        self._repo = repo or build_commerce_repository()

    def __call__(self, filters: dict[str, Any], *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        payload = self._repo.list_transactions(self._provider, filters, limit=limit, offset=offset)
        return {"ok": True, "filters": filters, **payload}


class GetTransactionQuery:
    def __init__(self, provider: str, repo: CommerceRepository | None = None) -> None:
        self._provider = provider
        self._repo = repo or build_commerce_repository()

    def __call__(self, order_no: str) -> dict[str, Any]:
        order = self._repo.get_order(order_no)
        if not order or order["payment_provider"] != self._provider:
            raise NotFoundError("transaction not found")
        return {"ok": True, "transaction": order}
