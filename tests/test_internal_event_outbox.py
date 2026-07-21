from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest
from psycopg.rows import dict_row

from aicrm_next.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform_foundation.internal_events.consumer_registry import InternalEventConsumerRegistry
from aicrm_next.platform_foundation.internal_events.models import (
    InternalEventConsumerResult,
    InternalEventConsumerSpec,
    InternalEventCreateRequest,
)
from aicrm_next.platform_foundation.internal_events.outbox import (
    InternalEventOutboxRelay,
    enqueue_transactional_internal_event_outbox,
)
from aicrm_next.platform_foundation.internal_events.payment import (
    PAYMENT_SUCCEEDED_CORE_CONSUMERS,
    PAYMENT_SUCCEEDED_EVENT_TYPE,
)
from aicrm_next.platform_foundation.internal_events.reconciliation import InternalEventOutboxReconciliationService
from aicrm_next.platform_foundation.internal_events.reconciliation import outbox as outbox_reconciliation
from aicrm_next.platform_foundation.internal_events.repository import (
    InMemoryInternalEventRepository,
    SQLAlchemyInternalEventRepository,
)
from aicrm_next.platform_foundation.internal_events.service import InternalEventService
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker
from aicrm_next.shared.db_session import get_session_factory
from scripts.ops import reconcile_internal_event_outbox


EVENT_TYPE = "test.transactional_event"
ROOT = Path(__file__).resolve().parents[1]


def _request(key: str = "transactional-event:1") -> InternalEventCreateRequest:
    return InternalEventCreateRequest(
        event_type=EVENT_TYPE,
        aggregate_type="test_record",
        aggregate_id="1",
        idempotency_key=key,
        source_module="tests.test_internal_event_outbox",
        source_command_id="command-1",
        context=CommandContext(
            actor_id="test-runner",
            actor_type="test",
            trace_id="trace-1",
            request_id="request-1",
            source_route="/tests/internal-event-outbox",
        ),
        payload={"record_id": 1},
        payload_summary={"record_id": 1},
    )


def _registry(*names: str) -> InternalEventConsumerRegistry:
    registry = InternalEventConsumerRegistry()
    for name in names:
        registry.register(
            EVENT_TYPE,
            name,
            lambda event, run: InternalEventConsumerResult(status="succeeded", result_summary={"consumer": run.consumer_name}),
        )
    registry.seal_fanout_contract()
    return registry


def _database_url() -> str:
    return str(os.getenv("AICRM_TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()


def _enable_internal_event_worker(monkeypatch: pytest.MonkeyPatch, *consumer_names: str) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE", "10")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", EVENT_TYPE)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", ",".join(consumer_names))
    monkeypatch.delenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS", raising=False)


def test_reconciliation_script_supports_direct_file_entrypoint() -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "scripts/ops/reconcile_internal_event_outbox.py", "--help"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Diagnose or repair internal event outbox gaps" in result.stdout


def test_reconciliation_script_uses_the_complete_production_consumer_registry(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeService:
        def __init__(self, *, consumer_registry):
            captured["consumer_registry"] = consumer_registry

        def diagnose(self):
            return {"ok": True}

    monkeypatch.setattr(reconcile_internal_event_outbox, "InternalEventOutboxReconciliationService", FakeService)

    assert reconcile_internal_event_outbox.run() == {"ok": True}
    registry = captured["consumer_registry"]
    consumer_names = {
        item.consumer_name
        for item in registry.list_for_event_type(PAYMENT_SUCCEEDED_EVENT_TYPE)
    }

    assert set(PAYMENT_SUCCEEDED_CORE_CONSUMERS).issubset(consumer_names)
    assert "service_period_entitlement_consumer" in consumer_names
    assert "ai_audience_source_poke_consumer" in consumer_names


def test_reconciliation_scopes_actionable_payment_gaps_to_r08_cutover(monkeypatch) -> None:
    class ScalarResult:
        def scalar_one(self):
            return 0

    class Session:
        def __init__(self):
            self.queries: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, params=None):
            self.queries.append(str(statement))
            return ScalarResult()

    class RowsResult:
        def fetchall(self):
            return []

    class Connection:
        def __init__(self):
            self.queries: list[str] = []
            self.row_factory = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=()):
            self.queries.append(str(query))
            return RowsResult()

        def commit(self):
            return None

    session = Session()
    connection = Connection()
    registry = InternalEventConsumerRegistry()
    registry.register(PAYMENT_SUCCEEDED_EVENT_TYPE, "projection-a", lambda event, run: InternalEventConsumerResult(status="succeeded"))
    service = InternalEventOutboxReconciliationService(
        InMemoryInternalEventRepository(),
        registry,
    )
    service._session_factory = lambda: session
    service._database_url = "postgresql://test"
    monkeypatch.setattr(outbox_reconciliation, "connect_raw_postgres", lambda database_url: connection)

    result = service.diagnose()
    repaired_count = service._repair_paid_without_outbox(limit=10)

    assert outbox_reconciliation._INTERNAL_EVENT_RECONCILIATION_CUTOVER_AT == "2026-07-13T09:46:09Z"
    assert result["actionable_cutover_at"] == "2026-07-13T09:46:09Z"
    assert any("COALESCE(p.paid_at, p.created_at) >= TIMESTAMPTZ '2026-07-13 09:46:09+00'" in query for query in session.queries)
    assert any("e.created_at >= TIMESTAMPTZ '2026-07-13 09:46:09+00'" in query for query in session.queries)
    assert repaired_count == 0
    assert "COALESCE(p.paid_at, p.created_at) >= TIMESTAMPTZ '2026-07-13 09:46:09+00'" in connection.queries[0]


