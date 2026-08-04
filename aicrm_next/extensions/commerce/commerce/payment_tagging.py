from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from typing import Any

from aicrm_next.platform.platform_foundation.command_bus import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import (
    ExternalEffectService,
    WECOM_CONTACT_TAG_MARK,
)
from aicrm_next.platform.platform_foundation.internal_events.models import (
    InternalEvent,
    InternalEventConsumerResult,
    InternalEventConsumerRun,
)


CONSUMER_NAME = "product_paid_wecom_tag_consumer"

IdentityResolver = Callable[[dict[str, Any], str], dict[str, Any]]
ProductTagConfigResolver = Callable[[str], dict[str, Any]]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _tag_ids(value: Any) -> list[str]:
    values = value if isinstance(value, list) else []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        tag_id = _text(raw)
        if not tag_id or tag_id in seen:
            continue
        seen.add(tag_id)
        normalized.append(tag_id)
    return normalized[:100]


def _order_from_event(event: InternalEvent) -> dict[str, Any]:
    payload = dict(event.payload_json or {})
    order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
    return dict(order or {})


def _context(event: InternalEvent) -> CommandContext:
    return CommandContext(
        actor_id=event.actor_id or "payment_tag_consumer",
        actor_type=event.actor_type or "system",
        trace_id=event.trace_id or event.event_id,
        request_id=event.request_id,
        source_route=f"/internal-events/payment.succeeded/{CONSUMER_NAME}",
    )


