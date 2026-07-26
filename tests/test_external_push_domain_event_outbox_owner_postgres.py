from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

from aicrm_next.external_push.domain_event_outbox_port import build_domain_event_outbox_port


def test_domain_event_outbox_owner_is_idempotent_in_caller_transaction(next_pg_schema) -> None:
    port = build_domain_event_outbox_port()
    payload = {"order_id": "910043", "buyer_id": "wm-owner-port"}

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
        first = port.enqueue_dbapi(
            connection,
            tenant_id="aicrm",
            event_type="transaction.paid",
            aggregate_type="wechat_pay_order",
            aggregate_id="910043",
            payload=payload,
        )
        second = port.enqueue_dbapi(
            connection,
            tenant_id="aicrm",
            event_type="transaction.paid",
            aggregate_type="wechat_pay_order",
            aggregate_id="910043",
            payload=payload,
        )
        count = connection.execute(
            """
            SELECT count(*) AS count
            FROM domain_event_outbox
            WHERE tenant_id = %s
              AND event_type = %s
              AND aggregate_type = %s
              AND aggregate_id = %s
            """,
            ("aicrm", "transaction.paid", "wechat_pay_order", "910043"),
        ).fetchone()["count"]
        connection.rollback()

    assert first is not None
    assert first["payload"] == payload
    assert second is None
    assert count == 1