def test_outbox_relay_is_idempotent_and_creates_all_registered_runs() -> None:
    repo = InMemoryInternalEventRepository()
    registry = _registry("projection-a", "projection-b")

    first = repo.enqueue_outbox(_request())
    duplicate = repo.enqueue_outbox(_request())
    result = InternalEventOutboxRelay(repo, registry).relay_due(limit=10)
    second_run = InternalEventOutboxRelay(repo, registry).relay_due(limit=10)
    events, event_total = repo.list_events({"event_type": EVENT_TYPE})
    runs, run_total = repo.list_consumer_runs({"event_id": events[0].event_id})

    assert first.outbox_id == duplicate.outbox_id
    assert result["ok"] is True
    assert result["counts"]["relayed_count"] == 1
    assert second_run["counts"]["candidate_count"] == 0
    assert event_total == 1
    assert run_total == 2
    assert {run.consumer_name for run in runs} == {"projection-a", "projection-b"}


def test_fanout_contract_is_deterministic_sealed_and_immutable() -> None:
    registry = InternalEventConsumerRegistry()
    registry.register(EVENT_TYPE, "projection-b", lambda event, run: InternalEventConsumerResult(status="succeeded"), max_attempts=7)
    registry.register(EVENT_TYPE, "projection-a", lambda event, run: InternalEventConsumerResult(status="succeeded"))

    assert registry.is_fanout_authoritative is False
    with pytest.raises(RuntimeError, match="fanout contract is not sealed"):
        registry.fanout_manifest_for(EVENT_TYPE)

    registry.seal_fanout_contract()
    manifest = registry.fanout_manifest_for(EVENT_TYPE)

    assert registry.is_fanout_authoritative is True
    assert manifest["version"] == "internal-event-fanout/v1"
    assert len(manifest["hash"]) == 64
    assert manifest["expected_consumer_count"] == 2
    assert [item["consumer_name"] for item in manifest["consumers"]] == ["projection-a", "projection-b"]
    assert manifest["consumers"][1]["max_attempts"] == 7
    with pytest.raises(RuntimeError, match="fanout contract is sealed"):
        registry.register(EVENT_TYPE, "projection-c", lambda event, run: InternalEventConsumerResult(status="succeeded"))


def test_outbox_relay_rejects_unsealed_partial_registry_before_acquire() -> None:
    repo = InMemoryInternalEventRepository()
    repo.enqueue_outbox(_request("transactional-event:unsealed"))
    partial = InternalEventConsumerRegistry()
    partial.register(EVENT_TYPE, "projection-a", lambda event, run: InternalEventConsumerResult(status="succeeded"))

    with pytest.raises(RuntimeError, match="authoritative fanout registry required"):
        InternalEventOutboxRelay(repo, partial)

    assert len(repo.list_due_outbox(limit=10)) == 1


