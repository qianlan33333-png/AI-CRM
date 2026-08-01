#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import threading
from typing import Any

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json, read_int_env
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json, read_int_env

ensure_repo_root_on_path()

from aicrm_next.channel_entry_composition import build_wecom_callback_inbox_worker_factory
from aicrm_next.channels.channel_entry.realtime_contract import WECOM_WELCOME_FALLBACK_SECONDS
from aicrm_next.deployment_profile import DeploymentProfile, deployment_profile_from_environment
from aicrm_next.external_effect_composition import (
    build_external_effect_adapter_registry,
    run_welcome_realtime_post_commit,
)
from aicrm_next.internal_event_composition import build_internal_event_consumer_registry
from aicrm_next.platform.platform_foundation.execution_runtime.handlers import (
    external_effect_handler,
    internal_event_handler,
    internal_outbox_handler,
    webhook_inbox_handler,
)
from aicrm_next.platform.platform_foundation.execution_runtime.lanes import (
    QueueLane,
)
from aicrm_next.platform.platform_foundation.execution_runtime.repository import (
    ExecutionRuntimeRepository,
    LanePolicy,
)
from aicrm_next.platform.platform_foundation.execution_runtime.service import QueueRuntimeService
from aicrm_next.platform.platform_foundation.execution_runtime.start_rate import SharedStartRateLimiter
from aicrm_next.platform.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.platform.shared.wecom_runtime import shared_token_provider_metrics
from aicrm_next.platform.platform_foundation.internal_events.outbox import InternalEventOutboxRelay
from aicrm_next.platform.platform_foundation.internal_events.worker import (
    RELAY_ROLE_CONSUMER_ONLY,
    InternalEventWorker,
)


EXECUTE_ENV = "AICRM_QUEUE_RUNTIME_EXECUTE"
TEST_ONLY_ENV = "AICRM_QUEUE_RUNTIME_TEST_ONLY"
ALLOWLISTED_CANARY_ENV = "AICRM_QUEUE_RUNTIME_ALLOWLISTED_CANARY"
ALL_SCOPE_ENV = "AICRM_QUEUE_RUNTIME_ALL_SCOPE"


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the PostgreSQL LISTEN/NOTIFY execution runtime in standby or execute mode.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--queue-kind",
        choices=("external", "internal", "webhook"),
    )
    target.add_argument(
        "--role",
        choices=("internal_worker", "external_worker"),
        help="Run the stable deployment role; internal_worker combines inbox and internal-event lanes.",
    )
    parser.add_argument(
        "--generation",
        type=int,
        default=read_int_env("AICRM_QUEUE_WORKER_GENERATION", 0),
    )
    execution_mode = parser.add_mutually_exclusive_group()
    execution_mode.add_argument(
        "--execute",
        dest="execute",
        action="store_true",
    )
    execution_mode.add_argument(
        "--standby",
        dest="execute",
        action="store_false",
        help="Force claimless standby even when the shared generation environment is armed.",
    )
    parser.set_defaults(execute=None)
    parser.add_argument(
        "--test-only",
        action="store_true",
        default=_truthy(os.getenv(TEST_ONLY_ENV, "")),
    )
    args = parser.parse_args(argv)
    if args.execute is None:
        args.execute = _truthy(os.getenv(EXECUTE_ENV, ""))
    if args.generation < 0:
        parser.error("--generation must be >= 0")
    if args.execute and args.generation <= 0:
        parser.error("--execute requires --generation > 0")
    if args.execute and not _truthy(os.getenv(EXECUTE_ENV, "")):
        parser.error(f"--execute requires {EXECUTE_ENV}=1")
    external_selected = "external" in _selected_queue_kinds(args)
    if (
        external_selected
        and args.execute
        and not args.test_only
        and not _truthy(os.getenv(ALLOWLISTED_CANARY_ENV, ""))
        and not _truthy(os.getenv(ALL_SCOPE_ENV, ""))
    ):
        parser.error(
            "external execute requires a reviewed allowlisted or all-scope generation marker"
        )
    if external_selected and args.test_only and _truthy(
        os.getenv(ALLOWLISTED_CANARY_ENV, "")
    ):
        parser.error("external runtime cannot be both test-only and allowlisted canary")
    if external_selected and args.test_only and _truthy(os.getenv(ALL_SCOPE_ENV, "")):
        parser.error("external runtime cannot be both test-only and all-scope")
    if _truthy(os.getenv(ALLOWLISTED_CANARY_ENV, "")) and _truthy(os.getenv(ALL_SCOPE_ENV, "")):
        parser.error("external runtime cannot be both allowlisted canary and all-scope")
    return args


