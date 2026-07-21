from __future__ import annotations

import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from aicrm_next.external_effect_composition import (
    EXTERNAL_EFFECT_PROVIDER_RESULT_ACCESS_ALLOWLIST,
    build_external_effect_continuation_consumers,
    build_external_effect_continuation_registry,
    build_external_effect_settlement_consumers,
)
from aicrm_next.platform_foundation.execution_runtime.read_model import (
    TIMELINE_MAX_EXECUTION_NODES,
    ExecutionRuntimeReadModel,
)
from aicrm_next.platform_foundation.external_effects.completion_events import (
    EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE,
    register_external_effect_completed_consumers,
)
from aicrm_next.platform_foundation.external_effects.models import ExternalEffectDispatchResult
from aicrm_next.platform_foundation.external_effects.repo import SQLAlchemyExternalEffectRepository
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform_foundation.external_effects.settlement_events import (
    EXTERNAL_EFFECT_SETTLED_EVENT_TYPE,
    register_external_effect_settled_consumers,
)
from aicrm_next.platform_foundation.internal_events.consumer_registry import (
    InternalEventConsumerRegistry,
)
from aicrm_next.platform_foundation.internal_events.outbox import InternalEventOutboxRelay
from aicrm_next.platform_foundation.internal_events.repository import (
    SQLAlchemyInternalEventRepository,
)


def _database_url() -> str:
    return str(os.environ.get("DATABASE_URL") or os.environ.get("AICRM_TEST_DATABASE_URL") or "")


def _connect():
    return psycopg.connect(_database_url(), autocommit=True, row_factory=dict_row)


def _completion_registry() -> InternalEventConsumerRegistry:
    registry = InternalEventConsumerRegistry()
    register_external_effect_completed_consumers(
        registry,
        consumers=build_external_effect_continuation_consumers(),
        repository_factory=SQLAlchemyExternalEffectRepository,
        legacy_continuation_registry_factory=build_external_effect_continuation_registry,
        provider_result_access_allowlist=EXTERNAL_EFFECT_PROVIDER_RESULT_ACCESS_ALLOWLIST,
    )
    register_external_effect_settled_consumers(
        registry,
        consumers=build_external_effect_settlement_consumers(),
        repository_factory=SQLAlchemyExternalEffectRepository,
    )
    registry.seal_fanout_contract()
    return registry


