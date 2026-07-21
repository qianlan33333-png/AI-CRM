from __future__ import annotations

import pytest

from scripts import run_execution_runtime


def test_runtime_defaults_to_claimless_standby(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_QUEUE_RUNTIME_EXECUTE", raising=False)
    monkeypatch.delenv("AICRM_QUEUE_WORKER_GENERATION", raising=False)

    args = run_execution_runtime._parse_args(["--queue-kind", "internal"])

    assert args.execute is False
    assert args.generation == 0


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
        lambda: object(),
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