def _selected_queue_kinds(args: argparse.Namespace) -> tuple[str, ...]:
    if getattr(args, "role", None) == "internal_worker":
        return ("internal", "webhook")
    if getattr(args, "role", None) == "external_worker":
        return ("external",)
    return (str(args.queue_kind),)


def _worker_identity(args: argparse.Namespace, *, queue_kind: str) -> str:
    hostname = socket.gethostname()
    role = str(getattr(args, "role", None) or "").strip()
    if role:
        return f"{hostname}:role:{role}:{queue_kind}"
    return f"{hostname}:{queue_kind}"


def _install_signal_handlers(
    stop_event: threading.Event,
    shutdown_requested: threading.Event | None = None,
) -> None:
    def stop(_signum, _frame) -> None:
        if shutdown_requested is not None:
            shutdown_requested.set()
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)


def _graceful_shutdown_payload(payload: dict[str, Any], *, shutdown_requested: bool) -> dict[str, Any]:
    if not shutdown_requested:
        return payload
    return {
        **payload,
        "ok": True,
        "shutdown_requested": True,
        "shutdown_errors": list(payload.get("errors") or []),
        "errors": [],
    }


def _lane(policy: LanePolicy, *, claimless: bool) -> QueueLane:
    return QueueLane(
        name=policy.lane,
        max_in_flight=policy.max_in_flight,
        enabled=policy.enabled,
        rollout_mode="standby" if claimless else policy.rollout_mode,
        fallback_seconds=(
            WECOM_WELCOME_FALLBACK_SECONDS
            if policy.lane in {"wecom_welcome_ingress", "wecom_welcome"}
            else None
        ),
    )


def _service(
    *,
    queue_kind: str,
    lane_names: tuple[str, ...],
    generation: int,
    handler,
    worker_id: str,
    claimless: bool,
    test_only: bool = False,
    runtime_metrics=None,
    repository: ExecutionRuntimeRepository | None = None,
) -> QueueRuntimeService:
    runtime_repository = repository or ExecutionRuntimeRepository()
    policies = runtime_repository.read_lane_policies(lane_names)
    return QueueRuntimeService(
        queue_kind=queue_kind,
        lanes=tuple(_lane(policy, claimless=claimless) for policy in policies),
        generation=generation,
        handler=handler,
        repository=runtime_repository,
        service_name=f"aicrm-{queue_kind}-runtime",
        worker_id=worker_id,
        lease_seconds=30,
        heartbeat_seconds=10,
        fallback_seconds=30,
        test_only=test_only,
        claimless=claimless,
        runtime_metrics=runtime_metrics,
    )


def _build_services(args: argparse.Namespace) -> tuple[QueueRuntimeService, ...]:
    services: list[QueueRuntimeService] = []
    profile = deployment_profile_from_environment()
    for queue_kind in _selected_queue_kinds(args):
        services.extend(_build_queue_services(args, queue_kind=queue_kind, profile=profile))
    return tuple(services)


