#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Sequence

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()


ROOT = Path(__file__).resolve().parents[2]
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
REFRESH_EVENT_TYPE = "customer_read_model.refresh.requested"
REFRESH_CONSUMER = "customer_read_model_refresh_intent_consumer"
COMPLETION_WAIT_SECONDS = "300"
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class ReleaseRefreshError(RuntimeError):
    """Raised when the exact deploy-time projection refresh cannot complete."""

    def __init__(self, reason: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = str(reason)
        self.diagnostics = dict(diagnostics or {})


def _payload_from_stdout(stdout: str, *, label: str) -> dict[str, Any]:
    lines = [line.strip() for line in str(stdout or "").splitlines() if line.strip()]
    if not lines:
        raise ReleaseRefreshError(f"{label}_output_missing")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ReleaseRefreshError(f"{label}_output_invalid") from exc
    if not isinstance(payload, dict):
        raise ReleaseRefreshError(f"{label}_output_not_object")
    return payload


def _run_json_command(
    command: Sequence[str],
    *,
    env: dict[str, str],
    label: str,
    command_runner: CommandRunner,
) -> dict[str, Any]:
    completed = command_runner(
        list(command),
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = _payload_from_stdout(completed.stdout, label=label)
    if completed.returncode != 0 or payload.get("ok") is not True:
        raise ReleaseRefreshError(f"{label}_failed")
    if payload.get("real_external_call_executed") is not False:
        raise ReleaseRefreshError(f"{label}_external_call_boundary_violated")
    return payload


def run_release_refresh(
    *,
    release_sha: str,
    command_runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    release_sha = str(release_sha or "").strip()
    if SHA_PATTERN.fullmatch(release_sha) is None:
        raise ReleaseRefreshError("release_sha_invalid")
    if os.getenv("AICRM_CUSTOMER_READ_MODEL_RELEASE_REFRESH_AUTHORIZED", "").strip() != "1":
        raise ReleaseRefreshError("customer_read_model_release_refresh_not_authorized")

    base_env = dict(os.environ)
    source_key = f"deploy_runtime:{release_sha}"
    request_command = (
        sys.executable,
        "scripts/run_customer_read_model_refresh.py",
        "--execute",
        "--release-refresh",
        "--source-key",
        source_key,
    )
    request = _run_json_command(
        request_command,
        env=base_env,
        label="release_refresh_request",
        command_runner=command_runner,
    )
    if request.get("accepted") is not True:
        raise ReleaseRefreshError("release_refresh_request_not_accepted")
    request_intent = dict(request.get("intent") or {})
    target_generation = int(request.get("generation") or request_intent.get("dirty_generation") or 0)
    if target_generation <= 0:
        raise ReleaseRefreshError("release_refresh_generation_missing")
    target_outbox_key = f"customer_read_model.refresh.requested:{target_generation}"
    already_completed = (
        bool(request.get("deduplicated"))
        and str(request_intent.get("status") or "") == "idle"
        and int(request_intent.get("completed_generation") or 0) >= target_generation
        and int(request_intent.get("completed_generation") or 0)
        >= int(request_intent.get("dirty_generation") or 0)
    )

    expected_counts = {
        "candidate_count": 0 if already_completed else 1,
        "processed_count": 0 if already_completed else 1,
        "succeeded_count": 0 if already_completed else 1,
        "failed_retryable_count": 0,
        "failed_terminal_count": 0,
        "blocked_count": 0,
        "lost_lease_count": 0,
        "unhandled_failure_count": 0,
    }
    consumer_counts = dict(expected_counts)
    consumer_mode = "already_completed" if already_completed else ""
    targeted_relay_counts: dict[str, int] = {}
    targeted_relay_mode = "already_completed" if already_completed else ""
    if not already_completed:
        worker_env = {
            **base_env,
            "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES": REFRESH_EVENT_TYPE,
            "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS": f"{REFRESH_EVENT_TYPE}:{REFRESH_CONSUMER}",
            "AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS": "",
            "AICRM_INTERNAL_EVENTS_AUTO_EXECUTE": "1",
            "AICRM_INTERNAL_EVENTS_SHADOW_ONLY": "0",
        }
        consumer = _run_json_command(
            (
                sys.executable,
                "scripts/run_internal_event_worker.py",
                "--execute",
                "--limit",
                "1",
                "--event-types",
                REFRESH_EVENT_TYPE,
                "--consumer-names",
                REFRESH_CONSUMER,
                "--outbox-idempotency-key",
                target_outbox_key,
            ),
            env=worker_env,
            label="release_refresh_consumer",
            command_runner=command_runner,
        )
        relay = dict(consumer.get("outbox_relay") or {})
        relay_counts = dict(relay.get("counts") or {})
        targeted_relay_counts = {
            "candidate_count": int(relay_counts.get("candidate_count") or 0),
            "relayed_count": int(relay_counts.get("relayed_count") or 0),
            "failed_retryable_count": int(relay_counts.get("failed_retryable_count") or 0),
            "failed_terminal_count": int(relay_counts.get("failed_terminal_count") or 0),
            "lost_lease_count": int(relay_counts.get("lost_lease_count") or 0),
            "unhandled_failure_count": int(relay_counts.get("unhandled_failure_count") or 0),
        }
        relay_pair = (
            targeted_relay_counts["candidate_count"],
            targeted_relay_counts["relayed_count"],
        )
        relay_failures = sum(
            targeted_relay_counts[key]
            for key in (
                "failed_retryable_count",
                "failed_terminal_count",
                "lost_lease_count",
                "unhandled_failure_count",
            )
        )
        if (
            relay.get("targeted") is not True
            or str(relay.get("event_type") or "") != REFRESH_EVENT_TYPE
            or relay_pair not in {(0, 0), (1, 1)}
            or relay_failures != 0
        ):
            raise ReleaseRefreshError("release_refresh_targeted_relay_count_mismatch")
        targeted_relay_mode = "relayed" if relay_pair == (1, 1) else "already_owned"
        counts = dict(consumer.get("counts") or {})
        consumer_counts = {key: int(counts.get(key) or 0) for key in expected_counts}
        exact_execution = all(
            consumer_counts[key] == expected for key, expected in expected_counts.items()
        )
        # The durable relay or another runtime may claim the exact signal between
        # the request and this scoped worker. Zero work is safe only when the
        # generation completion check below subsequently proves that ownership.
        concurrent_handoff = all(value == 0 for value in consumer_counts.values()) and int(
            counts.get("skipped_count") or 0
        ) == 0
        if not exact_execution and not concurrent_handoff:
            raise ReleaseRefreshError(
                "release_refresh_consumer_count_mismatch",
                diagnostics={
                    "consumer_counts": {
                        **consumer_counts,
                        "skipped_count": int(counts.get("skipped_count") or 0),
                    },
                    "expected_consumer_counts": expected_counts,
                    "consumer_mode": "rejected",
                },
            )
        consumer_mode = "executed" if exact_execution else "concurrent_handoff"

    completion = _run_json_command(
        (*request_command, "--wait-seconds", COMPLETION_WAIT_SECONDS),
        env=base_env,
        label="release_refresh_completion",
        command_runner=command_runner,
    )
    wait = dict(completion.get("wait") or {})
    if (
        wait.get("ok") is not True
        or int(wait.get("target_generation") or 0) != target_generation
        or int(wait.get("completed_generation") or 0) < target_generation
        or int(wait.get("completed_generation") or 0) < int(wait.get("dirty_generation") or 0)
        or str(wait.get("status") or "") != "idle"
    ):
        raise ReleaseRefreshError(
            "release_refresh_completion_mismatch",
            diagnostics={
                "completion": {
                    "ok": bool(wait.get("ok")),
                    "target_generation": int(wait.get("target_generation") or 0),
                    "dirty_generation": int(wait.get("dirty_generation") or 0),
                    "completed_generation": int(wait.get("completed_generation") or 0),
                    "status": str(wait.get("status") or ""),
                },
                "expected_target_generation": target_generation,
                "consumer_mode": consumer_mode,
            },
        )
    return {
        "ok": True,
        "release_sha": release_sha,
        "request": {
            "accepted": bool(request.get("accepted")),
            "deduplicated": bool(request.get("deduplicated")),
            "generation": target_generation,
            "already_completed": already_completed,
        },
        "consumer_counts": consumer_counts,
        "consumer_mode": consumer_mode,
        "targeted_relay_counts": targeted_relay_counts,
        "targeted_relay_mode": targeted_relay_mode,
        "completion": {
            "target_generation": target_generation,
            "completed_generation": int(wait.get("completed_generation") or 0),
            "status": "idle",
        },
        "scoped_event_consumer": f"{REFRESH_EVENT_TYPE}:{REFRESH_CONSUMER}",
        "real_external_call_executed": False,
        "target_values_redacted": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the exact deploy-time Customer 360 refresh consumer.")
    parser.add_argument("--release-sha", required=True)
    args = parser.parse_args(argv)
    try:
        payload = run_release_refresh(release_sha=args.release_sha)
    except ReleaseRefreshError as exc:
        payload = {
            "ok": False,
            "reason": exc.reason,
            "real_external_call_executed": False,
            "target_values_redacted": True,
        }
        if exc.diagnostics:
            payload["diagnostics"] = exc.diagnostics
        print_json(payload)
        return 1
    print_json(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
