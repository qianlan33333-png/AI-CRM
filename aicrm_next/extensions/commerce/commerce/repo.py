from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import secrets
from typing import Any, Callable, Protocol, TypeVar

from aicrm_next.navigation_target import completion_action_for_target
from aicrm_next.shared.db_session import connect_pooled_postgres
from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.repository_provider import assert_repository_allowed
from aicrm_next.shared.runtime import production_data_ready, raw_database_url

from .domain import completion_redirect_projection, normalize_product_completion_target, normalize_status, now_iso, validate_price_cents
from .domain import validate_product_code
from .refund_status import active_wechat_refund_sql


def connect_commerce_db(database_url: str | None = None):
    return connect_pooled_postgres(database_url or raw_database_url())


TransactionResult = TypeVar("TransactionResult")


def execute_commerce_transaction(
    operation: Callable[[Any], TransactionResult],
) -> TransactionResult:
    with connect_commerce_db() as conn:
        result = operation(conn)
        conn.commit()
        return result


class CommerceRepository(Protocol):
    def list_products(self, *, limit: int, offset: int) -> dict[str, Any]: ...
    def get_product(self, product_id: str) -> dict[str, Any] | None: ...
    def get_product_by_code(self, product_code: str) -> dict[str, Any] | None: ...
    def get_product_by_slug(self, page_slug: str) -> dict[str, Any] | None: ...
    def save_product(self, payload: dict[str, Any], product_id: str | None = None) -> dict[str, Any]: ...
    def set_product_enabled(self, product_id: str, enabled: bool) -> dict[str, Any]: ...
    def delete_product(self, product_id: str) -> dict[str, Any]: ...
    def copy_product(self, product_id: str) -> dict[str, Any]: ...
    def list_lead_channels(self) -> list[dict[str, Any]]: ...
    def get_external_push_config(self, product_id: str) -> dict[str, Any]: ...
    def save_external_push_config(self, product_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def count_orders_for_product_code(self, product_code: str) -> int: ...
    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def get_order(self, order_no: str) -> dict[str, Any] | None: ...
    def apply_notify(self, order_no: str, provider: str, status: str, transaction_id: str | None) -> dict[str, Any]: ...
    def list_transactions(self, provider: str, filters: dict[str, Any], *, limit: int, offset: int) -> dict[str, Any]: ...
    def request_refund(self, provider: str, order_no: str, payload: dict[str, Any]) -> dict[str, Any]: ...


_REFUND_RELATED_ORDER_STATUSES = {"requested", "processing", "refund_processing", "partial_refunded", "full_refunded"}
_SERVICE_PERIOD_PRODUCT_OWNER = "service_period"


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _order_is_paid(order: dict[str, Any]) -> bool:
    return (
        str(order.get("payment_status") or order.get("status") or "").strip().lower() == "paid"
        or str(order.get("trade_state") or "").strip().upper() == "SUCCESS"
        or bool(order.get("paid_at"))
    )


def _order_is_refund_related(order: dict[str, Any]) -> bool:
    refund_status = str(order.get("refund_status") or "").strip().lower()
    return (
        refund_status in _REFUND_RELATED_ORDER_STATUSES
        or _int_or_zero(order.get("refunded_amount_total")) > 0
        or _int_or_zero(order.get("active_refund_amount_total")) > 0
    )


def _product_sales_counts(orders: list[dict[str, Any]], product_code: str) -> dict[str, int]:
    code = str(product_code or "").strip()
    matched = [order for order in orders if str(order.get("product_code") or "").strip() == code]
    paid_order_count = sum(1 for order in matched if _order_is_paid(order))
    refund_order_count = sum(1 for order in matched if _order_is_refund_related(order))
    return {
        "paid_order_count": paid_order_count,
        "refund_order_count": refund_order_count,
        "sold_count": max(0, paid_order_count - refund_order_count),
    }


def _is_service_period_trade_product(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata_json") if isinstance(item.get("metadata_json"), dict) else {}
    return str(metadata.get("aicrm_product_owner") or "").strip() == _SERVICE_PERIOD_PRODUCT_OWNER


def _seed_products() -> list[dict[str, Any]]:
    ts = "2026-05-20T12:00:00Z"
    return [
        {
            "id": "prod_000",
            "product_code": "test-product",
            "title": "测试商品",
            "description": "Public product/pay landing smoke fixture，不生成真实支付。",
            "price_cents": 12900,
            "currency": "CNY",
            "enabled": True,
            "status": "active",
            "page_slug": "test-product",
            "cover_image_id": "image_masked_001",
            "detail_image_ids": [],
            "detail_sections": [{"title": "商品详情", "body": "用于 public landing smoke"}],
            "buy_button_text": "查看支付入口",
            "completion_redirect_enabled": False,
            "completion_redirect_url": "",
            "require_mobile": False,
            "lead_program_id": None,
            "lead_channel_id": None,
            "slices": [],
            "created_at": ts,
            "updated_at": ts,
            "deleted": False,
        },
        {
            "id": "prod_001",
            "product_code": "course_masked_001",
            "title": "课程商品样例",
            "description": "AI-CRM Next fixture 商品，不生成真实支付。",
            "price_cents": 9900,
            "currency": "CNY",
            "enabled": True,
            "status": "active",
            "page_slug": "course-masked-001",
            "cover_image_id": "image_masked_001",
            "detail_image_ids": ["image_masked_001"],
            "detail_sections": [{"title": "商品详情", "body": "脱敏 fixture 内容"}],
            "buy_button_text": "立即购买",
            "completion_redirect_enabled": False,
            "completion_redirect_url": "",
            "require_mobile": False,
            "lead_program_id": None,
            "lead_channel_id": None,
            "slices": [],
            "created_at": ts,
            "updated_at": ts,
            "deleted": False,
        },
        {
            "id": "prod_002",
            "product_code": "course_disabled_001",
            "title": "已下架商品样例",
            "description": "用于 disabled checkout 契约。",
            "price_cents": 19900,
            "currency": "CNY",
            "enabled": False,
            "status": "disabled",
            "page_slug": "course-disabled-001",
            "cover_image_id": "image_masked_001",
            "detail_image_ids": [],
            "detail_sections": [],
            "buy_button_text": "暂不可购买",
            "completion_redirect_enabled": False,
            "completion_redirect_url": "",
            "require_mobile": True,
            "lead_program_id": None,
            "lead_channel_id": None,
            "slices": [],
            "created_at": ts,
            "updated_at": ts,
            "deleted": False,
        },
    ]


def _seed_orders() -> list[dict[str, Any]]:
    ts = "2026-05-20T12:01:00Z"
    return [
        {
            "order_no": "order_masked_001",
            "payment_provider": "wechat",
            "product_code": "course_masked_001",
            "product_title": "课程商品样例",
            "buyer_mobile": "mobile_masked_001",
            "external_userid": "external_user_masked_001",
            "amount_cents": 9900,
            "currency": "CNY",
            "payment_status": "paid",
            "transaction_id": "transaction_masked_001",
            "refunded_amount_total": 0,
            "active_refund_amount_total": 0,
            "refund_status": "",
            "paid_at": ts,
            "created_at": ts,
            "updated_at": ts,
            "quantity": 1,
            "completion_redirect_enabled": False,
            "completion_redirect_url": "",
            "completion_redirect": {"enabled": False, "url": ""},
            "completion_action": {"type": "default", "redirect_url": ""},
        },
        {
            "order_no": "order_fake_0002",
            "payment_provider": "wechat",
            "product_code": "test-product",
            "product_title": "测试商品",
            "buyer_mobile": "13800138000",
            "external_userid": "wx_ext_001",
            "amount_cents": 12900,
            "currency": "CNY",
            "payment_status": "pending",
            "transaction_id": "",
            "refunded_amount_total": 0,
            "active_refund_amount_total": 0,
            "refund_status": "",
            "paid_at": None,
            "created_at": ts,
            "updated_at": ts,
            "quantity": 1,
            "completion_redirect_enabled": False,
            "completion_redirect_url": "",
            "completion_redirect": {"enabled": False, "url": ""},
            "completion_action": {"type": "default", "redirect_url": ""},
        },
        {
            "order_no": "order_fake_0003",
            "payment_provider": "alipay",
            "product_code": "test-product",
            "product_title": "测试商品",
            "buyer_mobile": "13800138000",
            "external_userid": "wx_ext_001",
            "amount_cents": 12900,
            "currency": "CNY",
            "payment_status": "pending",
            "transaction_id": "",
            "refunded_amount_total": 0,
            "active_refund_amount_total": 0,
            "refund_status": "",
            "paid_at": None,
            "created_at": ts,
            "updated_at": ts,
            "quantity": 1,
            "completion_redirect_enabled": False,
            "completion_redirect_url": "",
            "completion_redirect": {"enabled": False, "url": ""},
            "completion_action": {"type": "default", "redirect_url": ""},
        }
    ]


class InMemoryCommerceRepository:
    def __init__(self, products: list[dict[str, Any]] | None = None, orders: list[dict[str, Any]] | None = None) -> None:
        self._products = deepcopy(products if products is not None else _seed_products())
        self._orders = deepcopy(orders if orders is not None else _seed_orders())
        self._external_push: dict[str, dict[str, Any]] = {}

    def list_products(self, *, limit: int, offset: int) -> dict[str, Any]:
        rows = [
            self._serialize_product(item)
            for item in self._products
            if not item.get("deleted") and not _is_service_period_trade_product(item)
        ]
        return {"items": rows[offset : offset + limit], "total": len(rows), "limit": limit, "offset": offset}

    def get_product(self, product_id: str) -> dict[str, Any] | None:
        return self._find_product(lambda item: item["id"] == product_id)

    def get_product_by_code(self, product_code: str) -> dict[str, Any] | None:
        return self._find_product(lambda item: item["product_code"] == product_code)

    def get_product_by_slug(self, page_slug: str) -> dict[str, Any] | None:
        return self._find_product(lambda item: item["page_slug"] == page_slug)

    def save_product(self, payload: dict[str, Any], product_id: str | None = None) -> dict[str, Any]:
        validate_price_cents(int(payload.get("price_cents", 0)))
        completion_fields = normalize_product_completion_target(payload)
        payload = {
            **payload,
            **completion_fields,
            **completion_redirect_projection(
                completion_fields["completion_redirect_enabled"],
                completion_fields["completion_redirect_url"],
            ),
        }
        now = now_iso()
        code = validate_product_code(str(payload["product_code"]))
        payload = {**payload, "product_code": code}
        existing = self.get_product_by_code(code)
        if existing and existing["id"] != product_id:
            raise ContractError("product_code must be unique")
        if product_id:
            for index, item in enumerate(self._products):
                if item["id"] == product_id and not item.get("deleted"):
                    updated = {**item, **self._normalize_product_payload(payload), "id": product_id, "updated_at": now}
                    self._products[index] = updated
                    return self._serialize_product(updated)
            raise NotFoundError("product not found")
        product = {
            **self._normalize_product_payload(payload),
            "id": f"prod_{len(self._products) + 1:03d}",
            "page_slug": payload.get("page_slug") or code,
            "created_at": now,
            "updated_at": now,
            "deleted": False,
        }
        self._products.append(product)
        return self._serialize_product(product)

    def set_product_enabled(self, product_id: str, enabled: bool) -> dict[str, Any]:
        product = self.get_product(product_id)
        if not product:
            raise NotFoundError("product not found")
        product["enabled"] = enabled
        product["status"] = "active" if enabled else "disabled"
        return self.save_product(product, product_id)

    def delete_product(self, product_id: str) -> dict[str, Any]:
        for item in self._products:
            if item["id"] == product_id and not item.get("deleted"):
                if str(item.get("status") or "").strip().lower() == "active" and self.count_orders_for_product_code(str(item.get("product_code") or "")) > 0:
                    raise ContractError("已有订单的商品不能删除，请先下架")
                item["deleted"] = True
                item["enabled"] = False
                item["updated_at"] = now_iso()
                return {"ok": True, "deleted": True, "soft_deleted": False, "product_id": product_id}
        raise NotFoundError("product not found")

    def count_orders_for_product_code(self, product_code: str) -> int:
        code = str(product_code or "").strip()
        return sum(1 for order in self._orders if str(order.get("product_code") or "") == code)

    def copy_product(self, product_id: str) -> dict[str, Any]:
        product = self.get_product(product_id)
        if not product:
            raise NotFoundError("product not found")
        code = _generate_product_code()
        payload = {
            **product,
            "product_code": code,
            "title": f"{product.get('title') or product.get('name') or code} 副本",
            "status": "draft",
            "enabled": False,
        }
        return self.save_product(payload)

    def list_lead_channels(self) -> list[dict[str, Any]]:
        return [{"channel_id": 0, "channel_name": "不配置引流渠道码", "qr_url": "", "selectable": True}]

    def get_external_push_config(self, product_id: str) -> dict[str, Any]:
        if not self.get_product(product_id):
            raise NotFoundError("product not found")
        return deepcopy(self._external_push.get(str(product_id)) or _empty_external_push_config())

    def save_external_push_config(self, product_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.get_product(product_id):
            raise NotFoundError("product not found")
        config = _normalize_external_push_config(payload)
        self._external_push[str(product_id)] = config
        return deepcopy(config)

    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = now_iso()
        order = {
            **payload,
            "order_no": f"order_fake_{len(self._orders) + 1:04d}",
            "payment_status": "pending",
            "transaction_id": "",
            "paid_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self._orders.append(order)
        return deepcopy(order)

    def get_order(self, order_no: str) -> dict[str, Any] | None:
        for order in self._orders:
            if order["order_no"] == order_no:
                return deepcopy(order)
        return None

    def apply_notify(self, order_no: str, provider: str, status: str, transaction_id: str | None) -> dict[str, Any]:
        next_status = normalize_status(status)
        for order in self._orders:
            if order["order_no"] == order_no:
                if order["payment_provider"] != provider:
                    raise ContractError("payment_provider mismatch")
                if order["payment_status"] == next_status and order.get("transaction_id"):
                    return deepcopy(order)
                order["payment_status"] = next_status
                order["transaction_id"] = transaction_id or order.get("transaction_id") or f"transaction_fake_{order_no}"
                order.setdefault("refunded_amount_total", 0)
                order.setdefault("active_refund_amount_total", 0)
                order.setdefault("refund_status", "")
                order["paid_at"] = now_iso() if next_status == "paid" else order.get("paid_at")
                order["updated_at"] = now_iso()
                return deepcopy(order)
        raise NotFoundError("order not found")

    def list_transactions(self, provider: str, filters: dict[str, Any], *, limit: int, offset: int) -> dict[str, Any]:
        rows = [deepcopy(order) for order in self._orders if order["payment_provider"] == provider]
        for key in ["payment_status", "product_code", "external_userid"]:
            if filters.get(key):
                rows = [row for row in rows if row.get(key) == filters[key]]
        if filters.get("mobile"):
            rows = [row for row in rows if filters["mobile"] in str(row.get("buyer_mobile") or "")]
        if filters.get("date_from"):
            rows = [row for row in rows if str(row.get("created_at") or "") >= filters["date_from"]]
        if filters.get("date_to"):
            rows = [row for row in rows if str(row.get("created_at") or "") <= filters["date_to"]]
        return {"items": rows[offset : offset + limit], "total": len(rows), "limit": limit, "offset": offset}

    def request_refund(self, provider: str, order_no: str, payload: dict[str, Any]) -> dict[str, Any]:
        for order in self._orders:
            if order["order_no"] == order_no:
                if order["payment_provider"] != provider:
                    raise ContractError("payment_provider mismatch")
                refund_amount_total = int(payload.get("refund_amount_total") or (payload.get("amount") or {}).get("refund") or 0)
                active = int(order.get("active_refund_amount_total") or 0)
                order["active_refund_amount_total"] = active + refund_amount_total
                order["refund_status"] = "requested"
                order["updated_at"] = now_iso()
                return {
                    "refund": {
                        "status": "requested",
                        "status_label": "退款申请已提交",
                        "out_refund_no": payload.get("out_refund_no", ""),
                    },
                    "order": deepcopy(order),
                }
        raise NotFoundError("order not found")

    def _find_product(self, predicate) -> dict[str, Any] | None:
        for item in self._products:
            if predicate(item) and not item.get("deleted"):
                return self._serialize_product(item)
        return None

    def _normalize_product_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        status = _normalize_product_status(payload.get("status") or ("active" if payload.get("enabled", True) else "disabled"))
        slices = _normalize_slices(payload.get("slices") or [])
        return {
            **payload,
            "title": str(payload.get("title") or payload.get("name") or "").strip(),
            "name": str(payload.get("title") or payload.get("name") or "").strip(),
            "price_cents": int(payload.get("price_cents", payload.get("amount_total", 0)) or 0),
            "amount_total": int(payload.get("price_cents", payload.get("amount_total", 0)) or 0),
            "status": status,
            "enabled": status == "active",
            "buy_button_text": str(payload.get("buy_button_text") or payload.get("cta_text") or "立即购买").strip() or "立即购买",
            "cta_text": str(payload.get("buy_button_text") or payload.get("cta_text") or "立即购买").strip() or "立即购买",
            "require_mobile": bool(payload.get("require_mobile", False)),
            "lead_program_id": None,
            "lead_channel_id": _positive_int_or_none(payload.get("lead_channel_id")),
            "slices": slices,
            "slice_count": len(slices),
            "completion_target_json": deepcopy(payload.get("completion_target_json") or payload.get("completion_target") or {}),
            **completion_redirect_projection(
                payload.get("completion_redirect_enabled"),
                payload.get("completion_redirect_url"),
            ),
        }

    def _serialize_product(self, item: dict[str, Any]) -> dict[str, Any]:
        title = str(item.get("title") or item.get("name") or "").strip()
        price_cents = int(item.get("price_cents", item.get("amount_total", 0)) or 0)
        status = _normalize_product_status(item.get("status") or ("active" if item.get("enabled") else "disabled"))
        cta = str(item.get("buy_button_text") or item.get("cta_text") or "立即购买").strip() or "立即购买"
        slices = _normalize_slices(item.get("slices") or [])
        completion_redirect = completion_redirect_projection(
            item.get("completion_redirect_enabled"),
            item.get("completion_redirect_url"),
        )
        completion_target = normalize_product_completion_target(item)
        completion_action = completion_action_for_target(
            completion_target["completion_target_json"],
            legacy_redirect_url=completion_redirect.get("completion_redirect_url"),
            legacy_enabled=completion_redirect.get("completion_redirect_enabled"),
        )
        sales_counts = _product_sales_counts(self._orders, str(item.get("product_code") or ""))
        return {
            **deepcopy(item),
            "title": title,
            "name": title,
            "price_cents": price_cents,
            "amount_total": price_cents,
            "status": status,
            "enabled": status == "active",
            "buy_button_text": cta,
            "cta_text": cta,
            "require_mobile": bool(item.get("require_mobile", False)),
            "lead_program_id": None,
            "lead_channel_id": _positive_int_or_none(item.get("lead_channel_id")),
            "slices": slices,
            "slice_count": len(slices),
            **sales_counts,
            **completion_redirect,
            "completion_target_json": completion_target["completion_target_json"],
            "completion_target": completion_target["completion_target_json"],
            "completion_action": completion_action,
        }


def _generate_product_code() -> str:
    return "prd_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "_" + secrets.token_hex(3)


def _positive_int_or_none(value: Any) -> int | None:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        normalized = 0
    return normalized or None


def _normalize_product_status(value: Any) -> str:
    status = str(value or "draft").strip().lower()
    if status in {"enabled", "published"}:
        status = "active"
    if status in {"paused", "inactive"}:
        status = "disabled"
    if status not in {"draft", "active", "disabled"}:
        raise ContractError("unsupported product status")
    return status


def _normalize_slices(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    if len(value) > 10:
        raise ContractError("product slices cannot exceed 10")
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            item = {"image_library_id": item}
        image_id = _positive_int_or_none(item.get("image_library_id") or item.get("id"))
        if not image_id or image_id in seen:
            continue
        seen.add(image_id)
        image_url = _lightweight_slice_image_url(
            image_id,
            item.get("source_url"),
            item.get("image_url"),
            item.get("data_url"),
            item.get("url"),
            item.get("src"),
        )
        normalized.append(
            {
                "id": str(item.get("id") or ""),
                "image_library_id": image_id,
                "sort_order": int(item.get("sort_order") or index + 1),
                "name": str(item.get("name") or item.get("file_name") or f"切片 {index + 1}"),
                "file_name": str(item.get("file_name") or ""),
                "file_size": int(item.get("file_size") or 0),
                "mime_type": str(item.get("mime_type") or "image/png"),
                "image_url": image_url,
                "thumb_url": _image_variant_url(image_id, "thumb_320"),
                "preview_url": _image_variant_url(image_id, "mobile_1080"),
                "original_url": _image_variant_url(image_id, "original"),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    return normalized


def _image_variant_url(image_id: Any, variant_key: str) -> str:
    normalized = _positive_int_or_none(image_id)
    return f"/api/admin/image-library/{normalized}/variants/{variant_key}" if normalized else ""


def _lightweight_slice_image_url(image_id: Any, *candidates: Any) -> str:
    for candidate in candidates:
        url = str(candidate or "").strip()
        if url and not url.lower().startswith("data:"):
            return url
    return _image_variant_url(image_id, "mobile_1080")


def _image_public_url(row: dict[str, Any]) -> str:
    return _lightweight_slice_image_url(row.get("image_library_id"), row.get("source_url"))


def _empty_external_push_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "webhook_url": "",
        "push_type": "",
        "expires_at_ts": None,
        "day": None,
        "frequency": None,
        "remark": "",
        "custom_params": {},
        "has_secret": False,
    }


def _normalize_external_push_config(payload: dict[str, Any]) -> dict[str, Any]:
    webhook_url = str(payload.get("webhook_url") or payload.get("external_push_url") or "").strip()
    enabled = bool(payload.get("enabled"))
    if enabled and not webhook_url:
        raise ContractError("webhook_url is required when external push is enabled")
    if webhook_url and not webhook_url.startswith("https://"):
        raise ContractError("webhook_url must be an https URL")

    def int_or_none(key: str) -> int | None:
        value = payload.get(key)
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ContractError(f"{key} must be a number") from exc

    custom_params = payload.get("custom_params")
    if custom_params in (None, ""):
        custom_params = {}
    if isinstance(custom_params, list):
        custom_params = {str(item.get("key") or ""): item.get("value", "") for item in custom_params if isinstance(item, dict)}
    if not isinstance(custom_params, dict):
        raise ContractError("custom_params must be an object")
    if any(not str(key).strip() for key in custom_params):
        raise ContractError("custom_params key cannot be empty")
    return {
        "enabled": enabled,
        "webhook_url": webhook_url,
        "push_type": str(payload.get("push_type") or payload.get("type") or payload.get("external_push_type") or "").strip(),
        "expires_at_ts": int_or_none("expires_at_ts"),
        "day": int_or_none("day"),
        "frequency": int_or_none("frequency"),
        "remark": str(payload.get("remark") or "").strip(),
        "custom_params": custom_params,
        "secret": str(payload.get("secret") or "").strip() if "secret" in payload else None,
        "has_secret": bool(str(payload.get("secret") or "").strip()),
    }


def _jsonb(value: Any) -> Any:
    import json

    from psycopg.types.json import Jsonb

    return Jsonb(value, dumps=lambda data: json.dumps(data, ensure_ascii=False, default=str))


class PostgresCommerceRepository:
    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise ContractError("DATABASE_URL is required for production commerce repository")
        self._database_url = database_url

    def _connect(self):
        return connect_commerce_db(self._database_url)

    def list_products(self, *, limit: int, offset: int) -> dict[str, Any]:
        limit = max(1, min(int(limit or 50), 100))
        offset = max(0, int(offset or 0))
        with self._connect() as conn:
            with conn.cursor() as cur:
                rows = cur.execute(
                    f"""
                    WITH slice_counts AS (
                        SELECT product_id, count(*) AS slice_count
                        FROM wechat_pay_product_page_slices
                        WHERE enabled = TRUE
                        GROUP BY product_id
                    ),
                    order_counts AS (
                        SELECT
                            o.product_code,
                            count(*) FILTER (
                                WHERE o.status = 'paid'
                                   OR o.trade_state = 'SUCCESS'
                                   OR o.paid_at IS NOT NULL
                            ) AS paid_order_count,
                            count(*) FILTER (
                                WHERE COALESCE(o.refund_status, '') IN ('requested', 'processing', 'refund_processing', 'partial_refunded', 'full_refunded')
                                   OR COALESCE(o.refunded_amount_total, 0) > 0
                                   OR EXISTS (
                                       SELECT 1
                                       FROM wechat_pay_refunds r
                                       WHERE r.order_id = o.id
                                         AND {active_wechat_refund_sql("r")}
                                   )
                            ) AS refund_order_count
                        FROM wechat_pay_orders o
                        WHERE COALESCE(o.product_code, '') <> ''
                        GROUP BY o.product_code
                    )
                    SELECT
                        p.*,
                        COALESCE(sc.slice_count, 0) AS slice_count,
                        COALESCE(oc.paid_order_count, 0) AS paid_order_count,
                        COALESCE(oc.refund_order_count, 0) AS refund_order_count,
                        GREATEST(0, COALESCE(oc.paid_order_count, 0) - COALESCE(oc.refund_order_count, 0)) AS sold_count
                    FROM wechat_pay_products p
                    LEFT JOIN slice_counts sc ON sc.product_id = p.id
                    LEFT JOIN order_counts oc ON oc.product_code = p.product_code
                    WHERE COALESCE(p.metadata_json->>'aicrm_product_owner', '') <> 'service_period'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM service_period_products sp
                          WHERE sp.trade_product_id = p.id
                      )
                    ORDER BY p.updated_at DESC, p.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                ).fetchall()
                total_row = cur.execute(
                    """
                    SELECT count(*) AS total
                    FROM wechat_pay_products p
                    WHERE COALESCE(p.metadata_json->>'aicrm_product_owner', '') <> 'service_period'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM service_period_products sp
                          WHERE sp.trade_product_id = p.id
                      )
                    """
                ).fetchone() or {}
        return {
            "items": [self._serialize_product(row) for row in rows],
            "total": int(total_row.get("total") or 0),
            "limit": limit,
            "offset": offset,
        }

    def list_sidebar_active_products(self, *, limit: int, offset: int) -> dict[str, Any]:
        limit = max(1, min(int(limit or 50), 100))
        offset = max(0, int(offset or 0))
        with self._connect() as conn:
            with conn.cursor() as cur:
                rows = cur.execute(
                    """
                    SELECT
                        p.id,
                        p.product_code,
                        p.name,
                        p.amount_total,
                        p.currency,
                        p.enabled,
                        p.status,
                        p.cta_text,
                        p.require_mobile,
                        p.lead_channel_id,
                        p.completion_redirect_enabled,
                        p.completion_redirect_url,
                        p.completion_target_json,
                        p.metadata_json,
                        p.created_at,
                        p.updated_at,
                        count(s.id) AS slice_count
                    FROM wechat_pay_products p
                    LEFT JOIN wechat_pay_product_page_slices s
                      ON s.product_id = p.id AND s.enabled = TRUE
                    WHERE p.enabled = TRUE
                      AND p.status = 'active'
                      AND COALESCE(p.metadata_json->>'aicrm_product_owner', '') <> 'service_period'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM service_period_products sp
                          WHERE sp.trade_product_id = p.id
                      )
                    GROUP BY p.id
                    ORDER BY p.updated_at DESC, p.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                ).fetchall()
                total_row = cur.execute(
                    """
                    SELECT count(*) AS total
                    FROM wechat_pay_products p
                    WHERE p.enabled = TRUE
                      AND p.status = 'active'
                      AND COALESCE(p.metadata_json->>'aicrm_product_owner', '') <> 'service_period'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM service_period_products sp
                          WHERE sp.trade_product_id = p.id
                      )
                    """
                ).fetchone() or {}
        return {
            "items": [self._serialize_product(row) for row in rows],
            "total": int(total_row.get("total") or 0),
            "limit": limit,
            "offset": offset,
        }

    def get_product(self, product_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM wechat_pay_products WHERE id::text = %s LIMIT 1",
                (str(product_id),),
            ).fetchone()
            slices = self._list_product_slices(conn, str(product_id)) if row else []
        return self._serialize_product(row, slices=slices) if row else None

    def get_product_by_code(self, product_code: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM wechat_pay_products WHERE product_code = %s LIMIT 1",
                (str(product_code),),
            ).fetchone()
            slices = self._list_product_slices(conn, str(row.get("id"))) if row else []
        return self._serialize_product(row, slices=slices) if row else None

    def get_product_by_slug(self, page_slug: str) -> dict[str, Any] | None:
        return self.get_product_by_code(page_slug)

    def save_product(self, payload: dict[str, Any], product_id: str | None = None) -> dict[str, Any]:
        validate_price_cents(int(payload.get("price_cents", 0)))
        completion_fields = normalize_product_completion_target(payload)
        payload = {**payload, **completion_fields}
        code = validate_product_code(str(payload["product_code"]))
        status = _normalize_product_status(payload.get("status") or ("active" if payload.get("enabled", True) else "disabled"))
        enabled = status == "active"
        metadata = self._metadata_from_payload(payload)
        lead_channel_id = _positive_int_or_none(payload.get("lead_channel_id"))
        params = {
            "product_code": code,
            "name": str(payload.get("title") or "").strip(),
            "amount_total": int(payload.get("price_cents") or 0),
            "currency": str(payload.get("currency") or "CNY").strip() or "CNY",
            "status": status,
            "enabled": enabled,
            "cta_text": str(payload.get("buy_button_text") or "立即购买").strip() or "立即购买",
            "require_mobile": bool(payload.get("require_mobile", False)),
            "lead_program_id": None,
            "lead_channel_id": lead_channel_id,
            "completion_redirect_enabled": bool(completion_fields["completion_redirect_enabled"]),
            "completion_redirect_url": str(completion_fields["completion_redirect_url"] or ""),
            "completion_target_json": _jsonb(completion_fields["completion_target_json"]),
            "metadata_json": _jsonb(metadata),
        }
        with self._connect() as conn:
            if lead_channel_id:
                channel = conn.execute(
                    """
                    SELECT id, qr_url
                    FROM automation_channel
                    WHERE id = %s
                    LIMIT 1
                    """,
                    (lead_channel_id,),
                ).fetchone()
                if not channel or not str(channel.get("qr_url") or "").strip():
                    raise ContractError("selected lead channel is missing qr_url")
            if product_id:
                existing = conn.execute(
                    "SELECT product_code, amount_total FROM wechat_pay_products WHERE id::text = %s LIMIT 1 FOR UPDATE",
                    (str(product_id),),
                ).fetchone()
                if not existing:
                    raise NotFoundError("product not found")
                if str(existing.get("product_code") or "") != code:
                    raise ContractError("product_code cannot be changed after create")
                if int(params["amount_total"]) < int(existing.get("amount_total") or 0):
                    blocking_coupon = conn.execute(
                        """
                        SELECT cl.id
                        FROM commerce_coupon_product_bindings binding
                        JOIN commerce_coupon_claims cl ON cl.coupon_id = binding.coupon_id
                        WHERE binding.trade_product_id::text = %s
                          AND cl.status IN ('available', 'reserved')
                          AND cl.valid_until > CURRENT_TIMESTAMP
                          AND cl.discount_amount_total >= %s
                        LIMIT 1
                        FOR UPDATE OF cl
                        """,
                        (str(product_id), int(params["amount_total"])),
                    ).fetchone()
                    if blocking_coupon:
                        raise ContractError("商品价格不能低于或等于未过期优惠券的减免金额")
                row = conn.execute(
                    """
                    UPDATE wechat_pay_products
                    SET name = %(name)s,
                        amount_total = %(amount_total)s,
                        currency = %(currency)s,
                        status = %(status)s,
                        enabled = %(enabled)s,
                        cta_text = %(cta_text)s,
                        require_mobile = %(require_mobile)s,
                        lead_program_id = %(lead_program_id)s,
                        lead_channel_id = %(lead_channel_id)s,
                        completion_redirect_enabled = %(completion_redirect_enabled)s,
                        completion_redirect_url = %(completion_redirect_url)s,
                        completion_target_json = %(completion_target_json)s,
                        metadata_json = %(metadata_json)s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id::text = %(product_id)s
                    RETURNING *
                    """,
                    {**params, "product_id": str(product_id)},
                ).fetchone()
                if "slices" in payload:
                    self._replace_product_slices(conn, str(product_id), payload.get("slices") or [])
                conn.commit()
                return self._serialize_product(row, slices=self._list_product_slices(conn, str(product_id)))
            duplicate = conn.execute(
                "SELECT id FROM wechat_pay_products WHERE product_code = %s LIMIT 1",
                (code,),
            ).fetchone()
            if duplicate:
                raise ContractError("product_code must be unique")
            row = conn.execute(
                """
                INSERT INTO wechat_pay_products (
                    product_code,
                    name,
                    amount_total,
                    currency,
                    status,
                    enabled,
                    cta_text,
                    require_mobile,
                    lead_program_id,
                    lead_channel_id,
                    completion_redirect_enabled,
                    completion_redirect_url,
                    completion_target_json,
                    metadata_json,
                    created_at,
                    updated_at
                )
                VALUES (
                    %(product_code)s,
                    %(name)s,
                    %(amount_total)s,
                    %(currency)s,
                    %(status)s,
                    %(enabled)s,
                    %(cta_text)s,
                    %(require_mobile)s,
                    %(lead_program_id)s,
                    %(lead_channel_id)s,
                    %(completion_redirect_enabled)s,
                    %(completion_redirect_url)s,
                    %(completion_target_json)s,
                    %(metadata_json)s,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                RETURNING *
                """,
                params,
            ).fetchone()
            if "slices" in payload:
                self._replace_product_slices(conn, str(row.get("id") or ""), payload.get("slices") or [])
            slices = self._list_product_slices(conn, str(row.get("id") or ""))
            conn.commit()
        return self._serialize_product(row, slices=slices)

    def set_product_enabled(self, product_id: str, enabled: bool) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE wechat_pay_products
                SET enabled = %s,
                    status = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id::text = %s
                RETURNING *
                """,
                (bool(enabled), "active" if enabled else "disabled", str(product_id)),
            ).fetchone()
            conn.commit()
        if not row:
            raise NotFoundError("product not found")
        return self._serialize_product(row)

    def delete_product(self, product_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            product = conn.execute(
                "SELECT id, product_code, status FROM wechat_pay_products WHERE id::text = %s LIMIT 1",
                (str(product_id),),
            ).fetchone()
            if not product:
                raise NotFoundError("product not found")
            product_code = str(product.get("product_code") or "").strip()
            status = str(product.get("status") or "").strip().lower()
            if status == "active" and product_code and self._count_orders_for_product_code(conn, product_code) > 0:
                raise ContractError("已有订单的商品不能删除，请先下架")
            config_rows = conn.execute(
                """
                DELETE FROM external_push_config
                WHERE target_type = 'product'
                  AND target_id = %s
                RETURNING id
                """,
                (str(product_id),),
            ).fetchall()
            row = conn.execute(
                """
                DELETE FROM wechat_pay_products
                WHERE id::text = %s
                RETURNING *
                """,
                (str(product_id),),
            ).fetchone()
            conn.commit()
        if not row:
            raise NotFoundError("product not found")
        return {
            "ok": True,
            "deleted": True,
            "soft_deleted": False,
            "product_id": str(product_id),
            "product": self._serialize_product(row),
            "deleted_external_push_config_count": len(config_rows),
        }

    def count_orders_for_product_code(self, product_code: str) -> int:
        with self._connect() as conn:
            return self._count_orders_for_product_code(conn, str(product_code or "").strip())

    def _count_orders_for_product_code(self, conn: Any, product_code: str) -> int:
        row = conn.execute(
            "SELECT count(*) AS total FROM wechat_pay_orders WHERE product_code = %s",
            (str(product_code or "").strip(),),
        ).fetchone() or {}
        return int(row.get("total") or 0)

    def copy_product(self, product_id: str) -> dict[str, Any]:
        product = self.get_product(product_id)
        if not product:
            raise NotFoundError("product not found")
        payload = {
            **product,
            "product_code": _generate_product_code(),
            "title": f"{product.get('title') or product.get('name') or '商品'} 副本",
            "status": "draft",
            "enabled": False,
            "slices": product.get("slices") or [],
        }
        return self.save_product(payload)

    def list_lead_channels(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    c.id AS channel_id,
                    c.channel_name,
                    COALESCE(NULLIF(active_asset.qr_url, ''), NULLIF(c.qr_url, ''), '') AS qr_url,
                    c.status
                FROM automation_channel c
                LEFT JOIN LATERAL (
                    SELECT qa.qr_url
                    FROM automation_channel_qrcode_asset qa
                    WHERE qa.channel_id = c.id
                      AND qa.status = 'active'
                      AND NULLIF(qa.qr_url, '') IS NOT NULL
                    ORDER BY qa.generated_at DESC, qa.id DESC
                    LIMIT 1
                ) active_asset ON TRUE
                ORDER BY c.updated_at DESC NULLS LAST, c.id DESC
                LIMIT 200
                """
            ).fetchall()
        return [
            {"channel_id": 0, "channel_name": "不配置引流渠道码", "qr_url": "", "selectable": True}
        ] + [
            {
                "channel_id": int(row.get("channel_id") or 0),
                "channel_name": str(row.get("channel_name") or f"渠道 {row.get('channel_id')}"),
                "qr_url": str(row.get("qr_url") or ""),
                "status": str(row.get("status") or ""),
                "selectable": bool(str(row.get("qr_url") or "").strip()),
            }
            for row in rows
        ]

    def get_external_push_config(self, product_id: str) -> dict[str, Any]:
        if not self.get_product(product_id):
            raise NotFoundError("product not found")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM external_push_config
                WHERE tenant_id = 'aicrm'
                  AND target_type = 'product'
                  AND target_id = %s
                  AND event_type = 'transaction.paid'
                LIMIT 1
                """,
                (str(product_id),),
            ).fetchone()
        return self._serialize_external_push(row)

    def save_external_push_config(self, product_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.get_product(product_id):
            raise NotFoundError("product not found")
        config = _normalize_external_push_config(payload)
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO external_push_config (
                    tenant_id, target_type, target_id, event_type, enabled, webhook_url, push_type,
                    expires_at_ts, day, frequency, remark, custom_params, secret, created_by, updated_by,
                    created_at, updated_at
                )
                VALUES (
                    'aicrm', 'product', %(target_id)s, 'transaction.paid', %(enabled)s, %(webhook_url)s, %(push_type)s,
                    %(expires_at_ts)s, %(day)s, %(frequency)s, %(remark)s, %(custom_params)s, %(secret)s, 'next', 'next',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (tenant_id, target_type, target_id, event_type)
                DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    webhook_url = EXCLUDED.webhook_url,
                    push_type = EXCLUDED.push_type,
                    expires_at_ts = EXCLUDED.expires_at_ts,
                    day = EXCLUDED.day,
                    frequency = EXCLUDED.frequency,
                    remark = EXCLUDED.remark,
                    custom_params = EXCLUDED.custom_params,
                    secret = CASE WHEN EXCLUDED.secret = '' THEN external_push_config.secret ELSE EXCLUDED.secret END,
                    updated_by = 'next',
                    updated_at = CURRENT_TIMESTAMP
                RETURNING *
                """,
                {
                    **config,
                    "target_id": str(product_id),
                    "custom_params": _jsonb(config.get("custom_params") or {}),
                    "secret": config.get("secret") or "",
                },
            ).fetchone()
            conn.commit()
        return self._serialize_external_push(row)

    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise ContractError("checkout order writes are not available from the native commerce repository yet")

    def get_order(self, order_no: str) -> dict[str, Any] | None:
        return None

    def apply_notify(self, order_no: str, provider: str, status: str, transaction_id: str | None) -> dict[str, Any]:
        raise ContractError("payment notify writes are not available from the native commerce repository yet")

    def list_transactions(self, provider: str, filters: dict[str, Any], *, limit: int, offset: int) -> dict[str, Any]:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    def request_refund(self, provider: str, order_no: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise ContractError("refund writes are not available from the native commerce repository yet")

    def _metadata_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        metadata = payload.get("metadata_json") if isinstance(payload.get("metadata_json"), dict) else {}
        return {
            **metadata,
            "description": str(payload.get("description") or ""),
            "page_slug": str(payload.get("page_slug") or payload.get("product_code") or ""),
            "cover_image_id": payload.get("cover_image_id"),
            "detail_image_ids": list(payload.get("detail_image_ids") or []),
            "detail_sections": list(payload.get("detail_sections") or []),
        }

    def _serialize_product(self, row: dict[str, Any], *, slices: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
        product_code = str(row.get("product_code") or "")
        title = str(row.get("name") or "")
        price_cents = int(row.get("amount_total") or 0)
        cta = str(row.get("cta_text") or "立即购买")
        status = str(row.get("status") or ("active" if row.get("enabled") else "disabled"))
        slices = slices or []
        paid_order_count = _int_or_zero(row.get("paid_order_count"))
        refund_order_count = _int_or_zero(row.get("refund_order_count"))
        raw_sold_count = row.get("sold_count")
        sold_count = max(0, _int_or_zero(raw_sold_count)) if raw_sold_count not in (None, "") else max(0, paid_order_count - refund_order_count)
        completion_redirect = completion_redirect_projection(
            row.get("completion_redirect_enabled"),
            row.get("completion_redirect_url"),
        )
        completion_target = normalize_product_completion_target(
            {
                "completion_target": row.get("completion_target_json"),
                "completion_redirect_enabled": row.get("completion_redirect_enabled"),
                "completion_redirect_url": row.get("completion_redirect_url"),
            }
        )
        completion_action = completion_action_for_target(
            completion_target["completion_target_json"],
            legacy_redirect_url=completion_redirect.get("completion_redirect_url"),
            legacy_enabled=completion_redirect.get("completion_redirect_enabled"),
        )
        return {
            "id": str(row.get("id") or ""),
            "product_code": product_code,
            "title": title,
            "name": title,
            "description": str(metadata.get("description") or ""),
            "price_cents": price_cents,
            "amount_total": price_cents,
            "currency": str(row.get("currency") or "CNY"),
            "enabled": bool(row.get("enabled")),
            "status": status,
            "page_slug": str(metadata.get("page_slug") or product_code),
            "cover_image_id": metadata.get("cover_image_id"),
            "detail_image_ids": list(metadata.get("detail_image_ids") or []),
            "detail_sections": list(metadata.get("detail_sections") or []),
            "buy_button_text": cta,
            "cta_text": cta,
            "require_mobile": bool(row.get("require_mobile")),
            "lead_program_id": None,
            "lead_channel_id": _positive_int_or_none(row.get("lead_channel_id")),
            "slices": slices,
            "slice_count": int(row.get("slice_count") or len(slices)),
            "paid_order_count": paid_order_count,
            "refund_order_count": refund_order_count,
            "sold_count": sold_count,
            **completion_redirect,
            "completion_target_json": completion_target["completion_target_json"],
            "completion_target": completion_target["completion_target_json"],
            "completion_action": completion_action,
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
            "deleted": False,
        }

    def _list_product_slices(self, conn, product_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT
                s.id,
                s.product_id,
                s.image_library_id,
                s.sort_order,
                s.enabled,
                image.name,
                image.file_name,
                image.source_url,
                image.mime_type,
                image.file_size
            FROM wechat_pay_product_page_slices s
            JOIN image_library image ON image.id = s.image_library_id
            WHERE s.product_id::text = %s
            ORDER BY s.sort_order ASC, s.id ASC
            """,
            (str(product_id),),
        ).fetchall()
        return [
            {
                "id": str(row.get("id") or ""),
                "product_id": str(row.get("product_id") or ""),
                "image_library_id": int(row.get("image_library_id") or 0),
                "sort_order": int(row.get("sort_order") or 0),
                "name": str(row.get("name") or row.get("file_name") or ""),
                "file_name": str(row.get("file_name") or ""),
                "mime_type": str(row.get("mime_type") or "image/png"),
                "file_size": int(row.get("file_size") or 0),
                "image_url": _image_public_url(row),
                "thumb_url": _image_variant_url(row.get("image_library_id"), "thumb_320"),
                "preview_url": _image_variant_url(row.get("image_library_id"), "mobile_1080"),
                "original_url": _image_variant_url(row.get("image_library_id"), "original"),
                "enabled": bool(row.get("enabled")),
            }
            for row in rows
        ]

    def _replace_product_slices(self, conn, product_id: str, slices: Any) -> None:
        normalized = _normalize_slices(slices)
        conn.execute("DELETE FROM wechat_pay_product_page_slices WHERE product_id::text = %s", (str(product_id),))
        for index, item in enumerate(normalized):
            conn.execute(
                """
                INSERT INTO wechat_pay_product_page_slices (
                    product_id, image_library_id, sort_order, enabled, created_at, updated_at
                )
                VALUES (%s, %s, %s, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (int(product_id), int(item["image_library_id"]), int(item.get("sort_order") or index + 1)),
            )

    def _serialize_external_push(self, row: dict[str, Any] | None) -> dict[str, Any]:
        if not row:
            return _empty_external_push_config()
        params = row.get("custom_params") if isinstance(row.get("custom_params"), dict) else {}
        return {
            "enabled": bool(row.get("enabled")),
            "webhook_url": str(row.get("webhook_url") or ""),
            "push_type": str(row.get("push_type") or ""),
            "expires_at_ts": row.get("expires_at_ts"),
            "day": row.get("day"),
            "frequency": row.get("frequency"),
            "remark": str(row.get("remark") or ""),
            "custom_params": params,
            "has_secret": bool(str(row.get("secret") or "").strip()),
        }


_GLOBAL_REPO = InMemoryCommerceRepository()


def build_commerce_repository() -> CommerceRepository:
    if production_data_ready():
        return assert_repository_allowed(
            PostgresCommerceRepository(raw_database_url()),
            capability_owner="commerce",
        )
    return assert_repository_allowed(_GLOBAL_REPO, capability_owner="commerce")


def reset_commerce_fixture_state() -> None:
    global _GLOBAL_REPO
    _GLOBAL_REPO = InMemoryCommerceRepository()
    try:
        from .wechat_shop_service import reset_wechat_shop_fixture_state

        reset_wechat_shop_fixture_state()
    except Exception:
        pass
