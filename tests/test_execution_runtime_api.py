from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from aicrm_next.platform.platform_foundation.execution_runtime import api
from aicrm_next.platform.platform_foundation.execution_runtime.read_model import ExecutionRuntimeReadModel


def _body(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def test_runtime_endpoint_returns_lane_and_release_snapshot(monkeypatch) -> None:
    class FakeReadModel:
        def runtime_snapshot(self):
            return {
                "ok": True,
                "control": {"active_generation": 0, "claim_enabled": False},
                "lanes": [{"lane": "wecom_media", "eligible": 0, "held": 2450}],
                "workers": [],
                "release": {"all_fresh_workers_match_release": False},
                "pii_in_output": False,
                "secrets_in_output": False,
            }

    monkeypatch.setattr(api, "ExecutionRuntimeReadModel", FakeReadModel)

    response = api.get_execution_runtime()
    body = _body(response)

    assert response.status_code == 200
    assert body["route_owner"] == "aicrm_next.platform.platform_foundation"
    assert body["control"]["claim_enabled"] is False
    assert body["lanes"][0]["held"] == 2450
    assert body["pii_in_output"] is False


def test_execution_timeline_returns_explicit_parent_links(monkeypatch) -> None:
    class FakeReadModel:
        def execution_timeline(self, execution_id: str):
            assert execution_id == "exe_root"
            return {
                "execution_id": execution_id,
                "parent_execution_ids": [],
                "child_execution_ids": ["exe_child"],
                "items": [
                    {
                        "item_kind": "external_effect",
                        "execution_id": "exe_child",
                        "parent_execution_id": "exe_root",
                    }
                ],
                "pii_in_output": False,
                "secrets_in_output": False,
            }

    monkeypatch.setattr(api, "ExecutionRuntimeReadModel", FakeReadModel)

    response = api.get_execution_timeline("exe_root")
    body = _body(response)

    assert response.status_code == 200
    assert body["execution_id"] == "exe_root"
    assert body["child_execution_ids"] == ["exe_child"]
    assert body["items"][0]["parent_execution_id"] == "exe_root"


def test_execution_timeline_rejects_non_public_identifier() -> None:
    with pytest.raises(HTTPException) as exc_info:
        api.get_execution_timeline("trace:guessable-correlation")

    assert exc_info.value.status_code == 404


def test_runtime_endpoint_fails_closed_without_leaking_error_text(monkeypatch) -> None:
    class FailingReadModel:
        def runtime_snapshot(self):
            raise RuntimeError("postgresql://user:secret@db/private")

    monkeypatch.setattr(api, "ExecutionRuntimeReadModel", FailingReadModel)

    response = api.get_execution_runtime()
    body = _body(response)

    assert response.status_code == 503
    assert body["error"] == "execution_runtime_unavailable"
    assert body["error_class"] == "RuntimeError"
    assert "postgresql://user:secret" not in response.body.decode("utf-8")


def test_lane_summary_pushes_lane_scope_into_one_database_query() -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    class Result:
        def fetchall(self):
            return [
                {
                    "lane": "internal_general",
                    "max_in_flight": 1,
                    "enabled": True,
                    "rollout_mode": "canary",
                    "blocked_until": None,
                    "policy_version": "queue-v7",
                    "runtime_policy_version": "queue-v7",
                    "runtime_active_generation": 7,
                    "runtime_claim_enabled": True,
                    "runtime_rollout_mode": "canary",
                    "raw_open": 583,
                    "held": 583,
                    "eligible": 0,
                    "policy_gated": 0,
                    "scheduled": 0,
                    "retry_wait": 0,
                    "rate_limited": 0,
                    "in_flight": 0,
                    "unknown": 0,
                    "dlq": 0,
                    "oldest_eligible_age_seconds": 0,
                }
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement: str, params: tuple[object, ...]):
            calls.append((statement, params))
            return Result()

    model = ExecutionRuntimeReadModel(
        "postgresql://runtime-read",
        connect=lambda database_url: Connection(),
    )

    summary = model.lane_summary(frozenset({"internal_general"}))

    assert summary["policy_version"] == "queue-v7"
    assert summary["raw_open"] == 583
    assert summary["held"] == 583
    assert summary["eligible"] == 0
    assert len(calls) == 1
    statement, params = calls[0]
    assert statement.count("lane = ANY(%s::text[])") == 5
    assert params == (["internal_general"],) * 5
    assert "queue_worker_heartbeat" not in statement
    assert "queue_policy_snapshot" not in statement


def test_lane_summary_returns_zero_without_query_for_empty_scope() -> None:
    model = ExecutionRuntimeReadModel(
        "postgresql://runtime-read",
        connect=lambda database_url: (_ for _ in ()).throw(AssertionError("database should not be queried")),
    )

    summary = model.lane_summary(frozenset())

    assert summary["lanes"] == []
    assert summary["raw_open"] == 0
    assert summary["rollout_mode"] == "blocked"