def test_outbox_relay_persists_authoritative_fanout_manifest() -> None:
    repo = InMemoryInternalEventRepository()
    registry = _registry("projection-b", "projection-a")
    repo.enqueue_outbox(_request("transactional-event:manifest"))

    result = InternalEventOutboxRelay(repo, registry).relay_due(limit=10)
    events, total = repo.list_events({"event_type": EVENT_TYPE})

    assert result["ok"] is True
    assert total == 1
    event = events[0]
    assert event.fanout_manifest_version == "internal-event-fanout/v1"
    assert len(event.fanout_manifest_hash) == 64
    assert event.expected_consumer_count == 2
    assert [item["consumer_name"] for item in event.fanout_manifest_json] == ["projection-a", "projection-b"]


def test_manifest_mismatch_is_retryable_and_does_not_overwrite_existing_contract() -> None:
    repo = InMemoryInternalEventRepository()
    request = _request("transactional-event:manifest-mismatch")
    event = repo.create_event(request)
    event_row = next(row for row in repo._events if row["event_id"] == event.event_id)
    event_row.update(
        {
            "fanout_manifest_version": "internal-event-fanout/v1",
            "fanout_manifest_hash": "existing-contract-hash",
            "fanout_manifest_json": [
                {"consumer_name": "projection-old", "consumer_type": "projection", "max_attempts": 5}
            ],
            "expected_consumer_count": 1,
        }
    )
    repo.enqueue_outbox(request)

    result = InternalEventOutboxRelay(repo, _registry("projection-new")).relay_due(limit=10)

    assert result["ok"] is False
    assert result["counts"]["failed_retryable_count"] == 1
    assert repo._outbox[0]["status"] == "failed_retryable"
    assert repo._events[0]["fanout_manifest_hash"] == "existing-contract-hash"
    assert repo._runs == []


def test_incomplete_fanout_rolls_back_event_and_runs_before_outbox_failure() -> None:
    class IncompleteRepository(InMemoryInternalEventRepository):
        def create_consumer_run(self, *, event, consumer_name, consumer_type="projection", max_attempts=5):
            if consumer_name == "projection-b":
                consumer_name = "projection-a"
            return super().create_consumer_run(
                event=event,
                consumer_name=consumer_name,
                consumer_type=consumer_type,
                max_attempts=max_attempts,
            )

    repo = IncompleteRepository()
    repo.enqueue_outbox(_request("transactional-event:incomplete-fanout"))

    result = InternalEventOutboxRelay(repo, _registry("projection-a", "projection-b")).relay_due(limit=10)

    assert result["ok"] is False
    assert result["counts"]["failed_retryable_count"] == 1
    assert repo._outbox[0]["status"] == "failed_retryable"
    assert repo._outbox[0]["internal_event_id"] == ""
    assert repo._events == []
    assert repo._runs == []


def test_manifest_reconciliation_repairs_only_stored_consumers_without_execution() -> None:
    repo = InMemoryInternalEventRepository()
    handler_calls: list[str] = []
    relay_registry = InternalEventConsumerRegistry()
    for name in ("projection-a", "projection-b"):
        relay_registry.register(
            PAYMENT_SUCCEEDED_EVENT_TYPE,
            name,
            lambda event, run: handler_calls.append(run.consumer_name),
        )
    relay_registry.seal_fanout_contract()
    request = InternalEventCreateRequest(
        event_type=PAYMENT_SUCCEEDED_EVENT_TYPE,
        aggregate_type="wechat_pay_order",
        aggregate_id="manifest-repair",
        idempotency_key="payment.succeeded:MANIFEST_REPAIR",
        source_module="tests.test_internal_event_outbox",
        payload={"order": {"id": "manifest-repair", "status": "paid"}},
    )
    repo.enqueue_outbox(request)
    assert InternalEventOutboxRelay(repo, relay_registry).relay_due(limit=10)["ok"] is True
    event = repo.list_events({"event_type": PAYMENT_SUCCEEDED_EVENT_TYPE})[0][0]
    repo._runs = [row for row in repo._runs if row["consumer_name"] != "projection-b"]

    current_registry = InternalEventConsumerRegistry()
    current_registry.register(
        PAYMENT_SUCCEEDED_EVENT_TYPE,
        "projection-a",
        lambda event, run: handler_calls.append(run.consumer_name),
    )
    current_registry.register(
        PAYMENT_SUCCEEDED_EVENT_TYPE,
        "projection-c",
        lambda event, run: handler_calls.append(run.consumer_name),
    )
    current_registry.seal_fanout_contract()
    service = InternalEventOutboxReconciliationService(repo, current_registry)

    first_count, first_invalid = service._repair_missing_consumer_runs(limit=10)
    second_count, second_invalid = service._repair_missing_consumer_runs(limit=10)
    runs, total = repo.list_consumer_runs({"event_id": event.event_id})

    assert (first_count, first_invalid) == (1, 0)
    assert (second_count, second_invalid) == (0, 0)
    assert total == 2
    assert {run.consumer_name for run in runs} == {"projection-a", "projection-b"}
    assert all(run.attempt_count == 0 for run in runs)
    assert handler_calls == []


