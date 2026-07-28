from __future__ import annotations

import pytest

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
    assert run_execution_runtime.DEFAULT_LANE_CAPACITY["wecom_ai_assistant_bulk"] == 24
    assert run_execution_runtime.DEFAULT_LANE_CAPACITY["ai_generation"] == 64


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
