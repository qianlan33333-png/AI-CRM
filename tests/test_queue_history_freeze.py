from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os

import psycopg
from psycopg.rows import dict_row

from aicrm_next.platform_foundation.external_effects.models import ExternalEffectCreateRequest
from aicrm_next.platform_foundation.external_effects.repo import SQLAlchemyExternalEffectRepository
from aicrm_next.platform_foundation.external_effects.repo_memory import InMemoryExternalEffectRepository
from aicrm_next.platform_foundation.internal_events.models import InternalEventCreateRequest
from aicrm_next.platform_foundation.internal_events.repository_memory import InMemoryInternalEventRepository
from aicrm_next.platform_foundation.internal_events.repository import SQLAlchemyInternalEventRepository
from aicrm_next.platform_foundation.internal_events.repository_support import automatic_due_predicate_sql
from aicrm_next.platform_foundation.webhook_inbox.repository import (
    InMemoryWebhookInboxRepository,
    PostgresWebhookInboxRepository,
)
from scripts.ops.snapshot_queue_history_classification import FREEZE_REVISION, snapshot


def test_memory_repositories_never_claim_held_rows() -> None:
    external = InMemoryExternalEffectRepository()
    job = external.create_job(
        ExternalEffectCreateRequest(
            effect_type="webhook.test",
            adapter_name="http",
            operation="post",
            target_type="loopback",
            target_id="held",
            idempotency_key="held-external",
        )
    )
    external._find(job.id)["hold_reason"] = "history_frozen_at_0125"
    assert external.list_due_jobs() == []
    assert external.acquire_job(job.id, locked_by="test") is None
    assert external.queue_metrics({})["held_count"] == 1
    assert external.queue_metrics({})["eligible_due_count"] == 0

    internal = InMemoryInternalEventRepository()
    event = internal.create_event(
        InternalEventCreateRequest(
            event_type="payment.succeeded",
            aggregate_type="order",
            aggregate_id="held",
            idempotency_key="held-event",
        )
    )
    run = internal.create_consumer_run(event=event, consumer_name="held-consumer")
    internal._find_run(run.id)["hold_reason"] = "history_frozen_at_0125"
    assert internal.list_due_runs() == []
    assert (
        internal.acquire_consumer_run(
            event_id=event.event_id,
            consumer_name="held-consumer",
            locked_by="test",
            force=True,
        )
        is None
    )
    assert internal.queue_metrics({})["held_count"] == 1
    assert internal.queue_metrics({})["eligible_due_count"] == 0

    webhook = InMemoryWebhookInboxRepository()
    inbox = webhook.ingest(
        provider="wecom",
        event_family="contact_change",
        route="/callback",
        idempotency_key="held-webhook",
    )
    webhook.rows[0]["hold_reason"] = "history_frozen_at_0125"
    assert webhook.preview_due(provider="wecom") == []
    assert webhook.claim_one(int(inbox["id"])) is None
    assert webhook.queue_metrics({})["held_count"] == 1
    assert webhook.queue_metrics({})["eligible_due_count"] == 0


