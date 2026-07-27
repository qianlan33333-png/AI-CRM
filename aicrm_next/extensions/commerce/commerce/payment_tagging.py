from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.external_effects import (
    ExternalEffectService,
    WECOM_CONTACT_TAG_MARK,
)
from aicrm_next.platform_foundation.internal_events.models import (
    InternalEvent,
    InternalEventConsumerResult,
    InternalEventConsumerRun,
)


PRODUCT_CODE = "prd_20260713083438_75670b"
PRODUCT_CODES = frozenset(
    {
        "prd_20260707050545_291025",
        PRODUCT_CODE,
    }
)
TAG_ID = "etbNXyCwAAUZm79s_QWeVnr3fktQn0mg"
OWNER_USERID = "HuangYouCan"
CONSUMER_NAME = "product_paid_wecom_tag_consumer"

IdentityResolver = Callable[[dict[str, Any], str], dict[str, Any]]


def _text(value: Any) -> str:
    return str(value or "").strip()


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


def resolve_payment_tag_identity(conn: Any, order: dict[str, Any], owner_userid: str) -> dict[str, Any]:
    unionid = _text(order.get("unionid"))
    external_userid = _text(order.get("external_userid"))
    if not unionid and not external_userid:
        return {"ok": False, "reason": "payment_identity_pending"}

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
    match = next(
        (
            row
            for row in mappings
            if _text(row.get("status")) == "active"
            and _text(row.get("external_userid"))
        ),
        None,
    )
    if match:
        return {
            "ok": True,
            "external_userid": _text(match.get("external_userid")),
            "follow_user_userid": owner_userid,
        }
    if mappings:
        return {
            "ok": False,
            "reason": "wecom_identity_inactive",
        }
    return {"ok": False, "reason": "wecom_identity_pending"}


def product_paid_wecom_tag_consumer(
    event: InternalEvent,
    run: InternalEventConsumerRun,
    *,
    identity_resolver: IdentityResolver | None = None,
    external_effects: ExternalEffectService | None = None,
) -> InternalEventConsumerResult:
    order = _order_from_event(event)
    product_code = _text(order.get("product_code"))
    out_trade_no = _text(order.get("out_trade_no") or event.aggregate_id)
    if product_code not in PRODUCT_CODES:
        return InternalEventConsumerResult(
            status="skipped",
            request_summary={"event_id": event.event_id, "product_code": product_code},
            response_summary={"skipped": True, "reason": "product_rule_not_matched"},
            result_summary={"reason": "product_rule_not_matched"},
        )
    if _text(order.get("status")).lower() != "paid" and _text(order.get("trade_state")).upper() != "SUCCESS":
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
            response_summary={"paid": False},
            error_code="order_not_paid",
            error_message="order is not paid yet",
            retry_after_seconds=300,
        )
    if identity_resolver is None:
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
            response_summary={"identity_resolved": False},
            error_code="identity_resolver_not_configured",
            error_message="production payment tag identity resolver is required",
            retry_after_seconds=300,
        )

    try:
        identity = dict(identity_resolver(order, OWNER_USERID) or {})
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
        reason = _text(identity.get("reason")) or "wecom_identity_pending"
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
            response_summary={"identity_resolved": False, "reason": reason},
            result_summary={"reason": reason},
            error_code=reason,
            error_message="canonical HuangYouCan WeCom owner relation is not ready",
            retry_after_seconds=300,
        )

    external_userid = _text(identity.get("external_userid"))
    effects = external_effects or ExternalEffectService()
    business_id = out_trade_no or _text(order.get("id") or event.aggregate_id)
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
                    "follow_user_userid": OWNER_USERID,
                    "tag_ids": [TAG_ID],
                    "product_code": product_code,
                    "out_trade_no": out_trade_no,
                },
                payload_summary={
                    "product_code": product_code,
                    "tag_count": 1,
                    "owner_userid": OWNER_USERID,
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
                idempotency_key=f"payment.succeeded:{business_id}:wecom-tag:{TAG_ID}:{OWNER_USERID}",
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
            "tag_id": TAG_ID,
            "owner_userid": OWNER_USERID,
            "external_effect_job_id": int(job.get("id") or 0),
        },
    )


__all__ = [
    "CONSUMER_NAME",
    "OWNER_USERID",
    "PRODUCT_CODE",
    "PRODUCT_CODES",
    "TAG_ID",
    "product_paid_wecom_tag_consumer",
    "resolve_payment_tag_identity",
]
