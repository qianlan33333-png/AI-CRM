from __future__ import annotations

import pytest

from aicrm_next.platform.platform_foundation.execution_runtime.repository import (
    ExecutionRuntimeRepository,
    LanePolicy,
)
from scripts import run_execution_runtime


def test_runtime_defaults_to_claimless_standby(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_QUEUE_RUNTIME_EXECUTE", raising=False)
    monkeypatch.delenv("AICRM_QUEUE_WORKER_GENERATION", raising=False)

    args = run_execution_runtime._parse_args(["--queue-kind", "internal"])

    assert args.execute is False
    assert args.generation == 0


def test_internal_worker_role_combines_inbox_and_internal_lanes(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_QUEUE_RUNTIME_EXECUTE", raising=False)
    monkeypatch.delenv("AICRM_QUEUE_WORKER_GENERATION", raising=False)

    internal = run_execution_runtime._parse_args(["--role", "internal_worker"])
    external = run_execution_runtime._parse_args(["--role", "external_worker"])

    assert run_execution_runtime._selected_queue_kinds(internal) == ("internal", "webhook")
    assert run_execution_runtime._selected_queue_kinds(external) == ("external",)
    assert internal.execute is False


def test_explicit_standby_overrides_an_armed_shared_generation(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_QUEUE_RUNTIME_EXECUTE", "1")
    monkeypatch.setenv("AICRM_QUEUE_WORKER_GENERATION", "17")

    args = run_execution_runtime._parse_args(
        ["--role", "internal_worker", "--standby"]
    )

    assert args.execute is False
    assert args.generation == 17


def test_role_worker_identity_cannot_overwrite_legacy_queue_heartbeats(monkeypatch) -> None:
    monkeypatch.setattr(run_execution_runtime.socket, "gethostname", lambda: "queue-host")
    legacy = run_execution_runtime._parse_args(["--queue-kind", "internal"])
    combined = run_execution_runtime._parse_args(
        ["--role", "internal_worker", "--standby"]
    )

    assert run_execution_runtime._worker_identity(
        legacy, queue_kind="internal"
    ) == "queue-host:internal"
    assert run_execution_runtime._worker_identity(
        combined, queue_kind="internal"
    ) == "queue-host:role:internal_worker:internal"
    assert run_execution_runtime._worker_identity(
        combined, queue_kind="webhook"
    ) == "queue-host:role:internal_worker:webhook"


def test_runtime_can_be_armed_from_numeric_generation_environment(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_QUEUE_RUNTIME_EXECUTE", "1")
    monkeypatch.setenv("AICRM_QUEUE_RUNTIME_TEST_ONLY", "1")
    monkeypatch.setenv("AICRM_QUEUE_WORKER_GENERATION", "17")

    args = run_execution_runtime._parse_args(["--queue-kind", "external"])

    assert args.execute is True
    assert args.test_only is True
    assert args.generation == 17


def test_worker_id_is_stable_for_the_same_host_and_queue(monkeypatch) -> None:
    captured: list[str] = []

    monkeypatch.setattr(run_execution_runtime.socket, "gethostname", lambda: "queue-host")
    monkeypatch.setattr(
        run_execution_runtime,
        "build_external_effect_adapter_registry",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        run_execution_runtime,
        "build_wecom_callback_inbox_worker_factory",
        lambda **_kwargs: lambda: object(),
    )

    def capture_service(**kwargs):
        captured.append(str(kwargs["worker_id"]))
        return object()

    monkeypatch.setattr(run_execution_runtime, "_service", capture_service)
    args = run_execution_runtime._parse_args(["--queue-kind", "webhook"])

    run_execution_runtime._build_services(args)
    run_execution_runtime._build_services(args)

    assert captured == ["queue-host:webhook", "queue-host:webhook"]


def test_external_runtime_composes_dedicated_ai_assistant_bulk_lane(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        run_execution_runtime,
        "build_external_effect_adapter_registry",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        run_execution_runtime,
        "_service",
        lambda **kwargs: captured.append(kwargs) or object(),
    )
    args = run_execution_runtime._parse_args(["--queue-kind", "external"])

    run_execution_runtime._build_services(args)

    assert captured[0]["lane_names"] == (
        "ai_generation",
        "wecom_welcome",
        "wecom_interactive",
        "wecom_bulk",
        "wecom_ai_assistant_bulk",
        "wecom_media",
        "outbound_webhook",
    )


class _LanePolicyRepository:
    def __init__(self, policies: dict[str, int]) -> None:
        self._policies = policies
        self.reads: list[tuple[str, ...]] = []

    def read_lane_policies(self, lanes: tuple[str, ...]) -> tuple[LanePolicy, ...]:
        self.reads.append(lanes)
        missing = tuple(lane for lane in lanes if lane not in self._policies)
        if missing:
            raise RuntimeError("queue runtime lane policy is missing: " + ", ".join(missing))
        return tuple(
            LanePolicy(
                lane=lane,
                max_in_flight=self._policies[lane],
                enabled=True,
                rollout_mode="canary",
                blocked_until=None,
                policy_version="policy-v1",
            )
            for lane in lanes
        )


class _PolicyRows:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict]:
        return self._rows


class _PolicyConnection:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, _sql: str, params) -> _PolicyRows:
        self.params = params
        return _PolicyRows(self._rows)


def test_repository_reads_lane_policies_in_requested_order() -> None:
    connection = _PolicyConnection(
        [
            {
                "lane": "wecom_media",
                "max_in_flight": 2,
                "enabled": True,
                "rollout_mode": "canary",
                "blocked_until": None,
                "policy_version": "policy-v1",
            },
            {
                "lane": "ai_generation",
                "max_in_flight": 4,
                "enabled": True,
                "rollout_mode": "blocked",
                "blocked_until": None,
                "policy_version": "policy-v1",
            },
        ]
    )
    repository = ExecutionRuntimeRepository(
        "postgresql://runtime",
        connect=lambda _url: connection,
    )

    policies = repository.read_lane_policies(("ai_generation", "wecom_media"))

    assert [policy.lane for policy in policies] == ["ai_generation", "wecom_media"]
    assert [policy.max_in_flight for policy in policies] == [4, 2]
    assert connection.params == (["ai_generation", "wecom_media"],)


def test_repository_rejects_incomplete_lane_policy_snapshot() -> None:
    connection = _PolicyConnection([])
    repository = ExecutionRuntimeRepository(
        "postgresql://runtime",
        connect=lambda _url: connection,
    )

    with pytest.raises(RuntimeError, match="wecom_media"):
        repository.read_lane_policies(("wecom_media",))


def test_service_uses_authoritative_lane_capacity_and_reuses_repository() -> None:
    repository = _LanePolicyRepository(
        {
            "ai_generation": 4,
            "wecom_welcome": 2,
            "wecom_interactive": 4,
            "wecom_bulk": 1,
            "wecom_ai_assistant_bulk": 4,
            "wecom_media": 2,
            "outbound_webhook": 4,
        }
    )
    lane_names = (
        "ai_generation",
        "wecom_welcome",
        "wecom_interactive",
        "wecom_bulk",
        "wecom_ai_assistant_bulk",
        "wecom_media",
        "outbound_webhook",
    )

    service = run_execution_runtime._service(
        queue_kind="external_effect",
        lane_names=lane_names,
        generation=1,
        handler=lambda _claim: True,
        worker_id="worker-1",
        claimless=False,
        repository=repository,
    )

    assert repository.reads == [lane_names]
    assert [lane.max_in_flight for lane in service._lanes] == [4, 2, 4, 1, 4, 2, 4]
    assert sum(lane.max_in_flight for lane in service._lanes) == 21
    assert service._repo is repository


def test_service_fails_closed_when_authoritative_lane_policy_is_missing() -> None:
    repository = _LanePolicyRepository({"wecom_media": 2})

    with pytest.raises(RuntimeError, match="missing_lane"):
        run_execution_runtime._service(
            queue_kind="external_effect",
            lane_names=("wecom_media", "missing_lane"),
            generation=1,
            handler=lambda _claim: True,
            worker_id="worker-1",
            claimless=False,
            repository=repository,
        )


def test_external_runtime_exposes_safe_token_refresh_counters(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        run_execution_runtime,
        "build_external_effect_adapter_registry",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        run_execution_runtime,
        "shared_token_provider_metrics",
        lambda: {
            "provider_count": 2,
            "refresh_started": 3,
            "refresh_succeeded": 2,
            "refresh_failed": 1,
            "cache_hits": 19,
        },
    )
    monkeypatch.setattr(
        run_execution_runtime,
        "_service",
        lambda **kwargs: captured.append(kwargs) or object(),
    )
    args = run_execution_runtime._parse_args(["--queue-kind", "external"])

    run_execution_runtime._build_services(args)

    metrics = captured[0]["runtime_metrics"]("wecom_ai_assistant_bulk")
    assert metrics["wecom_api_auth_refresh"] == {
        "provider_count": 2,
        "refresh_started": 3,
        "refresh_succeeded": 2,
        "refresh_failed": 1,
        "cache_hits": 19,
    }
    assert "token_provider" not in metrics
    assert metrics["start_rate_limiter"]["target_rate_per_second"] == 2.0


def test_execute_requires_explicit_environment_gate(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_QUEUE_RUNTIME_EXECUTE", raising=False)

    with pytest.raises(SystemExit):
        run_execution_runtime._parse_args(["--queue-kind", "webhook", "--generation", "1", "--execute"])


def test_execute_rejects_generation_zero_even_with_environment_gate(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_QUEUE_RUNTIME_EXECUTE", "1")

    with pytest.raises(SystemExit):
        run_execution_runtime._parse_args(
            ["--queue-kind", "webhook", "--generation", "0", "--execute"]
        )


def test_external_execute_requires_test_only_or_reviewed_canary_marker(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_QUEUE_RUNTIME_EXECUTE", "1")
    monkeypatch.delenv("AICRM_QUEUE_RUNTIME_ALLOWLISTED_CANARY", raising=False)

    with pytest.raises(SystemExit):
        run_execution_runtime._parse_args(["--queue-kind", "external", "--generation", "1", "--execute"])

    args = run_execution_runtime._parse_args(
        [
            "--queue-kind",
            "external",
            "--generation",
            "1",
            "--execute",
            "--test-only",
        ]
    )
    assert args.test_only is True

    monkeypatch.setenv("AICRM_QUEUE_RUNTIME_ALLOWLISTED_CANARY", "1")
    canary = run_execution_runtime._parse_args(
        ["--queue-kind", "external", "--generation", "1", "--execute"]
    )
    assert canary.test_only is False

    with pytest.raises(SystemExit):
        run_execution_runtime._parse_args(
            [
                "--queue-kind",
                "external",
                "--generation",
                "1",
                "--execute",
                "--test-only",
            ]
        )


def test_external_execute_accepts_only_exclusive_all_scope_marker(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_QUEUE_RUNTIME_EXECUTE", "1")
    monkeypatch.delenv("AICRM_QUEUE_RUNTIME_ALLOWLISTED_CANARY", raising=False)
    monkeypatch.setenv("AICRM_QUEUE_RUNTIME_ALL_SCOPE", "1")

    args = run_execution_runtime._parse_args(
        ["--queue-kind", "external", "--generation", "1", "--execute"]
    )

    assert args.execute is True
    assert args.test_only is False

    monkeypatch.setenv("AICRM_QUEUE_RUNTIME_ALLOWLISTED_CANARY", "1")
    with pytest.raises(SystemExit):
        run_execution_runtime._parse_args(
            ["--queue-kind", "external", "--generation", "1", "--execute"]
        )


def test_graceful_shutdown_does_not_turn_systemd_stop_into_process_failure() -> None:
    payload = run_execution_runtime._graceful_shutdown_payload(
        {
            "ok": False,
            "queue_kind": "internal+webhook",
            "errors": ["OperationalError"],
            "real_external_call_executed": False,
        },
        shutdown_requested=True,
    )

    assert payload["ok"] is True
    assert payload["shutdown_requested"] is True
    assert payload["shutdown_errors"] == ["OperationalError"]
    assert payload["errors"] == []


def test_runtime_failure_remains_reportable_without_shutdown_signal() -> None:
    original = {"ok": False, "errors": ["OperationalError"]}

    assert run_execution_runtime._graceful_shutdown_payload(
        original,
        shutdown_requested=False,
    ) is original