def _build_queue_services(
    args: argparse.Namespace,
    *,
    queue_kind: str,
    profile: DeploymentProfile,
) -> tuple[QueueRuntimeService, ...]:
    claimless = not bool(args.execute)
    worker_id = _worker_identity(args, queue_kind=queue_kind)
    if queue_kind == "external":
        start_rate_limiter = SharedStartRateLimiter(
            target_rate_per_second=2.0,
            burst=2,
        )
        worker = ExternalEffectWorker(
            adapter_registry=build_external_effect_adapter_registry(profile),
            locked_by=worker_id,
            lease_seconds=30,
            critical_post_commit_hook=run_welcome_realtime_post_commit,
            deployment_profile=profile,
        )

        def external_runtime_metrics(lane_name: str) -> dict[str, Any]:
            if lane_name != "wecom_ai_assistant_bulk":
                return {}
            return {
                # Keep the public metric key free of secret-bearing vocabulary.
                # The runtime read model intentionally redacts keys containing
                # "token", even when their values are aggregate counters.
                "wecom_api_auth_refresh": shared_token_provider_metrics(),
                "start_rate_limiter": start_rate_limiter.aggregate_snapshot(),
            }

        return (
            _service(
                queue_kind="external_effect",
                lane_names=(
                    "ai_generation",
                    "wecom_welcome",
                    "wecom_interactive",
                    "wecom_bulk",
                    "wecom_ai_assistant_bulk",
                    "wecom_media",
                    "outbound_webhook",
                ),
                generation=args.generation,
                handler=external_effect_handler(
                    worker,
                    start_rate_limiter=start_rate_limiter,
                ),
                worker_id=worker_id,
                claimless=claimless,
                test_only=bool(args.test_only),
                runtime_metrics=external_runtime_metrics,
            ),
        )
    if queue_kind == "webhook":
        worker = build_wecom_callback_inbox_worker_factory(
            external_effect_adapter_registry=build_external_effect_adapter_registry(profile),
        )()
        return (
            _service(
                queue_kind="webhook_inbox",
                lane_names=("wecom_welcome_ingress", "webhook_inbox"),
                generation=args.generation,
                handler=webhook_inbox_handler(worker),
                worker_id=worker_id,
                claimless=claimless,
            ),
        )
    registry = build_internal_event_consumer_registry(profile)
    consumer_worker = InternalEventWorker(
        consumer_registry=registry,
        locked_by=worker_id,
        relay_role=RELAY_ROLE_CONSUMER_ONLY,
    )
    outbox_relay = InternalEventOutboxRelay(
        consumer_registry=registry,
        locked_by=f"{worker_id}-outbox",
    )
    lanes = ("internal_general", "internal_financial")
    return (
        _service(
            queue_kind="internal_event",
            lane_names=lanes,
            generation=args.generation,
            handler=internal_event_handler(consumer_worker),
            worker_id=worker_id,
            claimless=claimless,
        ),
        _service(
            queue_kind="internal_outbox",
            lane_names=lanes,
            generation=args.generation,
            handler=internal_outbox_handler(outbox_relay),
            worker_id=f"{worker_id}-outbox",
            claimless=claimless,
        ),
    )


def run(args: argparse.Namespace, *, stop_event: threading.Event) -> dict[str, Any]:
    services = _build_services(args)
    results: list[Any] = []
    errors: list[str] = []
    lock = threading.Lock()

    def run_service(service: QueueRuntimeService) -> None:
        try:
            result = service.run(stop_event=stop_event)
            with lock:
                results.append(result)
        except Exception as exc:
            with lock:
                errors.append(exc.__class__.__name__)
            stop_event.set()

    threads = [threading.Thread(target=run_service, args=(service,), daemon=True) for service in services]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return {
        "ok": not errors and all(result.ok for result in results),
        "queue_kind": "+".join(_selected_queue_kinds(args)),
        "runtime_role": str(getattr(args, "role", None) or "legacy_queue_kind"),
        "generation": int(args.generation),
        "mode": "execute" if args.execute else "standby_claimless",
        "test_only": bool(args.test_only),
        "services": [
            {
                "queue_kind": result.queue_kind,
                "ok": result.ok,
                "lane_results": result.lane_results,
            }
            for result in results
        ],
        "errors": errors,
        "real_external_call_executed": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    stop_event = threading.Event()
    shutdown_requested = threading.Event()
    _install_signal_handlers(stop_event, shutdown_requested)
    try:
        payload = run(args, stop_event=stop_event)
    except Exception as exc:
        payload = {
            "ok": False,
            "queue_kind": "+".join(_selected_queue_kinds(args)),
            "runtime_role": str(getattr(args, "role", None) or "legacy_queue_kind"),
            "generation": int(args.generation),
            "error": "execution_runtime_failed",
            "error_class": exc.__class__.__name__,
            "real_external_call_executed": False,
        }
    payload = _graceful_shutdown_payload(
        payload,
        shutdown_requested=shutdown_requested.is_set(),
    )
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