def test_manifest_reconciliation_never_falls_back_when_stored_hash_is_invalid() -> None:
    repo = InMemoryInternalEventRepository()
    relay_registry = InternalEventConsumerRegistry()
    relay_registry.register(
        PAYMENT_SUCCEEDED_EVENT_TYPE,
        "projection-a",
        lambda event, run: InternalEventConsumerResult(status="succeeded"),
    )
    relay_registry.seal_fanout_contract()
    request = InternalEventCreateRequest(
        event_type=PAYMENT_SUCCEEDED_EVENT_TYPE,
        aggregate_type="wechat_pay_order",
        aggregate_id="invalid-manifest-repair",
        idempotency_key="payment.succeeded:INVALID_MANIFEST_REPAIR",
        source_module="tests.test_internal_event_outbox",
    )
    repo.enqueue_outbox(request)
    assert InternalEventOutboxRelay(repo, relay_registry).relay_due(limit=10)["ok"] is True
    event = repo.list_events({"event_type": PAYMENT_SUCCEEDED_EVENT_TYPE})[0][0]
    repo._events[0]["fanout_manifest_hash"] = "tampered"
    repo._runs = []

    fallback_registry = InternalEventConsumerRegistry()
    fallback_registry.register(
        PAYMENT_SUCCEEDED_EVENT_TYPE,
        "projection-fallback",
        lambda event, run: InternalEventConsumerResult(status="succeeded"),
    )
    fallback_registry.seal_fanout_contract()
    service = InternalEventOutboxReconciliationService(repo, fallback_registry)

    repaired_count, invalid_count = service._repair_missing_consumer_runs(limit=10)

    assert (repaired_count, invalid_count) == (0, 1)
    assert repo.list_consumer_runs({"event_id": event.event_id}) == ([], 0)


def test_scoped_worker_is_consumer_only_and_cannot_relay_pending_outbox(monkeypatch) -> None:
    _enable_internal_event_worker(monkeypatch, "projection-a")
    repo = InMemoryInternalEventRepository()
    registry = _registry("projection-a")
    record = repo.enqueue_outbox(_request("transactional-event:scoped-worker"))

    scoped = InternalEventWorker(repo, registry).run_due(
        batch_size=10,
        dry_run=False,
        event_types=[EVENT_TYPE],
        consumer_names=["projection-a"],
    )

    assert scoped["ok"] is True
    assert scoped["relay_role"] == "consumer_only"
    assert scoped["outbox_relay"]["enabled"] is False
    assert scoped["outbox_relay"]["reason"] == "consumer_only_worker"
    due = repo.list_due_outbox(limit=10)
    assert [item.outbox_id for item in due] == [record.outbox_id]

    owner = InternalEventWorker(repo, registry, relay_role="owner").run_due(
        batch_size=10,
        dry_run=False,
        event_types=[EVENT_TYPE],
        consumer_names=["projection-a"],
    )

    assert owner["ok"] is True
    assert owner["relay_role"] == "owner"
    assert owner["outbox_relay"]["counts"]["relayed_count"] == 1
    assert repo.list_due_outbox(limit=10) == []


