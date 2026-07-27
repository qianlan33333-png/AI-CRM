from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

from aicrm_next.external_push.delivery_projection_port import build_external_push_delivery_projection_port
from aicrm_next.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform_foundation.internal_events.outbox import enqueue_transactional_internal_event_outbox
from aicrm_next.platform_foundation.internal_events.payment import build_payment_succeeded_event_request
from aicrm_next.platform_foundation.internal_events.refund import build_refund_succeeded_event_request
from aicrm_next.shared.db_session import connect_raw_postgres
from aicrm_next.shared.runtime import raw_database_url


_OPEN_CONTINUATION_STATUSES = "'pending', 'running', 'failed_retryable'"

# The count-only reconciliation became a production release gate when promotion
# run 29240024773 completed.  Keep the boundary explicit: deriving it from the
# first successful outbox/effect row hides the first real gap on a fresh tenant.
_FULFILLMENT_RECONCILIATION_CUTOVER_AT_SQL = "TIMESTAMPTZ '2026-07-13 09:46:09+00'"

_ANOMALY_QUERIES = {
    "paid_without_payment_outbox": f"""
        SELECT o.id
        FROM wechat_pay_orders o
        WHERE (o.status = 'paid' OR o.trade_state = 'SUCCESS')
          AND COALESCE(o.paid_at, o.created_at) >= {_FULFILLMENT_RECONCILIATION_CUTOVER_AT_SQL}
          AND NOT EXISTS (
              SELECT 1 FROM internal_event_outbox ieo
              WHERE ieo.tenant_id = 'aicrm'
                AND ieo.idempotency_key = 'payment.succeeded:' || o.out_trade_no
          )
    """,
    "paid_service_product_without_entitlement_or_open_consumer": f"""
        SELECT o.id
        FROM wechat_pay_orders o
        JOIN wechat_pay_products tp ON tp.product_code = o.product_code
        JOIN service_period_products sp ON sp.trade_product_id = tp.id AND sp.deleted = FALSE
        WHERE (o.status = 'paid' OR o.trade_state = 'SUCCESS')
          AND NOT EXISTS (
              SELECT 1 FROM service_period_events spe
              WHERE spe.out_trade_no = o.out_trade_no
                AND spe.event_type IN ('activated', 'renewed')
          )
          AND NOT EXISTS (
              SELECT 1 FROM service_period_entitlements ent
              WHERE ent.last_out_trade_no = o.out_trade_no
          )
          AND NOT EXISTS (
              SELECT 1 FROM internal_event_outbox ieo
              WHERE ieo.tenant_id = 'aicrm'
                AND ieo.idempotency_key = 'payment.succeeded:' || o.out_trade_no
                AND ieo.status IN ({_OPEN_CONTINUATION_STATUSES})
          )
          AND NOT EXISTS (
              SELECT 1
              FROM internal_event ie
              JOIN internal_event_consumer_run run ON run.event_id = ie.event_id
              WHERE ie.tenant_id = 'aicrm'
                AND ie.idempotency_key = 'payment.succeeded:' || o.out_trade_no
                AND run.consumer_name = 'service_period_entitlement_consumer'
                AND run.status IN ({_OPEN_CONTINUATION_STATUSES})
          )
    """,
    "successful_full_refund_with_active_entitlement": """
        SELECT o.id
        FROM wechat_pay_orders o
        WHERE (
            o.refund_status = 'full_refunded'
            OR (o.amount_total > 0 AND o.refunded_amount_total >= o.amount_total)
        )
          AND EXISTS (
              SELECT 1
              FROM service_period_events spe
              JOIN service_period_entitlements ent
                ON ent.id = spe.entitlement_id
               AND ent.status = 'active'
              WHERE spe.out_trade_no = o.out_trade_no
                AND spe.event_type IN ('activated', 'renewed')
          )
    """,
    "refund_request_without_effect": f"""
        SELECT r.id
        FROM wechat_pay_refunds r
        WHERE LOWER(COALESCE(r.status, '')) IN ('requested', 'queued')
          AND COALESCE(r.refund_id, '') = ''
          AND r.created_at >= {_FULFILLMENT_RECONCILIATION_CUTOVER_AT_SQL}
          AND NOT EXISTS (
              SELECT 1 FROM external_effect_job job
              WHERE job.tenant_id = 'aicrm'
                AND job.effect_type = 'payment.wechat.refund.request'
                AND job.target_type = 'wechat_pay_refund'
                AND job.target_id = r.out_refund_no
          )
    """,
    "duplicate_order_paid_effect": """
        SELECT d.order_id AS id
        FROM external_push_delivery d
        JOIN external_effect_job job
          ON job.target_type = 'external_push_delivery'
         AND job.target_id = d.delivery_id
         AND job.effect_type = 'webhook.order_paid.push'
        WHERE d.order_id > 0
        GROUP BY d.order_id
        HAVING COUNT(job.id) > 1
           AND COUNT(job.id) FILTER (
               WHERE job.status IN ('planned', 'approved', 'queued', 'dispatching', 'failed_retryable', 'unknown_after_dispatch')
           ) > 0
    """,
    "stale_succeeded_external_push_delivery_projection": """
        SELECT d.id
        FROM external_push_delivery d
        WHERE d.status <> 'success'
          AND EXISTS (
              SELECT 1
              FROM external_effect_job job
              WHERE job.target_type = 'external_push_delivery'
                AND job.target_id = d.delivery_id
                AND job.effect_type IN ('webhook.order_paid.push', 'webhook.generic.push')
                AND job.status = 'succeeded'
          )
    """,
    "legacy_domain_outbox_pending": """
        SELECT outbox.id
        FROM domain_event_outbox outbox
        WHERE outbox.event_type = 'transaction.paid'
          AND outbox.status IN ('pending', 'processing', 'failed')
    """,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _actor_hash(actor: str) -> str:
    normalized = _text(actor)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16] if normalized else ""


