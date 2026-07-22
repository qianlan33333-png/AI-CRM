from __future__ import annotations

import json
from subprocess import CompletedProcess

import pytest

from scripts.ops.run_release_customer_read_model_refresh import (
    REFRESH_CONSUMER,
    REFRESH_EVENT_TYPE,
    ReleaseRefreshError,
    run_release_refresh,
)


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

    result = run_release_refresh(release_sha="a" * 40, command_runner=runner)

    assert result["ok"] is True
    assert result["real_external_call_executed"] is False
    assert result["completion"] == {
        "target_generation": 26,
        "completed_generation": 26,
        "status": "idle",
    }
    assert len(calls) == 3
    worker_call = calls[1]
    assert worker_call["env"]["AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES"] == REFRESH_EVENT_TYPE
    assert worker_call["env"]["AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS"] == (
        f"{REFRESH_EVENT_TYPE}:{REFRESH_CONSUMER}"
    )
    assert worker_call["env"]["AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS"] == ""
    assert worker_call["env"]["AICRM_INTERNAL_EVENTS_AUTO_EXECUTE"] == "1"
    assert worker_call["env"]["AICRM_INTERNAL_EVENTS_SHADOW_ONLY"] == "0"
    assert worker_call["command"][-4:] == ["--event-types", REFRESH_EVENT_TYPE, "--consumer-names", REFRESH_CONSUMER]


def test_release_refresh_fails_closed_on_empty_or_non_exact_consumer_result(monkeypatch) -> None:
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
                "counts": {"candidate_count": 0, "processed_count": 0, "succeeded_count": 0},
                "real_external_call_executed": False,
            }
        return CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    with pytest.raises(ReleaseRefreshError, match="release_refresh_consumer_count_mismatch"):
        run_release_refresh(release_sha="b" * 40, command_runner=runner)


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
