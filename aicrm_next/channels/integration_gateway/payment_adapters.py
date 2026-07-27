from __future__ import annotations

import hashlib
from typing import Any

from aicrm_next.platform.shared.runtime_settings import managed_runtime_bool, managed_runtime_setting

from .audit import record_audit_event
from .idempotency import get_or_create, make_idempotency_key
from .payment_contracts import AdapterMode, Json


VALID_MODES = {"fake", "disabled", "staging", "production"}
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_NEXT_ALIPAY_MODE",
        "AICRM_NEXT_ENABLE_REAL_ALIPAY",
        "AICRM_NEXT_ENABLE_REAL_PAYMENT_NOTIFY",
        "AICRM_NEXT_ENABLE_REAL_PRODUCT_WRITES",
        "AICRM_NEXT_ENABLE_REAL_WECHAT_PAY",
        "AICRM_NEXT_PAYMENT_NOTIFY_MODE",
        "AICRM_NEXT_PRODUCT_WRITE_MODE",
        "AICRM_NEXT_WECHAT_PAY_MODE",
    }
)


def _normalise_mode(value: str | None, *, default: AdapterMode = "fake") -> AdapterMode:
    mode = (value or default).strip().lower()
    if mode not in VALID_MODES:
        return default
    return mode  # type: ignore[return-value]


def _env_true(name: str) -> bool:
    return managed_runtime_bool(name)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mode_prefix(mode: AdapterMode) -> str:
    return "staging" if mode == "staging" else "fake"


def _safe_target(target: dict[str, Any]) -> dict[str, Any]:
    forbidden = {
        "secret",
        "token",
        "access_token",
        "client_secret",
        "app_secret",
        "credential",
        "password",
        "api_key",
        "private_key",
        "cert",
        "certificate",
        "mch_key",
    }

    def is_secret_key(key: str) -> bool:
        lowered = key.lower()
        return any(marker in lowered for marker in forbidden)

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items() if not is_secret_key(key)}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(target)


def _payload_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    safe = _safe_target(payload or {})
    return {"payload_hash": _digest(repr(sorted(safe.items())))[:24], "payload_keys": sorted(safe.keys())}


def _base_result(
    *,
    ok: bool,
    adapter: str,
    mode: AdapterMode,
    operation: str,
    idempotency_key: str,
    target: dict[str, Any],
    result: dict[str, Any] | None,
    audit_id: str,
    error_code: str = "",
    error_message: str = "",
) -> Json:
    return {
        "ok": ok,
        "adapter": adapter,
        "mode": mode,
        "operation": operation,
        "idempotency_key": idempotency_key,
        "target": _safe_target(target),
        "result": result or {},
        "audit_id": audit_id,
        "side_effect_executed": False,
        "error_code": error_code,
        "error_message": error_message,
    }