def test_terminal_and_blocked_are_manual_only_and_do_not_gain_attempts(monkeypatch) -> None:
    _enable_internal_event_worker(monkeypatch, "terminal", "blocked")
    repo = InMemoryInternalEventRepository()
    registry = InternalEventConsumerRegistry()
    registry.register(EVENT_TYPE, "terminal", lambda event, run: InternalEventConsumerResult(status="failed_terminal", error_code="invalid"))
    registry.register(EVENT_TYPE, "blocked", lambda event, run: InternalEventConsumerResult(status="blocked", error_code="operator_required"))
    service = InternalEventService(repo, registry)
    emitted = service.emit_event(
        event_type=EVENT_TYPE,
        aggregate_type="test_record",
        aggregate_id="2",
        idempotency_key="terminal-blocked:2",
    )

    first = InternalEventWorker(repo, registry).run_due(batch_size=10, dry_run=False)
    attempts_after_first = service.list_attempts(event_id=emitted["event"]["event_id"])
    runs_after_first = {run.consumer_name: run for run in service.list_consumer_runs({"event_id": emitted["event"]["event_id"]})[0]}
    second = InternalEventWorker(repo, registry).run_due(batch_size=10, dry_run=False)
    attempts_after_second = service.list_attempts(event_id=emitted["event"]["event_id"])
    runs_after_second = {run.consumer_name: run for run in service.list_consumer_runs({"event_id": emitted["event"]["event_id"]})[0]}

    assert first["ok"] is False
    assert first["exit_code"] == 1
    assert first["counts"]["failed_terminal_count"] == 1
    assert first["counts"]["blocked_count"] == 1
    assert second["counts"]["candidate_count"] == 0
    assert len(attempts_after_first) == len(attempts_after_second) == 2
    assert {name: run.attempt_count for name, run in runs_after_first.items()} == {"terminal": 1, "blocked": 1}
    assert {name: run.attempt_count for name, run in runs_after_second.items()} == {"terminal": 1, "blocked": 1}


def test_lost_lease_cannot_write_attempt_or_result() -> None:
    repo = InMemoryInternalEventRepository()
    registry = _registry("projection-a")
    event, runs = repo.create_event_with_consumer_runs(
        _request("lost-lease:1"),
        [InternalEventConsumerSpec(consumer_name="projection-a")],
    )
    acquired = repo.acquire_consumer_run(
        event_id=event.event_id,
        consumer_name=runs[0].consumer_name,
        locked_by="worker-old",
    )
    assert acquired is not None
    current = repo._find_run(acquired.id)
    assert current is not None
    current["lease_token"] = "iel-new-owner"
    current["locked_by"] = "worker-new"

    result = InternalEventWorker(repo, registry, locked_by="worker-old").dispatch_one(acquired)

    assert result["ok"] is False
    assert result["error"] == "lost_lease"
    assert repo.list_attempts(event_id=event.event_id) == []
    assert repo.get_consumer_run_by_id(acquired.id).attempt_count == 0


def test_manual_retry_requires_actor_reason_and_records_audit_without_execution_attempt_increment() -> None:
    repo = InMemoryInternalEventRepository()
    event, runs = repo.create_event_with_consumer_runs(
        _request("manual-retry:1"),
        [InternalEventConsumerSpec(consumer_name="projection-a")],
    )
    repo.mark_result(runs[0].id, status="failed_terminal", attempt_id="seed-terminal", error_code="invalid")
    before = repo.get_consumer_run_by_id(runs[0].id)

    assert repo.retry_consumer_run(event.event_id, "projection-a", actor_id="", actor_type="operator", reason="approved") is None
    assert repo.retry_consumer_run(event.event_id, "projection-a", actor_id="operator-1", actor_type="operator", reason="") is None
    retried = repo.retry_consumer_run(
        event.event_id,
        "projection-a",
        actor_id="operator-1",
        actor_type="operator",
        reason="payload was repaired for 13800138000",
    )

    assert retried is not None
    run, audit = retried
    assert run.status == "pending"
    assert run.attempt_count == before.attempt_count
    assert audit.status == "manual_retry"
    assert len(audit.request_summary_json["actor_ref_hash"]) == 16
    assert "operator-1" not in str(audit.request_summary_json)
    assert audit.request_summary_json["reason"] == "payload was repaired for [pii]"
    assert "13800138000" not in audit.error_message