def test_postgres_claim_paths_never_claim_held_rows(next_pg_schema) -> None:
    external = SQLAlchemyExternalEffectRepository()
    external_job = external.create_job(
        ExternalEffectCreateRequest(
            effect_type="webhook.test",
            adapter_name="http",
            operation="post",
            target_type="loopback",
            target_id="held-pg",
            idempotency_key="held-external-pg",
        )
    )
    internal = SQLAlchemyInternalEventRepository()
    event = internal.create_event(
        InternalEventCreateRequest(
            event_type="payment.succeeded",
            aggregate_type="order",
            aggregate_id="held-pg",
            idempotency_key="held-event-pg",
        )
    )
    run = internal.create_consumer_run(event=event, consumer_name="held-consumer-pg")
    outbox = internal.enqueue_outbox(
        InternalEventCreateRequest(
            event_type="payment.succeeded",
            aggregate_type="order",
            aggregate_id="held-pg",
            idempotency_key="held-outbox-pg",
        )
    )
    webhook_repo = PostgresWebhookInboxRepository(os.environ["DATABASE_URL"])
    inbox = webhook_repo.ingest(
        provider="wecom",
        event_family="contact_change",
        route="/callback",
        method="POST",
        tenant_id="aicrm",
        corp_id="",
        event_type="add_external_contact",
        change_type="add_external_contact",
        external_event_id="",
        idempotency_key="held-webhook-pg",
        raw_query_json={},
        raw_headers_json={},
        raw_body=b"",
        payload_xml="",
        payload_json={},
        payload_summary_json={},
    )

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
        connection.execute(
            "UPDATE external_effect_job SET hold_reason = 'test_hold', hold_at = CURRENT_TIMESTAMP WHERE id = %s",
            (external_job.id,),
        )
        connection.execute(
            "UPDATE internal_event_consumer_run SET hold_reason = 'test_hold', hold_at = CURRENT_TIMESTAMP WHERE id = %s",
            (run.id,),
        )
        connection.execute(
            "UPDATE internal_event_outbox SET hold_reason = 'test_hold', hold_at = CURRENT_TIMESTAMP WHERE id = %s",
            (outbox.id,),
        )
        connection.execute(
            "UPDATE webhook_inbox SET hold_reason = 'test_hold', hold_at = CURRENT_TIMESTAMP WHERE id = %s",
            (int(inbox["id"]),),
        )
        connection.commit()

    assert external.list_due_jobs() == []
    assert external.acquire_job(external_job.id, locked_by="test") is None
    assert internal.list_due_runs() == []
    assert (
        internal.acquire_consumer_run(
            event_id=event.event_id,
            consumer_name="held-consumer-pg",
            locked_by="test",
            force=True,
        )
        is None
    )
    assert internal.list_due_outbox() == []
    assert internal.acquire_due_outbox(locked_by="test") == []
    assert webhook_repo.preview_due(provider="wecom") == []
    assert webhook_repo.claim_one(int(inbox["id"])) is None


def test_internal_due_predicate_places_hold_guard_around_every_status_branch() -> None:
    predicate = " ".join(automatic_due_predicate_sql("r").split())

    assert predicate.startswith("( r.hold_reason = '' AND (")
    assert predicate.endswith(") )")


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _SnapshotConnection:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, sql: str, params=None):
        self.sql.append(sql)
        if "GROUP BY queue_kind" in sql:
            return _Result(
                [
                    {"queue_kind": "external_effect", "classification": "safe_pre_provider", "count": 2},
                    {"queue_kind": "external_effect", "classification": "terminal_readonly", "count": 1},
                ]
            )
        if "queue_row_id" in sql:
            return _Result(
                [
                    {
                        "queue_kind": "external_effect",
                        "queue_row_id": 7,
                        "source_status": "queued",
                        "classification": "safe_pre_provider",
                        "hold_reason": "history_frozen_at_0125",
                        "classified_at": datetime(2026, 7, 17, tzinfo=timezone.utc),
                    }
                ]
            )
        return _Result([{"count": 2 if "external_effect_job" in sql else 0}])


@contextmanager
def _connection_factory(_database_url: str):
    connection = _SnapshotConnection()
    yield connection
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in connection.sql)


def test_history_snapshot_command_is_read_only_and_redacted() -> None:
    payload = snapshot(
        "postgresql://localhost/aicrm",
        sample_limit=10,
        connection_factory=_connection_factory,
    )

    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert payload["freeze_revision"] == FREEZE_REVISION
    assert payload["classified_total"] == 3
    assert payload["held_classification_total"] == 2
    assert payload["live_holds_by_queue"]["external_effect"] == 2
    assert payload["samples"] == [
        {
            "queue_kind": "external_effect",
            "queue_row_id": 7,
            "source_status": "queued",
            "classification": "safe_pre_provider",
            "hold_reason": "history_frozen_at_0125",
            "classified_at": "2026-07-17T00:00:00+00:00",
        }
    ]
