from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from aicrm_next.shared.product_code_aliases import product_code_filter_values
from aicrm_next.shared.config import Settings, get_settings
from aicrm_next.shared.db_session import get_session_factory


DEFAULT_TENANT_ID = "aicrm"
EVENT_TRANSACTION_PAID = "transaction.paid"


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str, separators=(",", ":"))


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


def _public_config(row: dict[str, Any] | None, *, include_secret: bool = False) -> dict[str, Any] | None:
    if not row:
        return None
    payload = dict(row)
    payload["id"] = int(payload.get("id") or 0)
    payload["enabled"] = bool(payload.get("enabled"))
    payload["custom_params"] = _json_obj(payload.get("custom_params"))
    payload["has_secret"] = bool(_normalized_text(payload.get("secret")))
    if not include_secret:
        payload.pop("secret", None)
    return payload


def _public_delivery(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    payload = dict(row)
    payload["id"] = int(payload.get("id") or 0)
    payload["config_id"] = int(payload.get("config_id") or 0)
    payload["order_id"] = int(payload.get("order_id") or 0)
    payload["product_id"] = int(payload.get("product_id") or 0)
    payload["attempt_count"] = int(payload.get("attempt_count") or 0)
    payload["request_headers"] = _json_obj(payload.get("request_headers"))
    payload["request_body"] = _json_obj(payload.get("request_body"))
    payload["response_body"] = _normalized_text(payload.get("response_body"))
    return payload


def _public_outbox(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    payload = dict(row)
    payload["id"] = int(payload.get("id") or 0)
    payload["retry_count"] = int(payload.get("retry_count") or 0)
    payload["payload"] = _json_obj(payload.get("payload"))
    return payload


def generate_delivery_id() -> str:
    return "deliv_" + secrets.token_urlsafe(18).replace("-", "").replace("_", "")[:24]


class SQLAlchemyExternalPushRepository:
    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def _one(self, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.execute(text(statement), params or {}).mappings().fetchone()
            return dict(row) if row else None

    def _all(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.execute(text(statement), params or {}).mappings().fetchall()
            return [dict(row) for row in rows]

    def _write_one(self, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.execute(text(statement), params or {}).mappings().fetchone()
            session.commit()
            return dict(row) if row else None

    def get_product_config(self, product_id: int, *, tenant_id: str = DEFAULT_TENANT_ID) -> dict[str, Any] | None:
        row = self._one(
            """
            SELECT *
            FROM external_push_config
            WHERE tenant_id = :tenant_id
              AND target_type = 'product'
              AND target_id = :target_id
              AND event_type = :event_type
            LIMIT 1
            """,
            {
                "tenant_id": _normalized_text(tenant_id) or DEFAULT_TENANT_ID,
                "target_id": str(int(product_id)),
                "event_type": EVENT_TRANSACTION_PAID,
            },
        )
        return _public_config(row)

    def get_config_with_secret(self, config_id: int) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM external_push_config WHERE id = :config_id LIMIT 1",
            {"config_id": int(config_id)},
        )
        return _public_config(row, include_secret=True)

    def get_order_by_id(self, order_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM wechat_pay_orders WHERE id = :order_id LIMIT 1", {"order_id": int(order_id)})
        return dict(row) if row else None

    def get_product_by_id(self, product_id: int) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM wechat_pay_products WHERE id = :product_id LIMIT 1",
            {"product_id": int(product_id)},
        )
        return dict(row) if row else None

    def get_product_for_order(self, order: dict[str, Any]) -> dict[str, Any]:
        for product_code in product_code_filter_values(_normalized_text(order.get("product_code"))):
            row = self._one(
                """
                SELECT *
                FROM wechat_pay_products
                WHERE product_code = :product_code
                ORDER BY updated_at DESC NULLS LAST, id DESC
                LIMIT 1
                """,
                {"product_code": product_code},
            )
            if row:
                return dict(row)
        return {
            "id": 0,
            "product_code": _normalized_text(order.get("product_code")),
            "name": _normalized_text(order.get("product_name") or order.get("product_code")),
            "amount_total": int(order.get("amount_total") or 0),
            "currency": _normalized_text(order.get("currency")) or "CNY",
        }

    def resolve_identity_mobile_by_unionid(self, unionid: str) -> str:
        row = self._one(
            """
            SELECT mobile
            FROM crm_user_identity
            WHERE unionid = :unionid
              AND COALESCE(mobile, '') <> ''
            LIMIT 1
            """,
            {"unionid": _normalized_text(unionid)},
        )
        return _normalized_text((row or {}).get("mobile"))

    def insert_outbox_event(
        self,
        *,
        tenant_id: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        row = self._write_one(
            """
            INSERT INTO domain_event_outbox (
                tenant_id, event_type, aggregate_type, aggregate_id, payload, status,
                retry_count, next_retry_at, created_at, updated_at
            )
            VALUES (
                :tenant_id, :event_type, :aggregate_type, :aggregate_id, CAST(:payload AS jsonb),
                'pending', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (tenant_id, event_type, aggregate_type, aggregate_id) DO NOTHING
            RETURNING *
            """,
            {
                "tenant_id": _normalized_text(tenant_id) or DEFAULT_TENANT_ID,
                "event_type": _normalized_text(event_type),
                "aggregate_type": _normalized_text(aggregate_type),
                "aggregate_id": _normalized_text(aggregate_id),
                "payload": _json_dumps(payload or {}),
            },
        )
        return _public_outbox(row)

    def list_due_outbox_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._all(
            """
            SELECT *
            FROM domain_event_outbox
            WHERE status IN ('pending', 'failed')
              AND event_type = :event_type
              AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
            ORDER BY created_at ASC, id ASC
            LIMIT :limit
            """,
            {"event_type": EVENT_TRANSACTION_PAID, "limit": max(1, min(int(limit), 200))},
        )
        return [_public_outbox(row) or {} for row in rows]

    def mark_outbox_status(
        self,
        outbox_id: int,
        *,
        status: str,
        retry_count: int | None = None,
        next_retry_at: str | None = None,
    ) -> dict[str, Any] | None:
        row = self._write_one(
            """
            UPDATE domain_event_outbox
            SET status = :status,
                retry_count = COALESCE(:retry_count, retry_count),
                next_retry_at = CAST(NULLIF(:next_retry_at, '') AS timestamptz),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :outbox_id
            RETURNING *
            """,
            {
                "status": _normalized_text(status),
                "retry_count": retry_count,
                "next_retry_at": _normalized_text(next_retry_at),
                "outbox_id": int(outbox_id),
            },
        )
        return _public_outbox(row)

    def create_delivery_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._write_one(
            """
            INSERT INTO external_push_delivery (
                tenant_id, config_id, event_type, delivery_id, target_type, target_id,
                order_id, product_id, status, attempt_count, request_url, request_headers,
                request_body, response_status, response_body, error_message, next_retry_at,
                created_at, updated_at
            )
            VALUES (
                :tenant_id, :config_id, :event_type, :delivery_id, :target_type, :target_id,
                :order_id, :product_id, 'pending', 0, :request_url, CAST('{}' AS jsonb), CAST('{}' AS jsonb),
                NULL, '', '', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (config_id, order_id, event_type) WHERE order_id > 0
            DO UPDATE SET updated_at = external_push_delivery.updated_at
            RETURNING *
            """,
            {
                "tenant_id": _normalized_text(payload.get("tenant_id")) or DEFAULT_TENANT_ID,
                "config_id": int(payload.get("config_id") or 0),
                "event_type": _normalized_text(payload.get("event_type")),
                "delivery_id": _normalized_text(payload.get("delivery_id")) or generate_delivery_id(),
                "target_type": _normalized_text(payload.get("target_type")),
                "target_id": _normalized_text(payload.get("target_id")),
                "order_id": int(payload.get("order_id") or 0),
                "product_id": int(payload.get("product_id") or 0),
                "request_url": _normalized_text(payload.get("request_url")),
            },
        )
        return _public_delivery(row) or {}

    def create_test_delivery(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._write_one(
            """
            INSERT INTO external_push_delivery (
                tenant_id, config_id, event_type, delivery_id, target_type, target_id,
                order_id, product_id, status, attempt_count, request_url, request_headers,
                request_body, response_status, response_body, error_message, next_retry_at,
                created_at, updated_at
            )
            VALUES (
                :tenant_id, :config_id, 'external_push.test', :delivery_id, 'product',
                :target_id, 0, :product_id, 'pending', 0, :request_url, CAST('{}' AS jsonb),
                CAST('{}' AS jsonb), NULL, '', '', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            RETURNING *
            """,
            {
                "tenant_id": _normalized_text(payload.get("tenant_id")) or DEFAULT_TENANT_ID,
                "config_id": int(payload.get("config_id") or 0),
                "delivery_id": _normalized_text(payload.get("delivery_id")) or generate_delivery_id(),
                "target_id": _normalized_text(payload.get("target_id")),
                "product_id": int(payload.get("product_id") or 0),
                "request_url": _normalized_text(payload.get("request_url")),
            },
        )
        return _public_delivery(row) or {}

    def update_delivery_result(
        self,
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
        row = self._write_one(
            """
            UPDATE external_push_delivery
            SET status = :status,
                attempt_count = :attempt_count,
                request_url = :request_url,
                request_headers = CAST(:request_headers AS jsonb),
                request_body = CAST(:request_body AS jsonb),
                response_status = COALESCE(:response_status, response_status),
                response_body = :response_body,
                error_message = :error_message,
                next_retry_at = CAST(NULLIF(:next_retry_at, '') AS timestamptz),
                updated_at = CURRENT_TIMESTAMP
            WHERE delivery_id = :delivery_id
            RETURNING *
            """,
            {
                "status": _normalized_text(status),
                "attempt_count": int(attempt_count),
                "request_url": _normalized_text(request_url),
                "request_headers": _json_dumps(request_headers or {}),
                "request_body": _json_dumps(request_body or {}),
                "response_status": response_status,
                "response_body": _normalized_text(response_body),
                "error_message": _normalized_text(error_message),
                "next_retry_at": _normalized_text(next_retry_at),
                "delivery_id": _normalized_text(delivery_id),
            },
        )
        return _public_delivery(row) or {}

    def mark_delivery_succeeded_from_external_effect(
        self,
        delivery_id: str,
        *,
        external_effect_job_id: int,
        response_status: int | None,
    ) -> dict[str, Any]:
        """Project the canonical External Effect result into the legacy delivery row."""

        row = self._write_one(
            """
            UPDATE external_push_delivery
            SET status = 'success',
                attempt_count = GREATEST(attempt_count, 1),
                response_status = :response_status,
                response_body = :response_body,
                error_message = '',
                next_retry_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE delivery_id = :delivery_id
            RETURNING *
            """,
            {
                "response_status": response_status,
                "response_body": _json_dumps(
                    {
                        "external_effect_job_id": int(external_effect_job_id),
                        "external_effect_status": "succeeded",
                    }
                ),
                "delivery_id": _normalized_text(delivery_id),
            },
        )
        return _public_delivery(row) or {}

    def mark_delivery_settled_from_external_effect(
        self,
        delivery_id: str,
        *,
        external_effect_job_id: int,
        external_effect_status: str,
        error_code: str = "",
    ) -> dict[str, Any]:
        """Close a delivery when its canonical External Effect cannot succeed."""

        terminal_status = _normalized_text(external_effect_status)
        delivery_status = {
            "failed_terminal": "gave_up",
            "blocked": "gave_up",
            "unknown_after_dispatch": "gave_up",
            "cancelled": "skipped",
            "simulated": "skipped",
        }.get(terminal_status)
        if not delivery_status:
            return {}
        row = self._write_one(
            """
            UPDATE external_push_delivery
            SET status = :status,
                attempt_count = GREATEST(attempt_count, 1),
                response_body = :response_body,
                error_message = :error_message,
                next_retry_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE delivery_id = :delivery_id
              AND status <> 'success'
            RETURNING *
            """,
            {
                "status": delivery_status,
                "response_body": _json_dumps(
                    {
                        "external_effect_job_id": int(external_effect_job_id),
                        "external_effect_status": terminal_status,
                        "reconciliation_required": terminal_status == "unknown_after_dispatch",
                    }
                ),
                "error_message": _normalized_text(error_code) or f"external_effect_{terminal_status}",
                "delivery_id": _normalized_text(delivery_id),
            },
        )
        return _public_delivery(row) or {}

    def get_delivery_by_delivery_id(self, delivery_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM external_push_delivery WHERE delivery_id = :delivery_id LIMIT 1",
            {"delivery_id": _normalized_text(delivery_id)},
        )
        return _public_delivery(row)

    def list_deliveries_for_order(self, order_id: int, *, tenant_id: str = DEFAULT_TENANT_ID) -> list[dict[str, Any]]:
        rows = self._all(
            """
            SELECT *
            FROM external_push_delivery
            WHERE tenant_id = :tenant_id
              AND order_id = :order_id
            ORDER BY created_at DESC, id DESC
            """,
            {"tenant_id": _normalized_text(tenant_id) or DEFAULT_TENANT_ID, "order_id": int(order_id)},
        )
        return [_public_delivery(row) or {} for row in rows]

    def list_due_deliveries(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._all(
            """
            SELECT *
            FROM external_push_delivery
            WHERE status IN ('failed', 'retrying')
              AND next_retry_at IS NOT NULL
              AND next_retry_at <= CURRENT_TIMESTAMP
            ORDER BY next_retry_at ASC, id ASC
            LIMIT :limit
            """,
            {"limit": max(1, min(int(limit), 200))},
        )
        return [_public_delivery(row) or {} for row in rows]


def build_external_push_repository(
    *,
    settings: Settings | None = None,
    session_factory: Callable[[], Session] | None = None,
) -> SQLAlchemyExternalPushRepository:
    factory = session_factory or get_session_factory(settings=settings or get_settings())
    return SQLAlchemyExternalPushRepository(factory)