def resolve_product_wecom_tagging_config(conn: Any, product_code: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT wecom_tagging_json
        FROM wechat_pay_products
        WHERE product_code = %s
        LIMIT 1
        """,
        (_text(product_code),),
    ).fetchone()
    raw = (row or {}).get("wecom_tagging_json")
    payload = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(payload.get("enabled")),
        "tag_ids": _tag_ids(payload.get("tag_ids")),
        "owner_userid": _text(payload.get("owner_userid")),
    }


def resolve_payment_tag_identity(conn: Any, order: dict[str, Any], owner_userid: str = "") -> dict[str, Any]:
    unionid = _text(order.get("unionid"))
    external_userid = _text(order.get("external_userid"))
    if not unionid and not external_userid:
        return {"ok": False, "reason": "payment_wecom_identity_missing"}

    rows = conn.execute(
        """
        SELECT external_userid, follow_user_userid, status
        FROM wecom_external_contact_identity_map
        WHERE (%s <> '' AND unionid = %s)
           OR (%s <> '' AND external_userid = %s)
        ORDER BY
            CASE WHEN status = 'active' THEN 0 ELSE 1 END,
            updated_at DESC NULLS LAST
        """,
        (unionid, unionid, external_userid, external_userid),
    ).fetchall()
    mappings = [dict(row) for row in rows]
    requested_owner = _text(owner_userid)
    match = next(
        (
            row
            for row in mappings
            if _text(row.get("status")) == "active"
            and _text(row.get("external_userid"))
            and _text(row.get("follow_user_userid"))
            and (not requested_owner or _text(row.get("follow_user_userid")) == requested_owner)
        ),
        None,
    )
    if match:
        return {
            "ok": True,
            "external_userid": _text(match.get("external_userid")),
            "follow_user_userid": _text(match.get("follow_user_userid")),
        }
    if not mappings:
        return {"ok": False, "reason": "wecom_contact_not_found"}
    if not any(_text(row.get("status")) == "active" for row in mappings):
        return {"ok": False, "reason": "wecom_contact_inactive"}
    if requested_owner:
        return {"ok": False, "reason": "wecom_owner_relation_missing"}
    if not any(_text(row.get("external_userid")) for row in mappings):
        return {"ok": False, "reason": "wecom_external_userid_missing"}
    return {"ok": False, "reason": "wecom_follow_user_missing"}


def _skipped(event: InternalEvent, product_code: str, out_trade_no: str, reason: str) -> InternalEventConsumerResult:
    return InternalEventConsumerResult(
        status="skipped",
        request_summary={
            "event_id": event.event_id,
            "product_code": product_code,
            "out_trade_no": out_trade_no,
        },
        response_summary={"skipped": True, "reason": reason, "retry_scheduled": False},
        result_summary={"reason": reason},
    )


def product_paid_wecom_tag_consumer(
    event: InternalEvent,
    run: InternalEventConsumerRun,
    *,
    config_resolver: ProductTagConfigResolver | None = None,
    identity_resolver: IdentityResolver | None = None,
    external_effects: ExternalEffectService | None = None,
) -> InternalEventConsumerResult:
    del run
    order = _order_from_event(event)
    product_code = _text(order.get("product_code"))
    out_trade_no = _text(order.get("out_trade_no") or event.aggregate_id)
    if _text(order.get("status")).lower() != "paid" and _text(order.get("trade_state")).upper() != "SUCCESS":
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
            response_summary={"paid": False},
            error_code="order_not_paid",
            error_message="order is not paid yet",
            retry_after_seconds=300,
        )
    if config_resolver is None:
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
            response_summary={"product_tag_config_loaded": False},
            error_code="product_tag_config_resolver_not_configured",
            error_message="production product tag config resolver is required",
            retry_after_seconds=300,
        )
    try:
        config = dict(config_resolver(product_code) or {})
    except Exception as exc:
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
            response_summary={"product_tag_config_loaded": False},
            error_code="product_tag_config_read_failed",
            error_message=str(exc)[:500],
            retry_after_seconds=300,
        )
    tag_ids = _tag_ids(config.get("tag_ids"))
    if not config.get("enabled") or not tag_ids:
        return _skipped(event, product_code, out_trade_no, "product_wecom_tagging_disabled")
    if identity_resolver is None:
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
            response_summary={"identity_resolved": False},
            error_code="identity_resolver_not_configured",
            error_message="production payment tag identity resolver is required",
            retry_after_seconds=300,
        )

    configured_owner = _text(config.get("owner_userid"))
    try:
        identity = dict(identity_resolver(order, configured_owner) or {})
    except Exception as exc:
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
            response_summary={"identity_resolved": False},
            error_code="payment_tag_identity_read_failed",
            error_message=str(exc)[:500],
            retry_after_seconds=300,
        )
    if not identity.get("ok"):
        reason = _text(identity.get("reason")) or "wecom_contact_not_found"
        return _skipped(event, product_code, out_trade_no, reason)

    external_userid = _text(identity.get("external_userid"))
    owner_userid = _text(identity.get("follow_user_userid"))
    if not external_userid:
        return _skipped(event, product_code, out_trade_no, "wecom_external_userid_missing")
    if not owner_userid:
        return _skipped(event, product_code, out_trade_no, "wecom_follow_user_missing")

    effects = external_effects or ExternalEffectService()
    business_id = out_trade_no or _text(order.get("id") or event.aggregate_id)
    tag_fingerprint = sha256("\n".join(tag_ids).encode("utf-8")).hexdigest()[:16]
    try:
        existing = effects.find_existing_job(
            effect_type=WECOM_CONTACT_TAG_MARK,
            target_type="external_user",
            target_id=external_userid,
            business_type="commerce_payment_tag",
            business_id=business_id,
        )
        if existing is not None:
            job = existing.to_dict()
            created = False
        else:
            job = effects.plan_effect(
                effect_type=WECOM_CONTACT_TAG_MARK,
                adapter_name="wecom_tag",
                operation="tag_mark",
                target_type="external_user",
                target_id=external_userid,
                business_type="commerce_payment_tag",
                business_id=business_id,
                payload={
                    "external_userid": external_userid,
                    "follow_user_userid": owner_userid,
                    "tag_ids": tag_ids,
                    "product_code": product_code,
                    "out_trade_no": out_trade_no,
                },
                payload_summary={
                    "product_code": product_code,
                    "tag_count": len(tag_ids),
                    "owner_userid": owner_userid,
                    "external_userid_present": True,
                },
                context=_context(event),
                source_module="commerce.payment_tagging",
                source_event_id=event.event_id,
                source_command_id=event.source_command_id,
                risk_level="high",
                requires_approval=False,
                execution_mode="execute",
                status="queued",
                idempotency_key=f"payment.succeeded:{business_id}:wecom-tag:{tag_fingerprint}:{owner_userid}",
            )
            created = True
    except Exception as exc:
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
            response_summary={"external_effect_job_created": False},
            error_code="payment_tag_effect_plan_failed",
            error_message=str(exc)[:500],
            retry_after_seconds=300,
        )

    return InternalEventConsumerResult(
        status="succeeded",
        request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
        response_summary={
            "external_effect_job_created": created,
            "external_effect_job_reused": not created,
            "external_effect_job_id": int(job.get("id") or 0),
            "status": _text(job.get("status")),
        },
        result_summary={
            "product_code": product_code,
            "tag_count": len(tag_ids),
            "owner_userid": owner_userid,
            "external_effect_job_id": int(job.get("id") or 0),
        },
    )


__all__ = [
    "CONSUMER_NAME",
    "product_paid_wecom_tag_consumer",
    "resolve_payment_tag_identity",
    "resolve_product_wecom_tagging_config",
]
