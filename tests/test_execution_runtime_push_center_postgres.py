"""PostgreSQL contract for Push Center queue truth and runtime claim order."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.platform_foundation.execution_runtime.repository import (
    ExecutionRuntimeRepository,
)
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform_foundation.push_center.sql_read_model import SQLPushCenterReadModel


pytestmark = pytest.mark.usefixtures("next_pg_schema")


def _database_url() -> str:
    return str(os.environ.get("DATABASE_URL") or os.environ.get("AICRM_TEST_DATABASE_URL") or "")


def _connect():
    return psycopg.connect(_database_url(), autocommit=True, row_factory=dict_row)


@pytest.fixture(autouse=True)
def _reset_queue_policy() -> None:
    with _connect() as connection:
        connection.execute("DELETE FROM queue_fairness_cursor")
        connection.execute("DELETE FROM queue_rate_scope_cooldown")
        connection.execute(
            """
            UPDATE queue_runtime_control
            SET active_generation = 7,
                claim_enabled = TRUE,
                rollout_mode = 'execute',
                global_max_in_flight = 20,
                policy_version = 'queue-v2-test-loopback',
                external_claim_scope = 'test_loopback'
            WHERE singleton = TRUE
            """
        )
        connection.execute(
            """
            UPDATE queue_lane_policy
            SET max_in_flight = 10,
                enabled = TRUE,
                rollout_mode = 'execute',
                blocked_until = NULL,
                policy_version = 'queue-v2-test-loopback'
            WHERE lane = 'wecom_interactive'
            """
        )


def _job(
    *,
    execution_scope: str = "test_loopback",
    fairness_key: str,
    priority: int,
    available_at: datetime,
    ordering_key: str = "",
    rate_scope_key: str = "",
) -> dict:
    suffix = uuid4().hex
    return ExternalEffectService().plan_effect(
        effect_type="test.push-center.queue-truth",
        adapter_name="test_provider",
        operation="send",
        target_type="test_target",
        target_id=f"target-{suffix}",
        business_type="push_center_queue_truth",
        business_id=f"business-{suffix}",
        payload={"execution_scope": execution_scope} if execution_scope else {},
        idempotency_key=f"push-center-queue-truth-{suffix}",
        status="queued",
        lane="wecom_interactive",
        ordering_key=ordering_key,
        fairness_key=fairness_key,
        rate_scope_key=rate_scope_key,
        priority=priority,
        available_at=available_at,
    )


def test_push_center_queue_truth_matches_scope_and_canonical_claim_order() -> None:
    now = datetime.now(timezone.utc)
    scope_gated = _job(
        execution_scope="",
        fairness_key="scope-gated",
        priority=1,
        available_at=now - timedelta(minutes=30),
    )
    fairness_delayed = _job(
        fairness_key="recently-served",
        priority=1,
        available_at=now - timedelta(minutes=20),
    )
    priority_ahead = _job(
        fairness_key="priority-ahead",
        priority=50,
        available_at=now - timedelta(seconds=30),
    )
    target = _job(
        fairness_key="target",
        priority=100,
        available_at=now - timedelta(minutes=1),
    )
    _job(
        fairness_key="scheduled",
        priority=1,
        available_at=now + timedelta(minutes=5),
    )
    _job(
        fairness_key="rate-limited",
        rate_scope_key="provider:corp-read-model:send",
        priority=1,
        available_at=now - timedelta(minutes=15),
    )
    ordering_active = _job(
        fairness_key="ordering-active",
        ordering_key="customer-serial",
        priority=1,
        available_at=now - timedelta(minutes=10),
    )
    _job(
        fairness_key="ordering-blocked",
        ordering_key="customer-serial",
        priority=1,
        available_at=now - timedelta(minutes=9),
    )
    stale_generation = _job(
        fairness_key="stale-generation",
        priority=1,
        available_at=now - timedelta(minutes=8),
    )
    stale_policy = _job(
        fairness_key="stale-policy",
        priority=1,
        available_at=now - timedelta(minutes=7),
    )
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO queue_fairness_cursor (lane, fairness_key, last_claimed_at, claim_count)
            VALUES ('wecom_interactive', 'recently-served', CURRENT_TIMESTAMP, 1)
            """
        )
        connection.execute(
            """
            INSERT INTO queue_rate_scope_cooldown (
                rate_scope_key, provider, operation, blocked_until, reason
            ) VALUES (%s, 'test_provider', 'send', CURRENT_TIMESTAMP + INTERVAL '5 minutes', 'pytest')
            """,
            ("provider:corp-read-model:send",),
        )
        connection.execute(
            """
            UPDATE external_effect_job
            SET status = 'dispatching',
                lease_token = 'read-model-ordering-active',
                lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '5 minutes',
                worker_generation = 7
            WHERE id = %s
            """,
            (ordering_active["id"],),
        )
        connection.execute(
            "UPDATE external_effect_job SET worker_generation = 6 WHERE id = %s",
            (stale_generation["id"],),
        )
        connection.execute(
            "UPDATE external_effect_job SET policy_version = 'stale-policy' WHERE id = %s",
            (stale_policy["id"],),
        )

    read_model = SQLPushCenterReadModel()
    gated_page = read_model.query({"target_id": scope_gated["target_id"]}, limit=10)
    gated_detail = read_model.get(f"external_effect_job:{scope_gated['id']}")
    target_detail = read_model.get(f"external_effect_job:{target['id']}")

    assert len(gated_page.items) == 1
    assert gated_page.items[0]["queue_state"] == "held"
    assert gated_page.items[0]["wait_reason"] == "external_claim_scope_policy_gated"
    assert gated_page.items[0]["policy_gated"] is True
    assert gated_detail is not None
    assert gated_detail["queue_state"] == "held"
    assert gated_detail["wait_reason"] == "external_claim_scope_policy_gated"
    assert gated_detail["lane_ahead_count"] == 0
    assert gated_detail["queue_position_eligible"] is False

    assert target_detail is not None
    assert target_detail["queue_state"] == "waiting"
    assert target_detail["wait_reason"] == "waiting_for_lane_capacity"
    assert target_detail["lane_ahead_count"] == 1
    assert target_detail["queue_position_eligible"] is True

    claim = ExecutionRuntimeRepository(_database_url()).claim_external_effect_one(
        lane="wecom_interactive",
        worker_id="push-center-queue-truth",
        generation=7,
    )
    assert claim is not None
    assert claim.item_id == priority_ahead["id"]
    assert claim.item_id != fairness_delayed["id"]
