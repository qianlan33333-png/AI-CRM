from __future__ import annotations

import subprocess
import sys

from aicrm_next.extensions.commerce.commerce import fulfillment_reconciliation
from aicrm_next.extensions.commerce.commerce.fulfillment_reconciliation import CommerceFulfillmentReconciliationService


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self):
        self.row_factory = None
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, params=()):
        del params
        self.statements.append(str(statement))
        return _Result({"anomaly_count": 1, "sample_ids": [7]})


def test_count_only_reconciliation_has_seven_pii_free_read_only_counts(monkeypatch) -> None:
    connection = _Connection()
    monkeypatch.setattr(fulfillment_reconciliation, "connect_raw_postgres", lambda url: connection)

    result = CommerceFulfillmentReconciliationService(database_url="postgresql://test/test").diagnose()

    assert result["ok"] is True
    assert result["mode"] == "count_only"
    assert set(result["counts"]) == {
        "paid_without_payment_outbox",
        "paid_service_product_without_entitlement_or_open_consumer",
        "successful_full_refund_with_active_entitlement",
        "refund_request_without_effect",
        "duplicate_order_paid_effect",
        "stale_succeeded_external_push_delivery_projection",
        "legacy_domain_outbox_pending",
    }
    assert set(result["counts"].values()) == {1}
    assert result["database_mutation_performed"] is False
    assert result["consumer_executed"] is False
    assert result["real_external_call_executed"] is False
    assert result["pii_in_output"] is False
    statements = "\n".join(connection.statements).upper()
    assert "INSERT INTO" not in statements
    assert "UPDATE " not in statements
    assert "DELETE FROM" not in statements


def test_reconciliation_only_flags_post_rollout_actionable_gaps() -> None:
    payment_query = fulfillment_reconciliation._ANOMALY_QUERIES["paid_without_payment_outbox"]
    refund_query = fulfillment_reconciliation._ANOMALY_QUERIES["refund_request_without_effect"]
    cutover = fulfillment_reconciliation._FULFILLMENT_RECONCILIATION_CUTOVER_AT_SQL

    assert cutover in payment_query
    assert "SELECT MIN(rollout.created_at)" not in payment_query
    assert "COALESCE(o.paid_at, o.created_at)" in payment_query
    assert "o.updated_at" not in payment_query
    assert "LOWER(COALESCE(r.status, '')) IN ('requested', 'queued')" in refund_query
    assert "COALESCE(r.refund_id, '') = ''" in refund_query
    assert cutover in refund_query
    assert "SELECT MIN(rollout.created_at)" not in refund_query
    assert "job.effect_type = 'payment.wechat.refund.request'" in refund_query


def test_repair_requires_auditable_actor_and_reason_without_connecting(monkeypatch) -> None:
    monkeypatch.setattr(
        fulfillment_reconciliation,
        "connect_raw_postgres",
        lambda url: (_ for _ in ()).throw(AssertionError("database must not be opened")),
    )

    result = CommerceFulfillmentReconciliationService(database_url="postgresql://test/test").repair(
        actor="",
        reason="",
    )

    assert result["ok"] is False
    assert result["error"] == "actor_and_reason_required"
    assert result["database_mutation_performed"] is False
    assert result["real_external_call_executed"] is False


def test_reconciliation_cli_help_is_available() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/ops/reconcile_commerce_fulfillment.py", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "--repair" in completed.stdout
    assert "--projection-only" in completed.stdout
    assert "--actor" in completed.stdout
    assert "--reason" in completed.stdout
