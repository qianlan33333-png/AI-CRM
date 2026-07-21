from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.platform_foundation.execution_runtime.commands import (
    QueueCommandConflict,
    QueueRuntimeCommandService,
)
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from tests.admin_auth_test_helpers import install_admin_action_tokens


pytestmark = pytest.mark.usefixtures("next_pg_schema")


def _connect():
    return psycopg.connect(
        str(os.environ["DATABASE_URL"]),
        autocommit=True,
        row_factory=dict_row,
    )


def _seed_internal_run(
    *,
    consumer_name: str = "pytest_command_consumer",
    status: str = "pending",
    attempt_count: int = 0,
    max_attempts: int = 5,
) -> dict:
    key = uuid4().hex
    event_id = f"iev_command_{key}"
    execution_id = f"exe_internal_command_{key}"
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO internal_event (
                event_id, event_type, aggregate_type, aggregate_id,
                idempotency_key, execution_id
            ) VALUES (%s, 'test.admin.queue.command', 'test', %s, %s, %s)
            """,
            (event_id, key, f"event-{key}", f"exe_event_{key}"),
        )
        row = connection.execute(
            """
            INSERT INTO internal_event_consumer_run (
                event_id, consumer_name, status, execution_id,
                parent_execution_id, lane, available_at,
                ordering_key, fairness_key, policy_version,
                attempt_count, max_attempts
            ) VALUES (
                %s, %s, %s, %s, %s, 'internal_general',
                CURRENT_TIMESTAMP + INTERVAL '1 hour', %s, 'pytest', 'queue-v2-test-loopback',
                %s, %s
            )
            RETURNING id, event_id, consumer_name, execution_id
            """,
            (
                event_id,
                consumer_name,
                status,
                execution_id,
                f"exe_event_{key}",
                f"order-{key}",
                int(attempt_count),
                int(max_attempts),
            ),
        ).fetchone()
    return dict(row)


def _seed_webhook(*, status: str = "received") -> dict:
    key = uuid4().hex
    with _connect() as connection:
        row = connection.execute(
            """
            INSERT INTO webhook_inbox (
                provider, event_family, route, idempotency_key,
                execution_id, lane, available_at, ordering_key,
                fairness_key, policy_version, status
            ) VALUES (
                'wecom', 'external_contact', '/tests/admin-queue-command', %s,
                %s, 'webhook_inbox', CURRENT_TIMESTAMP + INTERVAL '1 hour',
                %s, 'wecom:external_contact', 'queue-v2-test-loopback', %s
            )
            RETURNING id, execution_id
            """,
            (
                f"webhook-{key}",
                f"exe_webhook_command_{key}",
                f"order-{key}",
                status,
            ),
        ).fetchone()
    return dict(row)


def _assert_accepted(body: dict, *, queue_kind: str, item_id: int) -> None:
    assert body["ok"] is True
    assert body["accepted"] is True
    assert body["queue_kind"] == queue_kind
    assert body["item_id"] == item_id
    assert body["execution_id"]
    assert body["command_id"].startswith("qcmd_")
    assert body["intent_id"].startswith("ieo_")
    assert body["status_url"] == f"/api/admin/executions/{body['execution_id']}"
    assert body["actor"] == "admin-user:test"
    assert body["reason"] == "manual durable wake"
    assert body["real_external_call_executed"] is False


def _seed_external_effect(*, status: str) -> dict:
    return ExternalEffectService().plan_effect(
        effect_type="test.admin.manual.queue.command",
        adapter_name="forbidden_provider",
        operation="send",
        target_type="test_target",
        target_id=f"target-{uuid4().hex}",
        payload={"execution_scope": "test_loopback"},
        idempotency_key=f"admin-manual-queue-command-{uuid4().hex}",
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
        lane="wecom_interactive",
        status=status,
    )


def _assert_required_command_fields(client, url: str, *, headers: dict, payload: dict) -> None:
    for field in ("actor", "reason", "expected_version"):
        incomplete = dict(payload)
        incomplete.pop(field, None)
        response = client.post(url, headers=headers, json=incomplete)
        assert response.status_code == 422
        assert response.json()["error"] == "manual_queue_command_fields_required"
        assert response.json()["missing_fields"] == [field]


def test_internal_run_due_execute_accepts_one_durable_command_without_handler(
    next_client,
    monkeypatch,
) -> None:
    row = _seed_internal_run()
    service = QueueRuntimeCommandService()
    target = service.read_target("internal_event", int(row["id"]))
    assert target is not None
    token = install_admin_action_tokens(
        next_client,
        ("POST", "/api/admin/internal-events/run-due"),
    )[("POST", "/api/admin/internal-events/run-due")]
    monkeypatch.setattr(
        "aicrm_next.platform_foundation.internal_events.worker.InternalEventWorker.run_due",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("handler worker is forbidden")),
    )

    url = "/api/admin/internal-events/run-due"
    headers = {"X-Admin-Action-Token": token}
    payload = {
        "dry_run": False,
        "item_id": row["id"],
        "actor": "pytest-operator",
        "reason": "manual durable wake",
        "expected_version": target.version_token,
    }
    _assert_required_command_fields(next_client, url, headers=headers, payload=payload)
    response = next_client.post(url, headers=headers, json=payload)

    assert response.status_code == 202
    _assert_accepted(response.json(), queue_kind="internal_event", item_id=int(row["id"]))
    with _connect() as connection:
        persisted = connection.execute(
            "SELECT status, available_at FROM internal_event_consumer_run WHERE id = %s",
            (int(row["id"]),),
        ).fetchone()
        attempt_count = connection.execute(
            "SELECT COUNT(*) AS count FROM internal_event_consumer_attempt WHERE consumer_run_id = %s",
            (int(row["id"]),),
        ).fetchone()["count"]
    assert persisted["status"] == "pending"
    assert persisted["available_at"] <= datetime.now(timezone.utc)
    assert attempt_count == 0


def test_single_internal_consumer_execute_requires_version_and_returns_cas_conflict(
    next_client,
) -> None:
    row = _seed_internal_run(consumer_name="pytest_single_command")
    route = "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/run"
    token = install_admin_action_tokens(next_client, ("POST", route))[("POST", route)]
    target = QueueRuntimeCommandService().read_target("internal_event", int(row["id"]))
    assert target is not None
    url = f"/api/admin/internal-events/{row['event_id']}/consumers/{row['consumer_name']}/run"
    payload = {
        "dry_run": False,
        "actor": "pytest-operator",
        "reason": "manual durable wake",
        "expected_version": target.version_token,
    }
    headers = {"X-Admin-Action-Token": token}
    _assert_required_command_fields(next_client, url, headers=headers, payload=payload)
    accepted = next_client.post(url, headers={"X-Admin-Action-Token": token}, json=payload)
    stale = next_client.post(url, headers={"X-Admin-Action-Token": token}, json=payload)

    assert accepted.status_code == 202
    _assert_accepted(accepted.json(), queue_kind="internal_event", item_id=int(row["id"]))
    assert stale.status_code == 409
    assert stale.json()["error"] == "queue_command_cas_conflict"


@pytest.mark.parametrize(
    ("action", "initial_status", "expected_status"),
    (
        ("retry", "failed_terminal", "pending"),
        ("skip", "pending", "skipped"),
    ),
)
def test_internal_manual_actions_are_versioned_commands_with_durable_attempts(
    next_client,
    action: str,
    initial_status: str,
    expected_status: str,
) -> None:
    exhausted = action == "retry"
    row = _seed_internal_run(
        consumer_name=f"pytest_internal_{action}",
        status=initial_status,
        attempt_count=5 if exhausted else 0,
        max_attempts=5,
    )
    target = QueueRuntimeCommandService().read_target("internal_event", int(row["id"]))
    assert target is not None
    route = f"/api/admin/internal-events/{{event_id}}/consumers/{{consumer_name}}/{action}"
    token = install_admin_action_tokens(next_client, ("POST", route))[("POST", route)]
    url = route.replace("{event_id}", row["event_id"]).replace(
        "{consumer_name}", row["consumer_name"]
    )
    headers = {"X-Admin-Action-Token": token}
    payload = {
        "actor": "pytest-operator",
        "reason": "manual durable wake",
        "expected_version": target.version_token,
    }
    _assert_required_command_fields(next_client, url, headers=headers, payload=payload)

    accepted = next_client.post(url, headers=headers, json=payload)
    stale = next_client.post(url, headers=headers, json=payload)

    assert accepted.status_code == 202
    _assert_accepted(accepted.json(), queue_kind="internal_event", item_id=int(row["id"]))
    assert accepted.json()["action"] == action
    assert accepted.json()["status"] == expected_status
    assert stale.status_code == 409
    assert stale.json()["error"] == "queue_command_cas_conflict"
    with _connect() as connection:
        persisted = connection.execute(
            """
            SELECT status, attempt_count, max_attempts, last_attempt_id
            FROM internal_event_consumer_run WHERE id = %s
            """,
            (int(row["id"]),),
        ).fetchone()
        attempt = connection.execute(
            """
            SELECT status, request_summary_json, response_summary_json
            FROM internal_event_consumer_attempt
            WHERE consumer_run_id = %s
            ORDER BY id DESC LIMIT 1
            """,
            (int(row["id"]),),
        ).fetchone()
    assert persisted["status"] == expected_status
    assert persisted["last_attempt_id"]
    assert attempt["status"] == ("manual_retry" if action == "retry" else "skipped")
    assert attempt["request_summary_json"]["actor_ref_hash"]
    assert "pytest-operator" not in str(attempt["request_summary_json"])
    if action == "retry":
        assert persisted["attempt_count"] < persisted["max_attempts"]


def test_external_effect_run_due_execute_never_dispatches_provider(next_client, monkeypatch) -> None:
    scheduled_at = datetime.now(timezone.utc) + timedelta(hours=1)
    job = ExternalEffectService().plan_effect(
        effect_type="test.admin.queue.command",
        adapter_name="forbidden_provider",
        operation="send",
        target_type="test_target",
        target_id=f"target-{uuid4().hex}",
        payload={"execution_scope": "test_loopback"},
        idempotency_key=f"admin-queue-command-{uuid4().hex}",
        scheduled_at=scheduled_at,
        lane="wecom_interactive",
    )
    target = QueueRuntimeCommandService().read_target("external_effect", int(job["id"]))
    assert target is not None
    token = install_admin_action_tokens(
        next_client,
        ("POST", "/api/admin/external-effects/run-due"),
    )[("POST", "/api/admin/external-effects/run-due")]
    monkeypatch.setattr(
        "aicrm_next.platform_foundation.external_effects.worker.ExternalEffectWorker.run_due",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider worker is forbidden")),
    )

    url = "/api/admin/external-effects/run-due"
    headers = {"X-Admin-Action-Token": token}
    payload = {
        "dry_run": False,
        "item_id": job["id"],
        "actor": "pytest-operator",
        "reason": "manual durable wake",
        "expected_version": target.version_token,
        "test_only": True,
    }
    _assert_required_command_fields(next_client, url, headers=headers, payload=payload)
    response = next_client.post(url, headers=headers, json=payload)

    assert response.status_code == 202
    _assert_accepted(response.json(), queue_kind="external_effect", item_id=int(job["id"]))
    with _connect() as connection:
        persisted = connection.execute(
            """
            SELECT status, provider_call_started_at, available_at
            FROM external_effect_job WHERE id = %s
            """,
            (int(job["id"]),),
        ).fetchone()
        attempt_count = connection.execute(
            "SELECT COUNT(*) AS count FROM external_effect_attempt WHERE job_id = %s",
            (int(job["id"]),),
        ).fetchone()["count"]
    assert persisted["status"] == "queued"
    assert persisted["provider_call_started_at"] is None
    assert persisted["available_at"] <= datetime.now(timezone.utc)
    assert attempt_count == 0


@pytest.mark.parametrize(
    "route_template",
    (
        "/api/admin/webhook-inbox/{inbox_id}/dispatch",
        "/api/admin/webhook-inbox/run-due",
    ),
)
def test_webhook_execute_routes_accept_commands_without_dispatching_handler(
    next_client,
    monkeypatch,
    route_template: str,
) -> None:
    row = _seed_webhook()
    target = QueueRuntimeCommandService().read_target("webhook_inbox", int(row["id"]))
    assert target is not None
    token = install_admin_action_tokens(next_client, ("POST", route_template))[("POST", route_template)]
    monkeypatch.setattr(
        "aicrm_next.channel_entry.inbox.WeComCallbackInboxWorker.dispatch_one",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("callback handler is forbidden")),
    )
    monkeypatch.setattr(
        "aicrm_next.channel_entry.inbox.WeComCallbackInboxWorker.run_due",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("callback worker is forbidden")),
    )
    url = (
        route_template.replace("{inbox_id}", str(row["id"]))
        if "{inbox_id}" in route_template
        else route_template
    )

    headers = {"X-Admin-Action-Token": token}
    payload = {
        "dry_run": False,
        "provider": "wecom",
        "item_id": row["id"],
        "actor": "pytest-operator",
        "reason": "manual durable wake",
        "expected_version": target.version_token,
    }
    _assert_required_command_fields(next_client, url, headers=headers, payload=payload)
    response = next_client.post(url, headers=headers, json=payload)

    assert response.status_code == 202
    _assert_accepted(response.json(), queue_kind="webhook_inbox", item_id=int(row["id"]))
    with _connect() as connection:
        persisted = connection.execute(
            "SELECT status, available_at FROM webhook_inbox WHERE id = %s",
            (int(row["id"]),),
        ).fetchone()
    assert persisted["status"] == "received"
    assert persisted["available_at"] <= datetime.now(timezone.utc)


def test_queue_command_cas_has_one_concurrent_winner() -> None:
    row = _seed_webhook()
    service = QueueRuntimeCommandService()
    target = service.read_target("webhook_inbox", int(row["id"]))
    assert target is not None
    barrier = Barrier(2)

    def submit(index: int) -> str:
        barrier.wait(timeout=5)
        try:
            service.request_immediate_execution(
                "webhook_inbox",
                int(row["id"]),
                expected_status=target.status,
                expected_version=target.version_token,
                actor=f"pytest-{index}",
                reason="concurrent CAS proof",
            )
            return "accepted"
        except QueueCommandConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, (1, 2)))

    assert sorted(results) == ["accepted", "conflict"]


@pytest.mark.parametrize(
    "route_template",
    (
        "/api/admin/external-effects/jobs/{job_id}/retry",
        "/api/admin/push-center/jobs/{job_id}/retry",
    ),
)
def test_external_retry_routes_require_versioned_command_and_never_call_provider(
    next_client,
    route_template: str,
) -> None:
    job = _seed_external_effect(status="failed_retryable")
    target = QueueRuntimeCommandService().read_target("external_effect", int(job["id"]))
    assert target is not None
    token = install_admin_action_tokens(next_client, ("POST", route_template))[("POST", route_template)]
    url = route_template.replace("{job_id}", str(job["id"]))
    headers = {"X-Admin-Action-Token": token}
    payload = {
        "actor": "pytest-operator",
        "reason": "manual durable wake",
        "expected_version": target.version_token,
    }
    _assert_required_command_fields(next_client, url, headers=headers, payload=payload)

    accepted = next_client.post(url, headers=headers, json=payload)
    stale = next_client.post(url, headers=headers, json=payload)

    assert accepted.status_code == 202
    _assert_accepted(accepted.json(), queue_kind="external_effect", item_id=int(job["id"]))
    assert accepted.json()["action"] == "retry"
    assert stale.status_code == 409
    assert stale.json()["error"] == "queue_command_cas_conflict"
    with _connect() as connection:
        persisted = connection.execute(
            """
            SELECT status, provider_call_started_at, available_at
            FROM external_effect_job WHERE id = %s
            """,
            (int(job["id"]),),
        ).fetchone()
        attempt_count = connection.execute(
            "SELECT COUNT(*) AS count FROM external_effect_attempt WHERE job_id = %s",
            (int(job["id"]),),
        ).fetchone()["count"]
    assert persisted["status"] == "queued"
    assert persisted["provider_call_started_at"] is None
    assert persisted["available_at"] <= datetime.now(timezone.utc)
    assert attempt_count == 0


@pytest.mark.parametrize(
    "route_template",
    (
        "/api/admin/external-effects/jobs/{job_id}/cancel",
        "/api/admin/push-center/jobs/{job_id}/cancel",
    ),
)
def test_external_cancel_routes_are_strict_cas_commands(
    next_client,
    route_template: str,
) -> None:
    job = _seed_external_effect(status="queued")
    target = QueueRuntimeCommandService().read_target("external_effect", int(job["id"]))
    assert target is not None
    token = install_admin_action_tokens(next_client, ("POST", route_template))[("POST", route_template)]
    url = route_template.replace("{job_id}", str(job["id"]))
    headers = {"X-Admin-Action-Token": token}
    payload = {
        "actor": "pytest-operator",
        "reason": "manual durable wake",
        "expected_version": target.version_token,
    }
    _assert_required_command_fields(next_client, url, headers=headers, payload=payload)

    accepted = next_client.post(url, headers=headers, json=payload)
    stale = next_client.post(url, headers=headers, json=payload)

    assert accepted.status_code == 202
    _assert_accepted(accepted.json(), queue_kind="external_effect", item_id=int(job["id"]))
    assert accepted.json()["action"] == "cancel"
    assert accepted.json()["status"] == "cancelled"
    assert stale.status_code == 409
    assert stale.json()["error"] == "queue_command_cas_conflict"
    with _connect() as connection:
        persisted = connection.execute(
            "SELECT status, row_version FROM external_effect_job WHERE id = %s",
            (int(job["id"]),),
        ).fetchone()
        settlement = connection.execute(
            """
            SELECT event_type, payload_json, status
            FROM internal_event_outbox
            WHERE idempotency_key = %s
            """,
            (
                f"external_effect.settled:{job['id']}:cancelled:"
                f"row-version-{persisted['row_version']}",
            ),
        ).fetchone()
    assert persisted["status"] == "cancelled"
    assert settlement["event_type"] == "external_effect.settled"
    assert settlement["payload_json"]["attempt_id"] == ""
    assert settlement["status"] == "pending"


def test_unknown_external_retry_requires_duplicate_risk_confirmed(next_client) -> None:
    job = _seed_external_effect(status="failed_retryable")
    with _connect() as connection:
        connection.execute(
            """
            UPDATE external_effect_job
            SET status = 'unknown_after_dispatch',
                provider_call_started_at = CURRENT_TIMESTAMP,
                reconciliation_required = TRUE,
                row_version = row_version + 1
            WHERE id = %s
            """,
            (int(job["id"]),),
        )
    target = QueueRuntimeCommandService().read_target("external_effect", int(job["id"]))
    assert target is not None
    route = "/api/admin/external-effects/jobs/{job_id}/retry"
    token = install_admin_action_tokens(next_client, ("POST", route))[("POST", route)]
    url = route.replace("{job_id}", str(job["id"]))
    payload = {
        "actor": "pytest-operator",
        "reason": "manual durable wake",
        "expected_version": target.version_token,
    }

    missing_confirmation = next_client.post(
        url,
        headers={"X-Admin-Action-Token": token},
        json=payload,
    )
    payload["duplicate_risk_confirmed"] = True
    accepted = next_client.post(
        url,
        headers={"X-Admin-Action-Token": token},
        json=payload,
    )

    assert missing_confirmation.status_code == 409
    assert missing_confirmation.json()["error"] == "duplicate_risk_confirmation_required"
    assert accepted.status_code == 202
    assert accepted.json()["duplicate_risk_confirmed"] is True
    assert accepted.json()["action"] == "retry"


@pytest.mark.parametrize(
    ("action", "initial_status", "expected_status"),
    (
        ("retry", "dead_letter", "failed_retryable"),
        ("skip", "received", "ignored"),
    ),
)
def test_webhook_manual_actions_use_versioned_queue_commands(
    next_client,
    action: str,
    initial_status: str,
    expected_status: str,
) -> None:
    row = _seed_webhook(status=initial_status)
    target = QueueRuntimeCommandService().read_target("webhook_inbox", int(row["id"]))
    assert target is not None
    route = f"/api/admin/webhook-inbox/{{inbox_id}}/{action}"
    token = install_admin_action_tokens(next_client, ("POST", route))[("POST", route)]
    url = route.replace("{inbox_id}", str(row["id"]))
    headers = {"X-Admin-Action-Token": token}
    payload = {
        "actor": "pytest-operator",
        "reason": "manual durable wake",
        "expected_version": target.version_token,
    }
    _assert_required_command_fields(next_client, url, headers=headers, payload=payload)

    accepted = next_client.post(url, headers=headers, json=payload)
    stale = next_client.post(url, headers=headers, json=payload)

    assert accepted.status_code == 202
    _assert_accepted(accepted.json(), queue_kind="webhook_inbox", item_id=int(row["id"]))
    assert accepted.json()["action"] == action
    assert accepted.json()["status"] == expected_status
    assert stale.status_code == 409
    assert stale.json()["error"] == "queue_command_cas_conflict"
