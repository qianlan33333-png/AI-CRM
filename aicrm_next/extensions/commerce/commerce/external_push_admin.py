from __future__ import annotations

import json
import socket  # noqa: F401 -- compatibility export for legacy monkeypatch callers
from datetime import datetime, timedelta, timezone
from typing import Any

from aicrm_next.platform.external_push.delivery_projection_port import build_external_push_delivery_projection_port
from aicrm_next.platform.external_push.security import WebhookUrlValidationError, resolve_and_validate_public_https_url  # noqa: F401
from aicrm_next.platform.external_push.service import (
    build_external_push_payload as _build_external_push_payload,
    redact_sensitive_fields as _redact_sensitive_fields,
    sign_webhook_payload as _sign_webhook_payload,
)
from aicrm_next.platform.platform_foundation.command_bus import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import ExternalEffectService, WEBHOOK_GENERIC_PUSH, WEBHOOK_ORDER_PAID_PUSH
from aicrm_next.platform.shared.runtime import production_data_ready

from .external_push_outbox import DEFAULT_TENANT_ID, EVENT_TRANSACTION_PAID, resolve_product_for_order as _resolve_product_for_order
from .repo import connect_commerce_db


EVENT_EXTERNAL_PUSH_TEST = "external_push.test"


class ExternalPushAdminError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _metadata_mobile(order: dict[str, Any]) -> str:
    metadata = _json_object(order.get("metadata_json"))
    for key in ("payer_identity", "buyer_identity"):
        identity = metadata.get(key)
        if isinstance(identity, dict):
            mobile = _text(identity.get("mobile"))
            if mobile:
                return mobile
    return ""


def _identity_mobile_for_order(conn: Any, order: dict[str, Any]) -> str:
    unionid = _text(order.get("unionid"))
    if not unionid:
        return ""
    row = conn.execute(
        """
        SELECT mobile
        FROM crm_user_identity
        WHERE unionid = %s
          AND COALESCE(mobile, '') <> ''
        LIMIT 1
        """,
        (unionid,),
    ).fetchone()
    if not row:
        return ""
    try:
        return _text(dict(row).get("mobile"))
    except (TypeError, ValueError):
        return _text(getattr(row, "mobile", ""))


def _resolve_order_mobile(conn: Any | None, order: dict[str, Any]) -> str:
    mobile = _metadata_mobile(order)
    if mobile or conn is None:
        return mobile
    return _identity_mobile_for_order(conn, order)


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _connect():
    if not production_data_ready():
        raise ExternalPushAdminError("production_database_required")
    return connect_commerce_db()


