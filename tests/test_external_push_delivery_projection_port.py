from __future__ import annotations

import json

from aicrm_next.platform.external_push.delivery_projection_port import build_external_push_delivery_projection_port


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Executor:
    def __init__(self, row):
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, statement, params):
        self.calls.append((" ".join(statement.split()), tuple(params)))
        return _Cursor(self.row)


def test_order_delivery_projection_preserves_idempotency_and_caller_transaction() -> None:
    executor = _Executor({"id": 71, "delivery_id": "deliv-owner", "status": "pending"})

    row = build_external_push_delivery_projection_port().create_order_delivery_once_dbapi(
        executor,
        tenant_id="aicrm",
        config_id=7,
        event_type="transaction.paid",
        target_id="17",
        order_id=27,
        product_id=17,
        request_url="https://example.test/hook",
    )

    statement, params = executor.calls[0]
    assert row["id"] == 71
    assert statement.startswith("INSERT INTO external_push_delivery")
    assert "ON CONFLICT (config_id, order_id, event_type) WHERE order_id > 0" in statement
    assert params[:3] == ("aicrm", 7, "transaction.paid")
    assert params[3].startswith("deliv_")
    assert params[4:] == ("17", 27, 17, "https://example.test/hook")
    assert not hasattr(executor, "commit")


def test_delivery_result_projection_serializes_redacted_request_payloads() -> None:
    executor = _Executor({"id": 72, "delivery_id": "deliv-owner", "status": "retrying"})

    row = build_external_push_delivery_projection_port().update_delivery_result_dbapi(
        executor,
        delivery_id="deliv-owner",
        status="retrying",
        attempt_count=1,
        request_url="https://example.test/hook",
        request_headers={"Authorization": "[redacted]"},
        request_body={"phone_number": "[pii]"},
        response_status=None,
        response_body='{"external_effect_job_id":91}',
        error_message="external_effect_job_queued",
        next_retry_at="",
    )

    _, params = executor.calls[0]
    assert row["status"] == "retrying"
    assert json.loads(params[3]) == {"Authorization": "[redacted]"}
    assert json.loads(params[4]) == {"phone_number": "[pii]"}
    assert params[-1] == "deliv-owner"


def test_reconciliation_projection_records_audited_external_effect_receipt() -> None:
    executor = _Executor({"id": 73})

    changed = build_external_push_delivery_projection_port().reconcile_succeeded_dbapi(
        executor,
        delivery_id="deliv-owner",
        external_effect_job_id=91,
        response_status=204,
        actor_hash="actor-hash",
        reason_hash="reason-hash",
    )

    _, params = executor.calls[0]
    receipt = json.loads(params[1])
    assert changed is True
    assert params[0] == 204
    assert params[2] == "deliv-owner"
    assert receipt == {
        "external_effect_job_id": 91,
        "external_effect_status": "succeeded",
        "reconciled": True,
        "repair_actor_hash": "actor-hash",
        "repair_reason_hash": "reason-hash",
    }