class _GuardedPaymentAdapter:
    adapter_name = "PaymentAdapter"
    production_flag = ""

    def __init__(self, mode: AdapterMode | str = "fake") -> None:
        self.mode = _normalise_mode(str(mode), default="fake")

    def _guarded_result(self, *, operation: str, idempotency_key: str, target: dict[str, Any]) -> Json | None:
        if self.mode == "disabled":
            audit = record_audit_event(
                adapter=self.adapter_name,
                operation=operation,
                mode=self.mode,
                idempotency_key=idempotency_key,
                side_effect_executed=False,
                status="blocked",
                error_code="adapter_disabled",
            )
            return _base_result(
                ok=False,
                adapter=self.adapter_name,
                mode=self.mode,
                operation=operation,
                idempotency_key=idempotency_key,
                target=target,
                result={},
                audit_id=audit["audit_id"],
                error_code="adapter_disabled",
                error_message=f"{self.adapter_name} is disabled",
            )
        if self.mode == "production":
            error_code = "production_guard_failed" if not _env_true(self.production_flag) else "production_not_implemented"
            audit = record_audit_event(
                adapter=self.adapter_name,
                operation=operation,
                mode=self.mode,
                idempotency_key=idempotency_key,
                side_effect_executed=False,
                status="blocked",
                error_code=error_code,
            )
            return _base_result(
                ok=False,
                adapter=self.adapter_name,
                mode=self.mode,
                operation=operation,
                idempotency_key=idempotency_key,
                target=target,
                result={},
                audit_id=audit["audit_id"],
                error_code=error_code,
                error_message=f"{self.adapter_name} production mode is not implemented in D7.4",
            )
        return None

    def _successful_result(self, *, operation: str, idempotency_key: str, target: dict[str, Any], factory) -> Json:
        cached = get_or_create(idempotency_key, factory)
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation=operation,
            mode=self.mode,
            idempotency_key=idempotency_key,
            side_effect_executed=False,
            status="ok",
        )
        return _base_result(
            ok=True,
            adapter=self.adapter_name,
            mode=self.mode,
            operation=operation,
            idempotency_key=idempotency_key,
            target=target,
            result=cached,
            audit_id=audit["audit_id"],
        )

    def _audit_only(
        self,
        *,
        operation: str,
        target: dict[str, Any],
        result: dict[str, Any] | None = None,
        error_code: str = "",
        idempotency_key: str | None = None,
    ) -> Json:
        key = idempotency_key or make_idempotency_key(operation=operation, payload={"target": _safe_target(target), "result": result or {}})
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation=operation,
            mode=self.mode,
            idempotency_key=key,
            side_effect_executed=False,
            status="blocked" if error_code else "ok",
            error_code=error_code,
        )
        return _base_result(
            ok=not error_code,
            adapter=self.adapter_name,
            mode=self.mode,
            operation=operation,
            idempotency_key=key,
            target=target,
            result=result or {},
            audit_id=audit["audit_id"],
            error_code=error_code,
            error_message="" if not error_code else "audit recorded as blocked",
        )


