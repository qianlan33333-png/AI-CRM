from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from aicrm_next.platform_foundation.internal_events import (
    InternalEvent,
    InternalEventConsumerResult,
    InternalEventConsumerRun,
)
from aicrm_next.shared.db_session import get_session_factory

from .models import customer_timeline_event_next


TIMELINE_PROJECTION_CONSUMER = "customer_timeline_projection_consumer"
TIMELINE_SOURCE_EVENTS = (
    "channel_entry.entered",
    "questionnaire.submitted",
    "payment.succeeded",
    "radar.opened",
    "commerce.product_enrolled",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        token = _text(value).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(token) if token else datetime.now(timezone.utc)
        except ValueError:
            parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stable_event_id(prefix: str, source_key: Any) -> str:
    key = _text(source_key)
    candidate = f"{prefix}:{key}"
    if len(candidate) <= 128:
        return candidate
    return f"{prefix}:sha256:{hashlib.sha256(key.encode('utf-8')).hexdigest()}"


class CustomerTimelineProjectionRepository:
    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def upsert(self, item: dict[str, Any]) -> dict[str, Any]:
        row = {
            "event_id": _text(item.get("event_id")),
            "unionid": _text(item.get("unionid")),
            "event_type": _text(item.get("event_type")),
            "event_time": _datetime(item.get("event_time")),
            "title": _text(item.get("title")),
            "summary": _text(item.get("summary")),
            "source_table": _text(item.get("source_table")),
            "source_id": _text(item.get("source_id")),
            "metadata_json": dict(item.get("metadata") or {}),
            "created_at": _datetime(item.get("created_at")),
        }
        if not row["event_id"] or not row["unionid"] or not row["event_type"]:
            raise ValueError("timeline projection identity is required")
        with self._session_factory() as session:
            dialect = str(session.get_bind().dialect.name)
            insert_builder = sqlite_insert if dialect == "sqlite" else postgresql_insert
            statement = insert_builder(customer_timeline_event_next).values(**row)
            statement = statement.on_conflict_do_update(
                index_elements=[customer_timeline_event_next.c.event_id],
                set_={
                    "unionid": statement.excluded.unionid,
                    "event_type": statement.excluded.event_type,
                    "event_time": statement.excluded.event_time,
                    "title": statement.excluded.title,
                    "summary": statement.excluded.summary,
                    "source_table": statement.excluded.source_table,
                    "source_id": statement.excluded.source_id,
                    "metadata_json": statement.excluded.metadata_json,
                },
            )
            session.execute(statement)
            session.commit()
        return {"ok": True, "projected": True}


def timeline_projection_from_internal_event(event: InternalEvent) -> dict[str, Any] | None:
    payload = dict(event.payload_json or {})
    event_type = _text(event.event_type)
    occurred_at = _text(event.occurred_at) or _text(event.created_at)

    if event_type == "channel_entry.entered":
        unionid = _text(payload.get("unionid")) or (_text(event.subject_id) if event.subject_type == "unionid" else "")
        if not unionid:
            return None
        channel_id = _text(payload.get("channel_id"))
        channel_name = _text(payload.get("channel_name"))
        channel_code = _text(payload.get("channel_code") or payload.get("scene_value"))
        source_key = event.source_command_id or payload.get("channel_contact_id") or event.idempotency_key or event.event_id
        return {
            "event_id": _stable_event_id("channel_entry", source_key),
            "unionid": unionid,
            "event_type": "channel_entry",
            "event_time": occurred_at,
            "title": "扫码进入渠道" + (f" · {channel_name}" if channel_name else ""),
            "summary": channel_name or (f"渠道 {channel_code}" if channel_code else "通过渠道码进入"),
            "source_table": "automation_channel_contact",
            "source_id": _text(payload.get("channel_contact_id") or channel_id),
            "metadata": {"channel_name": channel_name, "channel_code": channel_code},
        }

    if event_type == "questionnaire.submitted":
        questionnaire = dict(payload.get("questionnaire") or {})
        submission = dict(payload.get("submission") or {})
        unionid = _text(submission.get("unionid")) or (_text(event.subject_id) if event.subject_type == "unionid" else "")
        if not unionid:
            return None
        submission_id = _text(submission.get("submission_id") or event.aggregate_id)
        questionnaire_id = _text(questionnaire.get("id") or submission.get("questionnaire_id"))
        title = _text(questionnaire.get("title")) or "问卷"
        return {
            "event_id": _stable_event_id("questionnaire", submission_id),
            "unionid": unionid,
            "event_type": "questionnaire_submitted",
            "event_time": _text(submission.get("submitted_at")) or occurred_at,
            "title": f"提交问卷 · {title}",
            "summary": "已完成问卷提交",
            "source_table": "questionnaire_submissions",
            "source_id": submission_id,
            "metadata": {"questionnaire_id": questionnaire_id, "questionnaire_title": title},
        }

    if event_type in {"payment.succeeded", "commerce.product_enrolled"}:
        order = dict(payload.get("order") or payload.get("enrollment") or payload)
        unionid = _text(order.get("unionid") or payload.get("unionid"))
        if not unionid and event.subject_type == "unionid":
            unionid = _text(event.subject_id)
        if not unionid:
            return None
        out_trade_no = _text(order.get("out_trade_no") or payload.get("out_trade_no"))
        source_table = _text(payload.get("source_table"))
        source_key = _text(payload.get("source_id")) or out_trade_no or _text(order.get("order_id") or order.get("id")) or event.idempotency_key or event.event_id
        product_id = _text(order.get("product_id") or order.get("trade_product_id") or order.get("product_code"))
        product_title = _text(order.get("product_name") or order.get("product_title") or order.get("title")) or "商品"
        product_type = _text(order.get("product_type") or payload.get("product_type")) or "standard_product"
        return {
            "event_id": _stable_event_id(
                "product",
                f"payment:{out_trade_no}"
                if out_trade_no
                else f"wechat_shop:{source_key}"
                if source_table == "wechat_shop_orders"
                else f"service_period:{source_key}"
                if source_table == "service_period_events"
                else source_key,
            ),
            "unionid": unionid,
            "event_type": "product_enrolled",
            "event_time": _text(order.get("paid_at") or order.get("activated_at") or payload.get("occurred_at")) or occurred_at,
            "title": f"报名或支付成功 · {product_title}",
            "summary": "已完成商品报名或支付",
            "source_table": source_table or ("wechat_pay_orders" if event_type == "payment.succeeded" else "commerce_enrollment"),
            "source_id": source_key,
            "metadata": {"product_id": product_id, "product_title": product_title, "product_type": product_type},
        }

    if event_type == "radar.opened":
        unionid = _text(payload.get("unionid")) or (_text(event.subject_id) if event.subject_type == "unionid" else "")
        if not unionid:
            return None
        click_event_id = _text(payload.get("click_event_id") or event.aggregate_id)
        title = _text(payload.get("radar_title")) or "雷达内容"
        target_type = _text(payload.get("target_type")) or "link"
        return {
            "event_id": _stable_event_id("radar", click_event_id),
            "unionid": unionid,
            "event_type": "radar_opened",
            "event_time": _text(payload.get("opened_at")) or occurred_at,
            "title": f"打开雷达 · {title}",
            "summary": "已打开追踪链接",
            "source_table": "radar_click_events",
            "source_id": click_event_id,
            "metadata": {"radar_id": _text(payload.get("radar_id")), "radar_title": title, "target_type": target_type},
        }
    return None


def customer_timeline_projection_consumer(
    event: InternalEvent,
    run: InternalEventConsumerRun,
    *,
    repository: CustomerTimelineProjectionRepository | None = None,
) -> InternalEventConsumerResult:
    try:
        projection = timeline_projection_from_internal_event(event)
        if projection is None:
            result = {"ok": True, "projected": False, "reason": "unionid_missing_or_event_unsupported"}
        else:
            result = (repository or CustomerTimelineProjectionRepository()).upsert(projection)
        ok = bool(result.get("ok"))
        return InternalEventConsumerResult(
            status="succeeded" if ok else "failed_retryable",
            request_summary={"event_type": event.event_type, "consumer_name": run.consumer_name},
            response_summary={"ok": ok, "projected": bool(result.get("projected")), "reason": _text(result.get("reason"))},
            result_summary={"ok": ok, "projected": bool(result.get("projected"))},
            error_code="" if ok else "customer_timeline_projection_failed",
            error_message="" if ok else _text(result.get("error")),
            retry_after_seconds=None if ok else 30,
        )
    except Exception as exc:
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary={"event_type": event.event_type, "consumer_name": run.consumer_name},
            response_summary={"ok": False},
            result_summary={"ok": False},
            error_code="customer_timeline_projection_exception",
            error_message=_text(exc)[:500],
            retry_after_seconds=30,
        )


__all__ = [
    "TIMELINE_PROJECTION_CONSUMER",
    "TIMELINE_SOURCE_EVENTS",
    "CustomerTimelineProjectionRepository",
    "customer_timeline_projection_consumer",
    "timeline_projection_from_internal_event",
]
