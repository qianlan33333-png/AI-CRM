from __future__ import annotations

import json
from pathlib import Path
import subprocess
from subprocess import CompletedProcess
import sys

import pytest

from scripts.ops.run_release_customer_read_model_refresh import (
    REFRESH_CONSUMER,
    REFRESH_EVENT_TYPE,
    ReleaseRefreshError,
    main,
    run_release_refresh,
)


ROOT = Path(__file__).resolve().parents[1]


def test_release_refresh_script_bootstraps_from_non_repository_working_directory(tmp_path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "ops" / "run_release_customer_read_model_refresh.py"),
            "--help",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--release-sha" in completed.stdout
    assert "--attempt-key" in completed.stdout


def test_release_refresh_runs_only_the_exact_internal_projection_consumer(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_CUSTOMER_READ_MODEL_RELEASE_REFRESH_AUTHORIZED", "1")
    calls: list[dict] = []

    def runner(command, **kwargs):
        calls.append({"command": list(command), **kwargs})
        if "--wait-seconds" in command:
            payload = {
                "ok": True,
                "wait": {
                    "ok": True,
                    "target_generation": 26,
                    "dirty_generation": 26,
                    "completed_generation": 26,
                    "status": "idle",
                },
                "real_external_call_executed": False,
            }
        elif "scripts/run_customer_read_model_refresh.py" in command:
            payload = {
                "ok": True,
                "accepted": True,
                "deduplicated": False,
                "generation": 26,
                "intent": {
                    "dirty_generation": 26,
                    "completed_generation": 25,
                    "status": "waiting",
                },
                "real_external_call_executed": False,
            }
        else:
            payload = {
                "ok": True,
                "outbox_relay": {
                    "targeted": True,
                    "event_type": REFRESH_EVENT_TYPE,
                    "counts": {
                        "candidate_count": 1,
                        "relayed_count": 1,
                        "failed_retryable_count": 0,
                        "failed_terminal_count": 0,
                        "lost_lease_count": 0,
                        "unhandled_failure_count": 0,
                    },
                },
                "counts": {
                    "candidate_count": 1,
                    "processed_count": 1,
                    "succeeded_count": 1,
                    "failed_retryable_count": 0,
                    "failed_terminal_count": 0,
                    "blocked_count": 0,
                    "lost_lease_count": 0,
                    "unhandled_failure_count": 0,
                },
                "real_external_call_executed": False,
            }
        return CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    result = run_release_refresh(
        release_sha="a" * 40,
        attempt_key="123456-2",
        command_runner=runner,
    )

    assert result["ok"] is True
    assert result["real_external_call_executed"] is False
    assert result["completion"] == {
        "target_generation": 26,
        "completed_generation": 26,
        "status": "idle",
    }
    assert result["consumer_mode"] == "executed"
    assert result["targeted_relay_mode"] == "relayed"
    assert len(calls) == 3
    assert calls[0]["command"][-2:] == [
        "--source-key",
        f"deploy_runtime:{'a' * 40}:123456-2",
    ]
    assert calls[2]["command"][-4:] == [
        "--source-key",
        f"deploy_runtime:{'a' * 40}:123456-2",
        "--wait-seconds",
        "300",
    ]
    worker_call = calls[1]
    assert worker_call["env"]["AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES"] == REFRESH_EVENT_TYPE
    assert worker_call["env"]["AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS"] == (
        f"{REFRESH_EVENT_TYPE}:{REFRESH_CONSUMER}"
    )
    assert worker_call["env"]["AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS"] == ""
    assert worker_call["env"]["AICRM_INTERNAL_EVENTS_AUTO_EXECUTE"] == "1"
    assert worker_call["env"]["AICRM_INTERNAL_EVENTS_SHADOW_ONLY"] == "0"
    assert "--release-customer-read-model-refresh" in worker_call["command"]
    assert worker_call["command"][-6:] == [
        "--event-types",
        REFRESH_EVENT_TYPE,
        "--consumer-names",
        REFRESH_CONSUMER,
        "--outbox-idempotency-key",
        "customer_read_model.refresh.requested:26",
    ]


def test_release_refresh_fails_closed_on_non_exact_consumer_result(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_CUSTOMER_READ_MODEL_RELEASE_REFRESH_AUTHORIZED", "1")
    call_count = 0

    def runner(command, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            payload = {
                "ok": True,
                "accepted": True,
                "generation": 1,
                "intent": {
                    "dirty_generation": 1,
                    "completed_generation": 0,
                    "status": "waiting",
                },
                "real_external_call_executed": False,
            }
        else:
            payload = {
                "ok": True,
                "outbox_relay": {
                    "targeted": True,
                    "event_type": REFRESH_EVENT_TYPE,
                    "counts": {"candidate_count": 1, "relayed_count": 1},
                },
                "counts": {"candidate_count": 1, "processed_count": 0, "succeeded_count": 0},
                "real_external_call_executed": False,
            }
        return CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    with pytest.raises(ReleaseRefreshError, match="release_refresh_consumer_count_mismatch") as exc_info:
        run_release_refresh(release_sha="b" * 40, command_runner=runner)

    assert exc_info.value.diagnostics == {
        "consumer_counts": {
            "candidate_count": 1,
            "processed_count": 0,
            "succeeded_count": 0,
            "failed_retryable_count": 0,
            "failed_terminal_count": 0,
            "blocked_count": 0,
            "lost_lease_count": 0,
            "unhandled_failure_count": 0,
            "skipped_count": 0,
        },
        "expected_consumer_counts": {
            "candidate_count": 1,
            "processed_count": 1,
            "succeeded_count": 1,
            "failed_retryable_count": 0,
            "failed_terminal_count": 0,
            "blocked_count": 0,
            "lost_lease_count": 0,
            "unhandled_failure_count": 0,
        },
        "consumer_mode": "rejected",
    }


def test_release_refresh_main_prints_only_safe_failure_diagnostics(monkeypatch, capsys) -> None:
    def fail(**_kwargs):
        raise ReleaseRefreshError(
            "release_refresh_consumer_count_mismatch",
            diagnostics={
                "consumer_counts": {"candidate_count": 0, "processed_count": 0},
                "consumer_mode": "rejected",
            },
        )

    monkeypatch.setattr(
        "scripts.ops.run_release_customer_read_model_refresh.run_release_refresh",
        fail,
    )

    assert main(["--release-sha", "f" * 40]) == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "ok": False,
        "reason": "release_refresh_consumer_count_mismatch",
        "real_external_call_executed": False,
        "target_values_redacted": True,
        "diagnostics": {
            "consumer_counts": {"candidate_count": 0, "processed_count": 0},
            "consumer_mode": "rejected",
        },
    }


def test_release_refresh_waits_for_a_concurrent_consumer_owner(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_CUSTOMER_READ_MODEL_RELEASE_REFRESH_AUTHORIZED", "1")
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        del kwargs
        calls.append(list(command))
        if "--wait-seconds" in command:
            payload = {
                "ok": True,
                "wait": {
                    "ok": True,
                    "target_generation": 31,
                    "dirty_generation": 31,
                    "completed_generation": 31,
                    "status": "idle",
                },
                "real_external_call_executed": False,
            }
        elif "scripts/run_customer_read_model_refresh.py" in command:
            payload = {
                "ok": True,
                "accepted": True,
                "deduplicated": False,
                "generation": 31,
                "intent": {
                    "dirty_generation": 31,
                    "completed_generation": 30,
                    "status": "waiting",
                },
                "real_external_call_executed": False,
            }
        else:
            payload = {
                "ok": True,
                "outbox_relay": {
                    "targeted": True,
                    "event_type": REFRESH_EVENT_TYPE,
                    "counts": {
                        "candidate_count": 0,
                        "relayed_count": 0,
                        "failed_retryable_count": 0,
                        "failed_terminal_count": 0,
                        "lost_lease_count": 0,
                        "unhandled_failure_count": 0,
                    },
                },
                "counts": {
                    "candidate_count": 0,
                    "processed_count": 0,
                    "succeeded_count": 0,
                    "failed_retryable_count": 0,
                    "failed_terminal_count": 0,
                    "blocked_count": 0,
                    "lost_lease_count": 0,
                    "unhandled_failure_count": 0,
                },
                "real_external_call_executed": False,
            }
        return CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    result = run_release_refresh(release_sha="e" * 40, command_runner=runner)

    assert result["ok"] is True
    assert result["consumer_mode"] == "concurrent_handoff"
    assert result["targeted_relay_mode"] == "already_owned"
    assert result["consumer_counts"] == {
        "candidate_count": 0,
        "processed_count": 0,
        "succeeded_count": 0,
        "failed_retryable_count": 0,
        "failed_terminal_count": 0,
        "blocked_count": 0,
        "lost_lease_count": 0,
        "unhandled_failure_count": 0,
    }
    assert calls[-1][-2:] == ["--wait-seconds", "300"]


def test_release_refresh_fails_closed_when_targeted_relay_advances_a_different_shape(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_CUSTOMER_READ_MODEL_RELEASE_REFRESH_AUTHORIZED", "1")
    call_count = 0

    def runner(command, **kwargs):
        del kwargs
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            payload = {
                "ok": True,
                "accepted": True,
                "generation": 42,
                "intent": {"dirty_generation": 42, "completed_generation": 41, "status": "waiting"},
                "real_external_call_executed": False,
            }
        else:
            payload = {
                "ok": True,
                "outbox_relay": {
                    "targeted": True,
                    "event_type": REFRESH_EVENT_TYPE,
                    "counts": {"candidate_count": 1, "relayed_count": 0},
                },
                "counts": {},
                "real_external_call_executed": False,
            }
        return CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    with pytest.raises(ReleaseRefreshError, match="release_refresh_targeted_relay_count_mismatch"):
        run_release_refresh(release_sha="f" * 40, command_runner=runner)


def test_release_refresh_is_idempotent_when_the_exact_release_generation_already_completed(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_CUSTOMER_READ_MODEL_RELEASE_REFRESH_AUTHORIZED", "1")
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        del kwargs
        calls.append(list(command))
        if "--wait-seconds" in command:
            payload = {
                "ok": True,
                "wait": {
                    "ok": True,
                    "target_generation": 8,
                    "dirty_generation": 8,
                    "completed_generation": 8,
                    "status": "idle",
                },
                "real_external_call_executed": False,
            }
        else:
            payload = {
                "ok": True,
                "accepted": True,
                "deduplicated": True,
                "intent": {
                    "dirty_generation": 8,
                    "completed_generation": 8,
                    "status": "idle",
                },
                "real_external_call_executed": False,
            }
        return CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    result = run_release_refresh(release_sha="d" * 40, command_runner=runner)

    assert result["request"]["already_completed"] is True
    assert result["consumer_mode"] == "already_completed"
    assert result["consumer_counts"] == {
        "candidate_count": 0,
        "processed_count": 0,
        "succeeded_count": 0,
        "failed_retryable_count": 0,
        "failed_terminal_count": 0,
        "blocked_count": 0,
        "lost_lease_count": 0,
        "unhandled_failure_count": 0,
    }
    assert len(calls) == 2
    assert all("scripts/run_internal_event_worker.py" not in command for command in calls)


def test_release_refresh_requires_explicit_deploy_authorization(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_CUSTOMER_READ_MODEL_RELEASE_REFRESH_AUTHORIZED", raising=False)

    with pytest.raises(ReleaseRefreshError, match="not_authorized"):
        run_release_refresh(release_sha="c" * 40)


def test_release_refresh_rejects_invalid_attempt_key_before_running_commands(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_CUSTOMER_READ_MODEL_RELEASE_REFRESH_AUTHORIZED", "1")
    command_called = False

    def runner(*_args, **_kwargs):
        nonlocal command_called
        command_called = True
        raise AssertionError("command must not run")

    with pytest.raises(ReleaseRefreshError, match="attempt_key_invalid"):
        run_release_refresh(
            release_sha="a" * 40,
            attempt_key="unsafe key",
            command_runner=runner,
        )

    assert command_called is False