class ProductWriteGateway(_GuardedPaymentAdapter):
    adapter_name = "ProductWriteGateway"
    production_flag = "AICRM_NEXT_ENABLE_REAL_PRODUCT_WRITES"

    def create_product(self, *, product_id: str = "", product_code: str = "", page_slug: str = "", amount: int = 0, currency: str = "CNY", payload_summary: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        return self._product_operation("create_product", product_id=product_id, product_code=product_code, page_slug=page_slug, amount=amount, currency=currency, payload_summary=payload_summary, idempotency_key=idempotency_key)

    def update_product(self, *, product_id: str, product_code: str = "", page_slug: str = "", amount: int = 0, currency: str = "CNY", payload_summary: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        return self._product_operation("update_product", product_id=product_id, product_code=product_code, page_slug=page_slug, amount=amount, currency=currency, payload_summary=payload_summary, idempotency_key=idempotency_key)

    def enable_product(self, *, product_id: str, product_code: str = "", page_slug: str = "", idempotency_key: str | None = None) -> Json:
        return self._product_operation("enable_product", product_id=product_id, product_code=product_code, page_slug=page_slug, idempotency_key=idempotency_key)

    def disable_product(self, *, product_id: str, product_code: str = "", page_slug: str = "", idempotency_key: str | None = None) -> Json:
        return self._product_operation("disable_product", product_id=product_id, product_code=product_code, page_slug=page_slug, idempotency_key=idempotency_key)

    def delete_product(self, *, product_id: str, product_code: str = "", page_slug: str = "", idempotency_key: str | None = None) -> Json:
        return self._product_operation("delete_product", product_id=product_id, product_code=product_code, page_slug=page_slug, idempotency_key=idempotency_key)

    def build_product_write_preview(self, *, operation: str, product_id: str = "", product_code: str = "", page_slug: str = "", amount: int = 0, currency: str = "CNY", payload_summary: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        return self._product_operation("build_product_write_preview", product_id=product_id, product_code=product_code, page_slug=page_slug, amount=amount, currency=currency, payload_summary={**(payload_summary or {}), "operation": operation}, idempotency_key=idempotency_key)

    def record_product_write_audit(self, *, operation: str, target: dict[str, Any], result: dict[str, Any] | None = None, error_code: str = "", idempotency_key: str | None = None) -> Json:
        return self._audit_only(operation=operation, target=target, result=result, error_code=error_code, idempotency_key=idempotency_key)

    def _product_operation(self, operation: str, *, product_id: str = "", product_code: str = "", page_slug: str = "", amount: int = 0, currency: str = "CNY", payload_summary: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        target = {"product_id": product_id, "product_code": product_code, "page_slug": page_slug, "amount": amount, "currency": currency, "payload_summary": _payload_summary(payload_summary)}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=target,
            factory=lambda: {
                "write_id": f"{mode_prefix}_product_write_{_digest(key)[:16]}",
                "source_status": mode_prefix,
                "applied": False,
                "operation": operation,
            },
        )


class WeChatPayAdapter(_GuardedPaymentAdapter):
    adapter_name = "WeChatPayAdapter"
    production_flag = "AICRM_NEXT_ENABLE_REAL_WECHAT_PAY"

    def create_jsapi_order(self, *, order_id: str, product_id: str = "", openid: str = "", amount: int = 0, currency: str = "CNY", return_url: str | None = None, idempotency_key: str | None = None) -> Json:
        return self._checkout_operation("create_jsapi_order", order_id=order_id, product_id=product_id, openid=openid, amount=amount, currency=currency, return_url=return_url, channel="jsapi", idempotency_key=idempotency_key)

    def create_h5_order(self, *, order_id: str, product_id: str = "", amount: int = 0, currency: str = "CNY", return_url: str | None = None, idempotency_key: str | None = None) -> Json:
        return self._checkout_operation("create_h5_order", order_id=order_id, product_id=product_id, amount=amount, currency=currency, return_url=return_url, channel="h5", idempotency_key=idempotency_key)

    def query_order(self, *, order_id: str = "", transaction_id: str = "", idempotency_key: str | None = None) -> Json:
        return self._simple_order_operation("query_order", order_id=order_id, transaction_id=transaction_id, idempotency_key=idempotency_key)

    def close_order(self, *, order_id: str, idempotency_key: str | None = None) -> Json:
        return self._simple_order_operation("close_order", order_id=order_id, idempotency_key=idempotency_key)

    def verify_notify_signature(self, *, notify_id: str = "", provider_payload: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        return self._notify_helper("verify_notify_signature", notify_id=notify_id, provider_payload=provider_payload, idempotency_key=idempotency_key)

    def parse_notify_payload(self, *, notify_id: str = "", provider_payload: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        return self._notify_helper("parse_notify_payload", notify_id=notify_id, provider_payload=provider_payload, idempotency_key=idempotency_key)

    def build_checkout_preview(self, *, order_id: str, product_id: str = "", openid: str = "", amount: int = 0, currency: str = "CNY", return_url: str | None = None, idempotency_key: str | None = None) -> Json:
        return self._checkout_operation("build_checkout_preview", order_id=order_id, product_id=product_id, openid=openid, amount=amount, currency=currency, return_url=return_url, channel="preview", idempotency_key=idempotency_key)

    def _checkout_operation(self, operation: str, *, order_id: str, product_id: str, openid: str = "", amount: int, currency: str, return_url: str | None, channel: str, idempotency_key: str | None) -> Json:
        target = {"provider": "wechat", "order_id": order_id, "product_id": product_id, "openid": openid, "amount": amount, "currency": currency, "return_url": return_url or "", "channel": channel}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        transaction_id = f"{mode_prefix}_wechat_tx_{_digest(key)[:16]}"
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=target,
            factory=lambda: {
                "provider": "wechat",
                "order_id": order_id,
                "transaction_id": transaction_id,
                "prepay_id": f"{mode_prefix}_prepay_{_digest(key)[:16]}",
                "checkout_url": f"https://fake-pay.local/wechat/checkout/{order_id}?return_url={return_url or ''}",
                "qr_code_url": f"https://fake-pay.local/wechat/qr/{order_id}.png",
                "source_status": mode_prefix,
                "signature_verified": False,
                "provider_called": False,
            },
        )

    def _simple_order_operation(self, operation: str, *, order_id: str = "", transaction_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"provider": "wechat", "order_id": order_id, "transaction_id": transaction_id}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, target=target, factory=lambda: {"provider": "wechat", "source_status": _mode_prefix(self.mode), "provider_called": False})

    def _notify_helper(self, operation: str, *, notify_id: str, provider_payload: dict[str, Any] | None, idempotency_key: str | None) -> Json:
        target = {"provider": "wechat", "notify_id": notify_id, **_payload_summary(provider_payload)}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, target=target, factory=lambda: {"provider": "wechat", "source_status": _mode_prefix(self.mode), "signature_verified": False, "parsed": operation == "parse_notify_payload"})


class AlipayAdapter(_GuardedPaymentAdapter):
    adapter_name = "AlipayAdapter"
    production_flag = "AICRM_NEXT_ENABLE_REAL_ALIPAY"

    def create_wap_order(self, *, order_id: str, product_id: str = "", payer_id: str = "", amount: int = 0, currency: str = "CNY", return_url: str | None = None, idempotency_key: str | None = None) -> Json:
        return self._checkout_operation("create_wap_order", order_id=order_id, product_id=product_id, payer_id=payer_id, amount=amount, currency=currency, return_url=return_url, idempotency_key=idempotency_key)

    def query_order(self, *, order_id: str = "", transaction_id: str = "", idempotency_key: str | None = None) -> Json:
        return self._simple_order_operation("query_order", order_id=order_id, transaction_id=transaction_id, idempotency_key=idempotency_key)

    def close_order(self, *, order_id: str, idempotency_key: str | None = None) -> Json:
        return self._simple_order_operation("close_order", order_id=order_id, idempotency_key=idempotency_key)

    def verify_notify_signature(self, *, notify_id: str = "", provider_payload: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        return self._notify_helper("verify_notify_signature", notify_id=notify_id, provider_payload=provider_payload, idempotency_key=idempotency_key)

    def parse_notify_payload(self, *, notify_id: str = "", provider_payload: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        return self._notify_helper("parse_notify_payload", notify_id=notify_id, provider_payload=provider_payload, idempotency_key=idempotency_key)

    def build_return_preview(self, *, order_id: str = "", transaction_id: str = "", status: str = "", provider_payload: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        return self._return_operation("build_return_preview", order_id=order_id, transaction_id=transaction_id, status=status, provider_payload=provider_payload, idempotency_key=idempotency_key)

    def build_checkout_preview(self, *, order_id: str, product_id: str = "", payer_id: str = "", amount: int = 0, currency: str = "CNY", return_url: str | None = None, idempotency_key: str | None = None) -> Json:
        return self._checkout_operation("build_checkout_preview", order_id=order_id, product_id=product_id, payer_id=payer_id, amount=amount, currency=currency, return_url=return_url, idempotency_key=idempotency_key)

    def _checkout_operation(self, operation: str, *, order_id: str, product_id: str, payer_id: str, amount: int, currency: str, return_url: str | None, idempotency_key: str | None) -> Json:
        target = {"provider": "alipay", "order_id": order_id, "product_id": product_id, "payer_id": payer_id, "amount": amount, "currency": currency, "return_url": return_url or ""}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        transaction_id = f"{mode_prefix}_alipay_tx_{_digest(key)[:16]}"
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=target,
            factory=lambda: {
                "provider": "alipay",
                "order_id": order_id,
                "transaction_id": transaction_id,
                "payment_url": f"https://fake-pay.local/alipay/checkout/{order_id}?return_url={return_url or ''}",
                "checkout_url": f"https://fake-pay.local/alipay/checkout/{order_id}?return_url={return_url or ''}",
                "qr_code_url": f"https://fake-pay.local/alipay/qr/{order_id}.png",
                "source_status": mode_prefix,
                "signature_verified": False,
                "provider_called": False,
            },
        )

    def _simple_order_operation(self, operation: str, *, order_id: str = "", transaction_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"provider": "alipay", "order_id": order_id, "transaction_id": transaction_id}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, target=target, factory=lambda: {"provider": "alipay", "source_status": _mode_prefix(self.mode), "provider_called": False})

    def _notify_helper(self, operation: str, *, notify_id: str, provider_payload: dict[str, Any] | None, idempotency_key: str | None) -> Json:
        target = {"provider": "alipay", "notify_id": notify_id, **_payload_summary(provider_payload)}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, target=target, factory=lambda: {"provider": "alipay", "source_status": _mode_prefix(self.mode), "signature_verified": False, "parsed": operation == "parse_notify_payload"})

    def _return_operation(self, operation: str, *, order_id: str, transaction_id: str, status: str, provider_payload: dict[str, Any] | None, idempotency_key: str | None) -> Json:
        target = {"provider": "alipay", "order_id": order_id, "transaction_id": transaction_id, "status": status, **_payload_summary(provider_payload)}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, target=target, factory=lambda: {"provider": "alipay", "source_status": _mode_prefix(self.mode), "payment_status": status or "pending", "return_processed": False})


class PaymentNotifyGateway(_GuardedPaymentAdapter):
    adapter_name = "PaymentNotifyGateway"
    production_flag = "AICRM_NEXT_ENABLE_REAL_PAYMENT_NOTIFY"

    def receive_wechat_notify(self, *, order_id: str = "", transaction_id: str = "", notify_id: str = "", amount: int = 0, currency: str = "CNY", provider_payload: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        return self._notify_operation("receive_wechat_notify", provider="wechat", order_id=order_id, transaction_id=transaction_id, notify_id=notify_id, amount=amount, currency=currency, provider_payload=provider_payload, idempotency_key=idempotency_key)

    def receive_alipay_notify(self, *, order_id: str = "", transaction_id: str = "", notify_id: str = "", amount: int = 0, currency: str = "CNY", provider_payload: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        return self._notify_operation("receive_alipay_notify", provider="alipay", order_id=order_id, transaction_id=transaction_id, notify_id=notify_id, amount=amount, currency=currency, provider_payload=provider_payload, idempotency_key=idempotency_key)

    def build_notify_preview(self, *, provider: str, order_id: str = "", transaction_id: str = "", notify_id: str = "", amount: int = 0, currency: str = "CNY", provider_payload: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        return self._notify_operation("build_notify_preview", provider=provider, order_id=order_id, transaction_id=transaction_id, notify_id=notify_id, amount=amount, currency=currency, provider_payload=provider_payload, idempotency_key=idempotency_key)

    def record_notify_audit(self, *, operation: str, target: dict[str, Any], result: dict[str, Any] | None = None, error_code: str = "", idempotency_key: str | None = None) -> Json:
        return self._audit_only(operation=operation, target=target, result=result, error_code=error_code, idempotency_key=idempotency_key)

    def build_order_status_update_preview(self, *, provider: str, order_id: str = "", transaction_id: str = "", payment_status: str = "", idempotency_key: str | None = None) -> Json:
        target = {"provider": provider, "order_id": order_id, "transaction_id": transaction_id, "payment_status": payment_status}
        key = idempotency_key or make_idempotency_key(operation="build_order_status_update_preview", payload=target)
        guarded = self._guarded_result(operation="build_order_status_update_preview", idempotency_key=key, target=target)
        if guarded:
            return guarded
        return self._successful_result(operation="build_order_status_update_preview", idempotency_key=key, target=target, factory=lambda: {"source_status": _mode_prefix(self.mode), "would_update_order": False, "payment_status": payment_status})

    def _notify_operation(self, operation: str, *, provider: str, order_id: str, transaction_id: str, notify_id: str, amount: int, currency: str, provider_payload: dict[str, Any] | None, idempotency_key: str | None) -> Json:
        target = {"provider": provider, "order_id": order_id, "transaction_id": transaction_id, "notify_id": notify_id, "amount": amount, "currency": currency, **_payload_summary(provider_payload)}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        mode_prefix = _mode_prefix(self.mode)
        return self._successful_result(
            operation=operation,
            idempotency_key=key,
            target=target,
            factory=lambda: {
                "provider": provider,
                "notify_id": notify_id or f"{mode_prefix}_{provider}_notify_{_digest(key)[:16]}",
                "transaction_id": transaction_id or f"{mode_prefix}_{provider}_tx_{_digest(key)[:16]}",
                "source_status": mode_prefix,
                "signature_verified": False,
                "parsed": True,
                "would_update_order": False,
            },
        )


class PaymentReturnGateway(_GuardedPaymentAdapter):
    adapter_name = "PaymentReturnGateway"
    production_flag = "AICRM_NEXT_ENABLE_REAL_PAYMENT_NOTIFY"

    def receive_alipay_return(self, *, order_id: str = "", transaction_id: str = "", status: str = "", provider_payload: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        return self._return_operation("receive_alipay_return", order_id=order_id, transaction_id=transaction_id, status=status, provider_payload=provider_payload, idempotency_key=idempotency_key)

    def build_return_page_context(self, *, order_id: str = "", transaction_id: str = "", status: str = "", provider_payload: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        return self._return_operation("build_return_page_context", order_id=order_id, transaction_id=transaction_id, status=status, provider_payload=provider_payload, idempotency_key=idempotency_key)

    def record_return_audit(self, *, operation: str, target: dict[str, Any], result: dict[str, Any] | None = None, error_code: str = "", idempotency_key: str | None = None) -> Json:
        return self._audit_only(operation=operation, target=target, result=result, error_code=error_code, idempotency_key=idempotency_key)

    def _return_operation(self, operation: str, *, order_id: str, transaction_id: str, status: str, provider_payload: dict[str, Any] | None, idempotency_key: str | None) -> Json:
        target = {"provider": "alipay", "order_id": order_id, "transaction_id": transaction_id, "status": status, **_payload_summary(provider_payload)}
        key = idempotency_key or make_idempotency_key(operation=operation, payload=target)
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, target=target, factory=lambda: {"provider": "alipay", "source_status": _mode_prefix(self.mode), "payment_status": status or "pending", "return_processed": False})


def build_product_write_gateway() -> ProductWriteGateway:
    return ProductWriteGateway(managed_runtime_setting("AICRM_NEXT_PRODUCT_WRITE_MODE", "fake"))


def build_wechat_pay_adapter() -> WeChatPayAdapter:
    return WeChatPayAdapter(
        managed_runtime_setting("AICRM_NEXT_WECHAT_PAY_MODE", "fake")
    )


def build_alipay_adapter() -> AlipayAdapter:
    return AlipayAdapter(managed_runtime_setting("AICRM_NEXT_ALIPAY_MODE", "fake"))


def build_payment_notify_gateway() -> PaymentNotifyGateway:
    return PaymentNotifyGateway(managed_runtime_setting("AICRM_NEXT_PAYMENT_NOTIFY_MODE", "fake"))


def build_payment_return_gateway() -> PaymentReturnGateway:
    return PaymentReturnGateway(managed_runtime_setting("AICRM_NEXT_PAYMENT_NOTIFY_MODE", "fake"))
