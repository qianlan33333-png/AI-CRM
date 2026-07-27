from __future__ import annotations

from datetime import datetime, timezone

from aicrm_next.platform.platform_foundation.command_bus import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import WEBHOOK_ORDER_PAID_PUSH, ExternalEffectService


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, *, conflict: bool = False) -> None:
        self.conflict = conflict
        self.calls: list[tuple[str, tuple]] = []
        self.commit_calls = 0
        self.rollback_calls = 0

    def execute(self, sql: str, params: tuple):
        self.calls.append((sql, params))
        if "INSERT INTO external_effect_job" in sql and self.conflict:
            return _Result(None)
        return _Result(
            {
                "id": 91,
                "tenant_id": "aicrm",
                "effect_type": WEBHOOK_ORDER_PAID_PUSH,
                "adapter_name": "outbound_webhook",
                "operation": "post",
                "target_type": "commerce_order",
                "target_id": "WXPAY91",
                "business_type": "commerce_order",
                "business_id": "WXPAY91",
                "source_module": "pytest",
                "source_route": "/pytest",
                "source_event_id": "evt-91",
                "source_command_id": "cmd-91",
                "trace_id": "trace-91",
                "request_id": "request-91",
                "correlation_id": "",
                "idempotency_key": "order-paid:WXPAY91",
                "actor_id": "pytest",
                "actor_type": "system",
                "risk_level": "medium",
                "requires_approval": False,
                "execution_mode": "execute",
                "payload_json": {},
                "payload_summary_json": {},
                "status": "queued",
                "priority": 100,
                "scheduled_at": datetime.now(timezone.utc),
                "attempt_count": 0,
                "max_attempts": 5,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


def _plan(connection: _Connection) -> dict:
    return ExternalEffectService().plan_effect(
        effect_type=WEBHOOK_ORDER_PAID_PUSH,
        adapter_name="outbound_webhook",
        operation="post",
        target_type="commerce_order",
        target_id="WXPAY91",
        business_type="commerce_order",
        business_id="WXPAY91",
        payload={"webhook_url": "https://hooks.example.test/order"},
        context=CommandContext(
            actor_id="pytest",
            actor_type="system",
            request_id="request-91",
            trace_id="trace-91",
            source_route="/pytest",
        ),
        source_module="pytest",
        source_event_id="evt-91",
        source_command_id="cmd-91",
        idempotency_key="order-paid:WXPAY91",
        connection=connection,
    )


def test_transactional_external_effect_create_never_owns_commit() -> None:
    connection = _Connection()

    job = _plan(connection)

    assert job["id"] == 91
    assert "INSERT INTO external_effect_job" in connection.calls[0][0]
    assert connection.commit_calls == 0
    assert connection.rollback_calls == 0


def test_transactional_external_effect_create_reuses_conflicting_job_in_same_transaction() -> None:
    connection = _Connection(conflict=True)

    job = _plan(connection)

    assert job["id"] == 91
    assert len(connection.calls) == 2
    assert "SELECT * FROM external_effect_job" in connection.calls[1][0]
    assert connection.commit_calls == 0