def test_safe_emit_is_documented_as_shadow_only_and_payment_uses_transactional_outbox() -> None:
    shadow_source = (ROOT / "aicrm_next/platform_foundation/internal_events/shadow.py").read_text(encoding="utf-8")
    payment_source = (ROOT / "aicrm_next/public_product/h5_wechat_pay.py").read_text(encoding="utf-8")
    tree = ast.parse(payment_source)
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}

    assert "shadow telemetry" in shadow_source[shadow_source.index("def safe_emit") :]
    assert "enqueue_transactional_internal_event_outbox" in called
    assert "InternalEventService" not in payment_source
    assert "_emit_payment_succeeded_internal_event" not in payment_source


@pytest.mark.skipif(not _database_url(), reason="PostgreSQL integration database is not configured")
def test_postgres_outbox_obeys_caller_transaction_and_duplicate_relay_is_atomic() -> None:
    import psycopg

    database_url = _database_url()
    repo = SQLAlchemyInternalEventRepository(get_session_factory(database_url))
    registry = _registry("projection-a", "projection-b")

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        enqueue_transactional_internal_event_outbox(conn, _request("postgres-outbox:rollback"))
        conn.rollback()
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM internal_event_outbox WHERE idempotency_key = %s",
            ("postgres-outbox:rollback",),
        ).fetchone()["count"]
        assert count == 0

        enqueue_transactional_internal_event_outbox(conn, _request("postgres-outbox:commit"))
        conn.commit()

    first = InternalEventOutboxRelay(repo, registry).relay_due(limit=10)
    assert first["ok"] is True
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "UPDATE internal_event_outbox SET status = 'pending', internal_event_id = '', relayed_at = NULL "
            "WHERE idempotency_key = %s",
            ("postgres-outbox:commit",),
        )
        conn.commit()
    duplicate = InternalEventOutboxRelay(repo, registry).relay_due(limit=10)

    with psycopg.connect(database_url) as conn:
        event_count = conn.execute(
            "SELECT COUNT(*) FROM internal_event WHERE idempotency_key = %s",
            ("postgres-outbox:commit",),
        ).fetchone()[0]
        run_count = conn.execute(
            "SELECT COUNT(*) FROM internal_event_consumer_run r "
            "JOIN internal_event e ON e.event_id = r.event_id WHERE e.idempotency_key = %s",
            ("postgres-outbox:commit",),
        ).fetchone()[0]
        relayed_count = conn.execute(
            "SELECT COUNT(*) FROM internal_event_outbox WHERE idempotency_key = %s AND status = 'relayed'",
            ("postgres-outbox:commit",),
        ).fetchone()[0]

    assert duplicate["ok"] is True
    assert event_count == 1
    assert run_count == 2
    assert relayed_count == 1


@pytest.mark.skipif(not _database_url(), reason="PostgreSQL integration database is not configured")
def test_postgres_event_and_consumer_runs_rollback_together_and_lease_cas_is_atomic(monkeypatch) -> None:
    import psycopg

    database_url = _database_url()
    repo = SQLAlchemyInternalEventRepository(get_session_factory(database_url))
    original = repo._create_consumer_run_in_session

    def fail_consumer_insert(session, *, event, consumer):
        raise RuntimeError("injected consumer insert failure")

    monkeypatch.setattr(repo, "_create_consumer_run_in_session", fail_consumer_insert)
    with pytest.raises(RuntimeError, match="injected consumer insert failure"):
        repo.create_event_with_consumer_runs(
            _request("postgres-atomic:event-run"),
            [InternalEventConsumerSpec(consumer_name="projection-a")],
        )
    with psycopg.connect(database_url) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM internal_event WHERE idempotency_key = %s",
            ("postgres-atomic:event-run",),
        ).fetchone()[0] == 0

    monkeypatch.setattr(repo, "_create_consumer_run_in_session", original)
    event, runs = repo.create_event_with_consumer_runs(
        _request("postgres-lease:1"),
        [InternalEventConsumerSpec(consumer_name="projection-a")],
    )
    acquired = repo.acquire_consumer_run(event_id=event.event_id, consumer_name="projection-a", locked_by="worker-old")
    assert acquired is not None
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "UPDATE internal_event_consumer_run SET lease_token = 'iel-new-owner', locked_by = 'worker-new' WHERE id = %s",
            (runs[0].id,),
        )
        conn.commit()

    completed = repo.complete_consumer_attempt(
        run=acquired,
        status="succeeded",
        request_summary={},
        response_summary={},
        result_summary={"ok": True},
    )
    with psycopg.connect(database_url) as conn:
        attempt_count = conn.execute(
            "SELECT COUNT(*) FROM internal_event_consumer_attempt WHERE consumer_run_id = %s",
            (runs[0].id,),
        ).fetchone()[0]
        run_row = conn.execute(
            "SELECT status, attempt_count, lease_token FROM internal_event_consumer_run WHERE id = %s",
            (runs[0].id,),
        ).fetchone()

    assert completed is None
    assert attempt_count == 0
    assert run_row == ("running", 0, "iel-new-owner")