def _reason_hash(reason: str) -> str:
    normalized = _text(reason)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16] if normalized else ""


def _audited_request(request, *, actor_hash: str, reason: str):
    context = CommandContext(
        actor_id=f"repair:{actor_hash}",
        actor_type="operator",
        trace_id=request.context.trace_id,
        request_id=request.context.request_id,
        source_route="commerce.fulfillment_reconciliation.repair",
    )
    return replace(
        request,
        context=context,
        source_module="commerce.fulfillment_reconciliation",
        payload_summary={
            **dict(request.payload_summary or {}),
            "reconciliation_repair": True,
            "repair_actor_hash": actor_hash,
            "repair_reason": _text(reason)[:200],
        },
    )


class CommerceFulfillmentReconciliationService:
    """Diagnose commerce continuation gaps without calling consumers/providers."""

    def __init__(self, *, database_url: str = "") -> None:
        self._database_url = _text(database_url) or raw_database_url()

    def diagnose(self) -> dict[str, Any]:
        if not self._database_url:
            return self._result_error("database_url_required")
        from psycopg.rows import dict_row

        counts: dict[str, int] = {}
        sample_internal_ids: dict[str, list[int]] = {}
        with connect_raw_postgres(self._database_url) as conn:
            conn.row_factory = dict_row
            for name, query in _ANOMALY_QUERIES.items():
                row = conn.execute(
                    f"""
                    WITH anomalies AS ({query})
                    SELECT COUNT(*)::integer AS anomaly_count,
                           COALESCE(
                               (SELECT ARRAY_AGG(sample.id ORDER BY sample.id)
                                FROM (SELECT id FROM anomalies ORDER BY id LIMIT 20) sample),
                               ARRAY[]::bigint[]
                           ) AS sample_ids
                    FROM anomalies
                    """
                ).fetchone()
                counts[name] = int((row or {}).get("anomaly_count") or 0)
                sample_internal_ids[name] = [int(value) for value in ((row or {}).get("sample_ids") or [])]
        return {
            "ok": True,
            "mode": "count_only",
            "repair_supported": True,
            "has_anomalies": any(counts.values()),
            "counts": counts,
            "sample_internal_ids": sample_internal_ids,
            "database_mutation_performed": False,
            "consumer_executed": False,
            "real_external_call_executed": False,
            "pii_in_output": False,
        }

    def repair(
        self,
        *,
        actor: str,
        reason: str,
        limit: int = 100,
        projection_only: bool = False,
    ) -> dict[str, Any]:
        normalized_actor = _text(actor)
        normalized_reason = _text(reason)
        if not normalized_actor or not normalized_reason:
            return self._result_error("actor_and_reason_required")
        if not self._database_url:
            return self._result_error("database_url_required")
        bounded_limit = max(1, min(int(limit or 100), 500))
        actor_hash = _actor_hash(normalized_actor)
        before = self.diagnose()
        from psycopg.rows import dict_row

        repaired_payment_outbox = 0
        repaired_refund_outbox = 0
        repaired_external_push_delivery_projection = 0
        with connect_raw_postgres(self._database_url) as conn:
            conn.row_factory = dict_row
            paid_rows = (
                []
                if projection_only
                else conn.execute(
                    f"""
                    {_ANOMALY_QUERIES['paid_without_payment_outbox']}
                    ORDER BY o.id
                    LIMIT %s
                    FOR UPDATE OF o SKIP LOCKED
                    """,
                    (bounded_limit,),
                ).fetchall()
            )
            for row in paid_rows:
                order = dict(
                    conn.execute("SELECT * FROM wechat_pay_orders WHERE id = %s", (int(row["id"]),)).fetchone()
                    or {}
                )
                request = build_payment_succeeded_event_request(
                    order=order,
                    transaction=_json_object(order.get("notify_payload_json")),
                    domain_event_outbox_id=None,
                    source_route="commerce.fulfillment_reconciliation.repair",
                )
                if request is None:
                    continue
                enqueue_transactional_internal_event_outbox(
                    conn,
                    _audited_request(request, actor_hash=actor_hash, reason=normalized_reason),
                )
                repaired_payment_outbox += 1

            refund_rows = (
                []
                if projection_only
                else conn.execute(
                    f"""
                    {_ANOMALY_QUERIES['successful_full_refund_with_active_entitlement']}
                    ORDER BY o.id
                    LIMIT %s
                    FOR UPDATE OF o SKIP LOCKED
                    """,
                    (bounded_limit,),
                ).fetchall()
            )
            for row in refund_rows:
                order = dict(
                    conn.execute("SELECT * FROM wechat_pay_orders WHERE id = %s", (int(row["id"]),)).fetchone()
                    or {}
                )
                refund = dict(
                    conn.execute(
                        """
                        SELECT * FROM wechat_pay_refunds
                        WHERE order_id = %s AND UPPER(status) = 'SUCCESS'
                        ORDER BY updated_at DESC, id DESC LIMIT 1
                        """,
                        (int(row["id"]),),
                    ).fetchone()
                    or {}
                )
                request = build_refund_succeeded_event_request(
                    refund={
                        **refund,
                        "amount_total": refund.get("refund_amount_total"),
                        "order_refund_status": order.get("refund_status"),
                    },
                    order=order,
                    source_route="commerce.fulfillment_reconciliation.repair",
                )
                if request is None:
                    continue
                enqueue_transactional_internal_event_outbox(
                    conn,
                    _audited_request(request, actor_hash=actor_hash, reason=normalized_reason),
                )
                repaired_refund_outbox += 1

            stale_deliveries = conn.execute(
                """
                SELECT d.delivery_id, job.id AS external_effect_job_id,
                       CASE
                           WHEN COALESCE(job.result_summary_json->>'status_code', '') ~ '^[0-9]{3}$'
                           THEN (job.result_summary_json->>'status_code')::integer
                           ELSE NULL
                       END AS response_status
                FROM external_push_delivery d
                JOIN LATERAL (
                    SELECT candidate.id, candidate.result_summary_json
                    FROM external_effect_job candidate
                    WHERE candidate.target_type = 'external_push_delivery'
                      AND candidate.target_id = d.delivery_id
                      AND candidate.effect_type IN ('webhook.order_paid.push', 'webhook.generic.push')
                      AND candidate.status = 'succeeded'
                    ORDER BY candidate.completed_at DESC NULLS LAST, candidate.id DESC
                    LIMIT 1
                ) job ON TRUE
                WHERE d.status <> 'success'
                ORDER BY d.id
                LIMIT %s
                FOR UPDATE OF d SKIP LOCKED
                """,
                (bounded_limit,),
            ).fetchall()
            delivery_projection_port = build_external_push_delivery_projection_port()
            for row in stale_deliveries:
                updated = delivery_projection_port.reconcile_succeeded_dbapi(
                    conn,
                    delivery_id=row["delivery_id"],
                    external_effect_job_id=int(row["external_effect_job_id"]),
                    response_status=row.get("response_status"),
                    actor_hash=actor_hash,
                    reason_hash=_reason_hash(normalized_reason),
                )
                if updated:
                    repaired_external_push_delivery_projection += 1
            conn.commit()
        after = self.diagnose()
        repaired_total = (
            repaired_payment_outbox
            + repaired_refund_outbox
            + repaired_external_push_delivery_projection
        )
        return {
            "ok": bool(before.get("ok")) and bool(after.get("ok")),
            "mode": "repair_external_push_projection_only" if projection_only else "repair_continuation_only",
            "before": before,
            "after": after,
            "repaired": {
                "payment_succeeded_outbox_count": repaired_payment_outbox,
                "refund_succeeded_outbox_count": repaired_refund_outbox,
                "external_push_delivery_projection_count": repaired_external_push_delivery_projection,
            },
            "repair_actor_hash": actor_hash,
            "repair_reason_hash": _reason_hash(normalized_reason),
            "repair_reason_recorded": True,
            "database_mutation_performed": repaired_total > 0,
            "consumer_executed": False,
            "real_external_call_executed": False,
            "pii_in_output": False,
        }

    @staticmethod
    def _result_error(error: str) -> dict[str, Any]:
        return {
            "ok": False,
            "error": error,
            "database_mutation_performed": False,
            "consumer_executed": False,
            "real_external_call_executed": False,
            "pii_in_output": False,
        }


__all__ = ["CommerceFulfillmentReconciliationService"]