def test_external_effect_root_recursively_includes_completion_fanout_and_attempts(
    next_pg_schema,
) -> None:
    external_repo = SQLAlchemyExternalEffectRepository()
    key = uuid4().hex
    planned = ExternalEffectService(external_repo).plan_effect(
        effect_type="test.timeline.recursive",
        adapter_name="test_provider",
        operation="send",
        target_type="test_target",
        target_id=f"target-{key}",
        payload={"execution_scope": "test_loopback"},
        payload_summary={"execution_scope": "test_loopback"},
        idempotency_key=f"timeline-recursive-{key}",
        status="queued",
        lane="wecom_interactive",
    )
    claimed = external_repo.acquire_job(planned["id"], locked_by="timeline-recursive-test")
    assert claimed is not None
    begun = external_repo.begin_provider_attempt(
        job=claimed,
        request_summary={"mobile": "13800000000", "token": "timeline-secret-token"},
    )
    assert begun is not None
    completed = external_repo.complete_dispatch(
        job=begun[0],
        result=ExternalEffectDispatchResult(
            status="succeeded",
            adapter_mode="test_loopback",
            response_summary={"mobile": "13800000000", "token": "timeline-secret-token"},
            provider_result_received=True,
            real_external_call_executed=False,
        ),
    )
    assert completed is not None

    internal_repo = SQLAlchemyInternalEventRepository()
    relay = InternalEventOutboxRelay(
        internal_repo,
        _completion_registry(),
        locked_by="timeline-recursive-relay",
    ).relay_due(limit=10)
    assert relay["ok"] is True
    assert relay["counts"]["relayed_count"] == 2
    assert relay["items"][0]["consumer_run_count"] == 7
    assert relay["items"][1]["consumer_run_count"] == 5

    runs = internal_repo.acquire_due_runs(
        limit=10,
        locked_by="timeline-recursive-consumers",
        event_types=[EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE],
    )
    expected_consumers = {
        consumer.consumer_name for consumer in build_external_effect_continuation_consumers()
    }
    expected_settlement_consumers = {
        consumer.consumer_name for consumer in build_external_effect_settlement_consumers()
    }
    assert {run.consumer_name for run in runs} == expected_consumers
    assert len(runs) == 7
    for run in runs:
        result = internal_repo.complete_consumer_attempt(
            run=run,
            status="succeeded",
            request_summary={"consumer_name": run.consumer_name, "token": "timeline-secret-token"},
            response_summary={"ok": True, "mobile": "13800000000"},
            result_summary={"ok": True},
        )
        assert result is not None

    root_execution_id = str(planned["execution_id"])
    with _connect() as connection:
        completion = connection.execute(
            """
            SELECT execution_id
            FROM internal_event
            WHERE event_type = %s AND parent_execution_id = %s
            """,
            (EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE, root_execution_id),
        ).fetchone()
        settlement = connection.execute(
            """
            SELECT execution_id
            FROM internal_event
            WHERE event_type = %s AND parent_execution_id = %s
            """,
            (EXTERNAL_EFFECT_SETTLED_EVENT_TYPE, root_execution_id),
        ).fetchone()
        run_rows = connection.execute(
            """
            SELECT execution_id
            FROM internal_event_consumer_run
            WHERE parent_execution_id = %s
            ORDER BY execution_id
            """,
            (completion["execution_id"],),
        ).fetchall()
        # Deliberately close the graph into a cycle. The read model must still
        # return each execution/item once and remain bounded.
        connection.execute(
            "UPDATE external_effect_job SET parent_execution_id = %s WHERE id = %s",
            (run_rows[0]["execution_id"], planned["id"]),
        )

    timeline = ExecutionRuntimeReadModel(_database_url()).execution_timeline(root_execution_id)
    assert timeline is not None
    items = timeline["items"]
    kinds = [item["item_kind"] for item in items]
    assert kinds.count("external_effect") == 1
    assert kinds.count("external_effect_attempt") == 1
    assert kinds.count("internal_outbox") == 2
    assert kinds.count("internal_event") == 2
    assert kinds.count("internal_consumer_run") == 12
    assert kinds.count("internal_consumer_attempt") == 7
    completion_items = [
        item
        for item in items
        if item["item_kind"] == "internal_event"
        and item["item_type"] == EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE
    ]
    assert len(completion_items) == 1
    assert completion_items[0]["parent_execution_id"] == root_execution_id
    settlement_items = [
        item
        for item in items
        if item["item_kind"] == "internal_event"
        and item["item_type"] == EXTERNAL_EFFECT_SETTLED_EVENT_TYPE
    ]
    assert len(settlement_items) == 1
    assert settlement_items[0]["parent_execution_id"] == root_execution_id
    assert settlement["execution_id"] == settlement_items[0]["execution_id"]
    assert {
        item["item_type"] for item in items if item["item_kind"] == "internal_consumer_run"
    } == expected_consumers | expected_settlement_consumers
    assert len({(item["item_kind"], item["item_id"]) for item in items}) == len(items)
    assert timeline["graph"] == {
        "execution_node_count": 15,
        "edge_count": 15,
        "max_depth_reached": 2,
        "max_depth": 12,
        "max_execution_nodes": 256,
        "max_items": 1024,
        "truncated": False,
    }
    assert "13800000000" not in str(timeline)
    assert "timeline-secret-token" not in str(timeline)

    leaf_timeline = ExecutionRuntimeReadModel(_database_url()).execution_timeline(
        run_rows[0]["execution_id"]
    )
    assert leaf_timeline is not None
    assert leaf_timeline["graph"]["execution_node_count"] == 15
    assert sum(
        item["item_kind"] == "internal_consumer_run" for item in leaf_timeline["items"]
    ) == 12


class _FakeRows:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, str]]:
        return list(self._rows)


class _FakeGraphConnection:
    def __init__(self, edges: set[tuple[str, str]]) -> None:
        self._edges = edges

    def execute(self, _sql: str, params: tuple) -> _FakeRows:
        frontier = set(params[0])
        limit = int(params[-1])
        rows = [
            {"execution_id": child, "parent_execution_id": parent}
            for child, parent in sorted(self._edges)
            if child in frontier or parent in frontier
        ]
        return _FakeRows(rows[:limit])


def test_execution_graph_is_cycle_safe_and_enforces_the_global_node_cap() -> None:
    root = "exe_root"
    children = {f"exe_child_{index:04d}" for index in range(TIMELINE_MAX_EXECUTION_NODES + 50)}
    graph = ExecutionRuntimeReadModel._discover_execution_graph(
        _FakeGraphConnection({(child, root) for child in children}),
        root,
    )

    assert len(graph["execution_ids"]) == TIMELINE_MAX_EXECUTION_NODES
    assert len(graph["edges"]) == TIMELINE_MAX_EXECUTION_NODES - 1
    assert graph["truncated"] is True

    cycle = ExecutionRuntimeReadModel._discover_execution_graph(
        _FakeGraphConnection({("exe_child", root), (root, "exe_child")}),
        root,
    )
    assert cycle["execution_ids"] == {root, "exe_child"}
    assert cycle["edges"] == {("exe_child", root), (root, "exe_child")}
    assert cycle["truncated"] is False