@pytest.mark.skipif(not _database_url(), reason="PostgreSQL integration database is not configured")
def test_postgres_count_only_reconciliation_repairs_missing_consumer_runs_without_execution() -> None:
    import psycopg

    database_url = _database_url()
    repo = SQLAlchemyInternalEventRepository(get_session_factory(database_url))
    registry = InternalEventConsumerRegistry()
    registry.register(PAYMENT_SUCCEEDED_EVENT_TYPE, "projection-a", lambda event, run: InternalEventConsumerResult(status="succeeded"))
    registry.register(PAYMENT_SUCCEEDED_EVENT_TYPE, "projection-b", lambda event, run: InternalEventConsumerResult(status="succeeded"))
    registry.seal_fanout_contract()
    request = InternalEventCreateRequest(
        event_type=PAYMENT_SUCCEEDED_EVENT_TYPE,
        aggregate_type="wechat_pay_order",
        aggregate_id="991",
        idempotency_key="payment.succeeded:RECONCILE_991",
        source_module="tests.test_internal_event_outbox",
        payload={"order": {"id": 991, "out_trade_no": "RECONCILE_991", "status": "paid"}},
        payload_summary={"order_id": 991, "status": "paid"},
    )
    event = repo.create_event(request)
    legacy_event = repo.create_event(
        InternalEventCreateRequest(
            event_type=PAYMENT_SUCCEEDED_EVENT_TYPE,
            aggregate_type="wechat_pay_order",
            aggregate_id="990",
            idempotency_key="payment.succeeded:RECONCILE_LEGACY_990",
            source_module="tests.test_internal_event_outbox",
            payload={"order": {"id": 990, "out_trade_no": "RECONCILE_LEGACY_990", "status": "paid"}},
            payload_summary={"order_id": 990, "status": "paid"},
        )
    )
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "UPDATE internal_event SET created_at = TIMESTAMPTZ '2026-07-01 00:00:00+00' WHERE event_id = %s",
            (legacy_event.event_id,),
        )
        conn.commit()
    service = InternalEventOutboxReconciliationService(repo, registry, database_url=database_url)

    before = service.diagnose()
    dry_run = service.repair(dry_run=True)
    still_missing, _ = repo.list_consumer_runs({"event_id": event.event_id})
    repaired = service.repair(dry_run=False)
    runs, total = repo.list_consumer_runs({"event_id": event.event_id})

    assert before["event_missing_consumer_run_count"] == 2
    assert before["legacy_event_missing_consumer_run_count"] == 2
    assert before["legacy_paid_without_outbox_count"] >= 0
    assert before["actionable_cutover_at"] == "2026-07-13T09:46:09Z"
    assert before["pii_in_output"] is False
    assert dry_run["dry_run"] is True
    assert still_missing == []
    assert repaired["ok"] is True
    assert repaired["repaired"]["consumer_run_count"] == 2
    assert repaired["after"]["event_missing_consumer_run_count"] == 0
    assert total == 2
    legacy_runs, legacy_total = repo.list_consumer_runs({"event_id": legacy_event.event_id})
    assert legacy_runs == []
    assert legacy_total == 0
    assert {run.consumer_name for run in runs} == {"projection-a", "projection-b"}
    assert all(run.attempt_count == 0 for run in runs)