def _public_delivery(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    payload = dict(row)
    payload["id"] = int(payload.get("id") or 0)
    payload["config_id"] = int(payload.get("config_id") or 0)
    payload["order_id"] = int(payload.get("order_id") or 0)
    payload["product_id"] = int(payload.get("product_id") or 0)
    payload["attempt_count"] = int(payload.get("attempt_count") or 0)
    payload["request_headers"] = _json_obj(payload.get("request_headers"))
    payload["request_body"] = _json_obj(payload.get("request_body"))
    payload["response_body"] = _text(payload.get("response_body"))
    return payload


def _public_outbox(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    payload = dict(row)
    payload["id"] = int(payload.get("id") or 0)
    payload["retry_count"] = int(payload.get("retry_count") or 0)
    payload["payload"] = _json_obj(payload.get("payload"))
    return payload


def _public_config(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    payload = dict(row)
    payload["id"] = int(payload.get("id") or 0)
    payload["enabled"] = bool(payload.get("enabled"))
    payload["custom_params"] = _json_obj(payload.get("custom_params"))
    payload["has_secret"] = bool(_text(payload.get("secret")))
    payload.pop("secret", None)
    return payload


def _retry_at(attempt_count: int) -> str | None:
    delays = {1: 0, 2: 60, 3: 300, 4: 1800, 5: 7200}
    if int(attempt_count) >= 5:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=delays.get(int(attempt_count) + 1, 7200))).isoformat()


def _get_order(conn: Any, order_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM wechat_pay_orders WHERE id = %s LIMIT 1", (int(order_id),)).fetchone()
    return dict(row) if row else None


def _get_product(conn: Any, product_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM wechat_pay_products WHERE id = %s LIMIT 1", (int(product_id),)).fetchone()
    return dict(row) if row else None


def _get_product_for_order(conn: Any, order: dict[str, Any]) -> dict[str, Any]:
    try:
        return _resolve_product_for_order(conn, order)
    except Exception:
        pass
    row = conn.execute(
        """
        SELECT *
        FROM wechat_pay_products
        WHERE product_code = %s
        LIMIT 1
        """,
        (_text(order.get("product_code")),),
    ).fetchone()
    if row:
        return dict(row)
    return {
        "id": 0,
        "product_code": _text(order.get("product_code")),
        "name": _text(order.get("product_name") or order.get("product_code")),
        "amount_total": int(order.get("amount_total") or 0),
    }


def _get_config(conn: Any, config_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM external_push_config WHERE id = %s LIMIT 1", (int(config_id),)).fetchone()
    return dict(row) if row else None


def _get_product_config(conn: Any, product_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM external_push_config
        WHERE tenant_id = %s
          AND target_type = 'product'
          AND target_id = %s
          AND event_type = %s
        LIMIT 1
        """,
        (DEFAULT_TENANT_ID, str(int(product_id)), EVENT_TRANSACTION_PAID),
    ).fetchone()
    return dict(row) if row else None


def _get_delivery(conn: Any, delivery_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM external_push_delivery WHERE delivery_id = %s LIMIT 1", (_text(delivery_id),)).fetchone()
    return dict(row) if row else None


def _update_delivery_result(
    conn: Any,
    delivery_id: str,
    *,
    status: str,
    attempt_count: int,
    request_url: str,
    request_headers: dict[str, Any],
    request_body: dict[str, Any],
    response_status: int | None,
    response_body: str,
    error_message: str,
    next_retry_at: str | None,
) -> dict[str, Any]:
    return build_external_push_delivery_projection_port().update_delivery_result_dbapi(
        conn,
        delivery_id=delivery_id,
        status=status,
        attempt_count=attempt_count,
        request_url=request_url,
        request_headers=request_headers,
        request_body=request_body,
        response_status=response_status,
        response_body=response_body,
        error_message=error_message,
        next_retry_at=next_retry_at,
    )


def _create_test_delivery(conn: Any, *, config: dict[str, Any], product_id: int, request_url: str) -> dict[str, Any]:
    return build_external_push_delivery_projection_port().create_test_delivery_dbapi(
        conn,
        tenant_id=DEFAULT_TENANT_ID,
        config_id=int(config.get("id") or 0),
        target_id=str(int(product_id)),
        product_id=int(product_id),
        request_url=request_url,
    )


def _create_order_delivery_once(
    conn: Any,
    *,
    config: dict[str, Any],
    order: dict[str, Any],
    product: dict[str, Any],
    request_url: str,
) -> dict[str, Any]:
    return build_external_push_delivery_projection_port().create_order_delivery_once_dbapi(
        conn,
        tenant_id=DEFAULT_TENANT_ID,
        config_id=int(config.get("id") or 0),
        event_type=EVENT_TRANSACTION_PAID,
        target_id=str(int(product.get("id") or 0)),
        order_id=int(order.get("id") or 0),
        product_id=int(product.get("id") or 0),
        request_url=request_url,
    )


def _extract_external_effect_job_id(delivery: dict[str, Any]) -> int | None:
    response_body = _text(delivery.get("response_body"))
    if not response_body:
        return None
    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict):
        try:
            return int(payload.get("external_effect_job_id") or 0) or None
        except (TypeError, ValueError):
            return None
    if "external_effect_job_id=" in response_body:
        try:
            return int(response_body.rsplit("external_effect_job_id=", 1)[-1].strip())
        except (TypeError, ValueError):
            return None
    return None


def plan_order_paid_external_push_effect(
    conn: Any,
    *,
    order: dict[str, Any],
    transaction: dict[str, Any] | None = None,
    outbox: dict[str, Any] | None = None,
    source_module: str = "commerce.external_push_admin",
    source_route: str = "commerce.external_push_admin.plan_order_paid_external_push_effect",
) -> dict[str, Any]:
    """Create one configured External Effect Queue job for an order paid webhook.

    The order push must be config-driven. If the product has no enabled external
    push config, this returns a skipped result and intentionally does not create
    a half-populated webhook job.
    """

    order_payload = dict(order or {})
    product = _get_product_for_order(conn, order_payload)
    product_id = int(product.get("id") or 0)
    if not product_id:
        return {"ok": True, "queued": False, "skipped": True, "reason": "product_not_found"}
    config = _get_product_config(conn, product_id)
    if not config:
        return {"ok": True, "queued": False, "skipped": True, "reason": "external_push_config_not_found", "product_id": product_id}
    if not bool(config.get("enabled")):
        return {"ok": True, "queued": False, "skipped": True, "reason": "external_push_config_disabled", "config_id": config.get("id")}
    webhook_url = _text(config.get("webhook_url"))
    if not webhook_url:
        return {"ok": True, "queued": False, "skipped": True, "reason": "external_push_webhook_url_missing", "config_id": config.get("id")}
    delivery = _create_order_delivery_once(conn, config=config, order=order_payload, product=product, request_url=webhook_url)
    existing_job_id = _extract_external_effect_job_id(delivery)
    if existing_job_id:
        return {
            "ok": True,
            "queued": True,
            "deduped": True,
            "delivery": _public_delivery(delivery),
            "external_effect_job_id": existing_job_id,
            "real_external_call_executed": False,
        }
    payload = _build_external_push_payload(
        EVENT_TRANSACTION_PAID,
        order_payload,
        product,
        _public_config(config),
        delivery_id=delivery["delivery_id"],
        phone_number=_resolve_order_mobile(conn, order_payload),
    )
    payload["transaction"] = {
        "transaction_id": _text((transaction or {}).get("transaction_id")),
        "trade_state": _text((transaction or {}).get("trade_state")),
        "success_time": _text((transaction or {}).get("success_time")),
    }
    if outbox:
        payload["domain_event_outbox_id"] = outbox.get("id")
    result = _attempt_delivery(conn, delivery, config=config, payload=payload, source_module=source_module, source_route=source_route)
    result["source_module"] = source_module
    result["source_route"] = source_route
    return result


def _attempt_delivery(
    conn: Any,
    delivery: dict[str, Any],
    *,
    config: dict[str, Any],
    payload: dict[str, Any],
    source_module: str = "commerce.external_push_admin",
    source_route: str = "commerce.external_push_admin._attempt_delivery",
) -> dict[str, Any]:
    delivery_id = _text(delivery.get("delivery_id"))
    event_type = _text(delivery.get("event_type")) or EVENT_TRANSACTION_PAID
    raw_body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    headers = {
        "Content-Type": "application/json",
        "X-AICRM-Event": event_type,
        "X-AICRM-Delivery-Id": delivery_id,
        "X-AICRM-Timestamp": timestamp,
    }
    secret = _text(config.get("secret"))
    headers["X-AICRM-Signature"] = _sign_webhook_payload(secret, timestamp, raw_body) if secret else ""
    next_attempt = int(delivery.get("attempt_count") or 0) + 1
    request_url = _text(config.get("webhook_url") or delivery.get("request_url"))
    try:
        final_url = resolve_and_validate_public_https_url(request_url)
        effect_type = WEBHOOK_GENERIC_PUSH if event_type == EVENT_EXTERNAL_PUSH_TEST else WEBHOOK_ORDER_PAID_PUSH
        job = ExternalEffectService().plan_effect(
            effect_type=effect_type,
            adapter_name="outbound_webhook",
            operation="post",
            target_type="external_push_delivery",
            target_id=delivery_id,
            business_type="commerce_order" if event_type != EVENT_EXTERNAL_PUSH_TEST else "commerce_product_external_push_test",
            business_id=_text(delivery.get("order_id") or delivery.get("product_id") or delivery_id),
            payload={
                "webhook_url": final_url,
                "body": payload,
                "headers": headers,
                "legacy_delivery_id": delivery_id,
                "event_type": event_type,
            },
            payload_summary={
                "event_type": event_type,
                "delivery_id": delivery_id,
                "target_url_present": bool(final_url),
                "header_count": len(headers),
                "body_type": type(payload).__name__,
                "external_effect_queue_required": True,
            },
            context=CommandContext(
                actor_id="commerce_external_push",
                actor_type="system",
                request_id=delivery_id,
                trace_id=_text(delivery.get("delivery_id")),
                source_route=source_route,
            ),
            source_module=source_module,
            source_event_id=delivery_id,
            source_command_id=delivery_id,
            idempotency_key=f"commerce-external-push:{delivery_id}",
            execution_mode="execute",
            status="queued",
            connection=conn,
        )
        updated = _update_delivery_result(
            conn,
            delivery_id,
            status="retrying",
            attempt_count=next_attempt,
            request_url=final_url,
            request_headers=_redact_sensitive_fields(headers),
            request_body=_redact_sensitive_fields(payload),
            response_status=None,
            response_body=json.dumps({"external_effect_job_id": job.get("id")}, ensure_ascii=False),
            error_message="external_effect_job_queued",
            next_retry_at="",
        )
        return {
            "ok": True,
            "delivery": _public_delivery(updated),
            "external_effect_job_id": job.get("id"),
            "external_effect_required": True,
            "real_external_call_executed": False,
            "reason": "queued_external_effect_job",
        }
    except Exception:
        # The caller owns the transaction. Raising rolls back both the delivery
        # projection and the External Effect job, so a consumer retry starts
        # from a clean durability boundary.
        raise


def list_order_external_push_state(order_id: int) -> dict[str, Any]:
    with _connect() as conn:
        order = _get_order(conn, int(order_id))
        if not order:
            raise ExternalPushAdminError("订单不存在")
        delivery_rows = conn.execute(
            """
            SELECT *
            FROM external_push_delivery
            WHERE tenant_id = %s
              AND order_id = %s
            ORDER BY created_at DESC, id DESC
            """,
            (DEFAULT_TENANT_ID, int(order_id)),
        ).fetchall()
        outbox_rows = conn.execute(
            """
            SELECT *
            FROM domain_event_outbox
            WHERE tenant_id = %s
              AND event_type = %s
              AND aggregate_type = 'wechat_pay_order'
              AND aggregate_id = %s
            ORDER BY created_at DESC, id DESC
            """,
            (DEFAULT_TENANT_ID, EVENT_TRANSACTION_PAID, str(int(order_id))),
        ).fetchall()
    return {
        "ok": True,
        "order_id": int(order_id),
        "outbox": [_public_outbox(dict(row)) for row in outbox_rows],
        "items": [_public_delivery(dict(row)) for row in delivery_rows],
    }


def send_product_external_push_test(product_id: int) -> dict[str, Any]:
    with _connect() as conn:
        product = _get_product(conn, int(product_id))
        if not product:
            raise ExternalPushAdminError("商品不存在")
        config = _get_product_config(conn, int(product_id))
        if not config:
            raise ExternalPushAdminError("请先保存外部推送配置")
        config_public = _public_config(config)
        if not config_public.get("enabled"):
            raise ExternalPushAdminError("外部推送未启用")
        webhook_url = _text(config.get("webhook_url"))
        if not webhook_url:
            raise ExternalPushAdminError("webhook_url is required")
        delivery = _create_test_delivery(conn, config=config, product_id=int(product_id), request_url=webhook_url)
        payload = _build_external_push_payload(EVENT_EXTERNAL_PUSH_TEST, {}, product, config_public, delivery_id=delivery["delivery_id"])
        result = _attempt_delivery(conn, delivery, config=config, payload=payload)
        conn.commit()
        return result


def retry_order_delivery(order_id: int, delivery_id: str) -> dict[str, Any]:
    with _connect() as conn:
        order = _get_order(conn, int(order_id))
        if not order:
            raise ExternalPushAdminError("订单不存在")
        delivery = _get_delivery(conn, delivery_id)
        if not delivery or int(delivery.get("order_id") or 0) != int(order_id):
            raise ExternalPushAdminError("外推记录不存在")
        if _text(delivery.get("status")) not in {"failed", "retrying", "gave_up"}:
            raise ExternalPushAdminError("只能重试 failed / retrying / gave_up 状态")
        config = _get_config(conn, int(delivery.get("config_id") or 0)) or {}
        product = _get_product(conn, int(delivery.get("product_id") or 0)) or _get_product_for_order(conn, order)
        payload = _build_external_push_payload(
            EVENT_TRANSACTION_PAID,
            order,
            product,
            _public_config(config),
            delivery_id=delivery["delivery_id"],
            phone_number=_resolve_order_mobile(conn, order),
        )
        result = _attempt_delivery(conn, delivery, config=config, payload=payload)
        conn.commit()
        return result
