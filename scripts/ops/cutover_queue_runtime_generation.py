#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Sequence

try:
    from scripts.script_runtime import ensure_repo_root_on_path
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.script_runtime import ensure_repo_root_on_path

ensure_repo_root_on_path()

from aicrm_next.platform_foundation.execution_runtime.cutover import (
    CANONICAL_RUNTIME_SERVICES,
    PR3_LEGACY_PERSISTENT_SERVICES,
    PR3_LEGACY_TIMER_OWNERS,
    PR3_OWNER_INVENTORY_NAME,
    PR3_REPLACEMENT_TIMER_OWNERS,
    QueueRuntimeCutoverCoordinator,
    QueueRuntimeCutoverRequest,
    RuntimeGenerationRepository,
)


RUNTIME_GENERATION_ENV = Path("/home/ubuntu/.aicrm-queue-runtime-generation.env")
AUTHORIZATION_ENV = "AICRM_QUEUE_CUTOVER_AUTHORIZED"


def _run(command: Sequence[str], *, check: bool = True, capture_output: bool = False):
    return subprocess.run(
        [str(item) for item in command],
        check=check,
        capture_output=capture_output,
        text=True,
    )


class SystemdQueueRuntimeLifecycle:
    """Operate only the three canonical PR-2 services and explicit old owners."""

    def stage_target_generation(self, generation: int) -> None:
        self.write_generation_marker(generation=int(generation), committed=False, test_only=True)

    @staticmethod
    def write_generation_marker(*, generation: int, committed: bool, test_only: bool) -> None:
        test_flag = 1 if test_only else 0
        allowlisted_flag = 0 if test_only else 1
        payload = (
            f"AICRM_QUEUE_WORKER_GENERATION={int(generation)}\n"
            "AICRM_QUEUE_RUNTIME_EXECUTE=1\n"
            f"AICRM_QUEUE_RUNTIME_TEST_ONLY={test_flag}\n"
            f"AICRM_QUEUE_RUNTIME_ALLOWLISTED_CANARY={allowlisted_flag}\n"
            f"AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY={test_flag}\n"
            f"AICRM_QUEUE_CUTOVER_COMMITTED={1 if committed else 0}\n"
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(payload)
            staged = Path(handle.name)
        try:
            _run(
                (
                    "sudo",
                    "install",
                    "-o",
                    "ubuntu",
                    "-g",
                    "ubuntu",
                    "-m",
                    "0600",
                    str(staged),
                    str(RUNTIME_GENERATION_ENV),
                )
            )
        finally:
            staged.unlink(missing_ok=True)

    def start_target_service(self, service: str) -> None:
        if service not in CANONICAL_RUNTIME_SERVICES:
            raise ValueError(f"non-canonical target runtime service: {service}")
        _run(("sudo", "systemctl", "enable", service))
        _run(("sudo", "systemctl", "restart", service))
        _run(("sudo", "systemctl", "is-active", "--quiet", service))

    def stop_legacy_triggers(self, units: Sequence[str]) -> None:
        for unit in units:
            normalized = str(unit or "").strip()
            if not normalized.endswith(".timer"):
                raise ValueError(f"legacy trigger must be a systemd timer: {normalized}")
            _run(("sudo", "systemctl", "stop", normalized))

    def stop_legacy_services(self, units: Sequence[str]) -> None:
        for unit in units:
            normalized = str(unit or "").strip()
            if not normalized.endswith(".service"):
                raise ValueError(f"legacy owner must be a systemd service: {normalized}")
            _run(("sudo", "systemctl", "stop", normalized))

    def wait_legacy_services_drained(self, units: Sequence[str], timeout_seconds: int) -> None:
        pending = {str(unit or "").strip() for unit in units}
        if any(not unit.endswith(".service") for unit in pending):
            raise ValueError("legacy drain targets must be systemd services")
        deadline = time.monotonic() + max(1, int(timeout_seconds or 600))
        while pending:
            inactive = set()
            for unit in pending:
                result = _run(
                    ("sudo", "systemctl", "is-active", unit),
                    check=False,
                    capture_output=True,
                )
                state = str(result.stdout or "").strip().lower()
                if result.returncode == 4 or state == "unknown":
                    raise RuntimeError(f"legacy queue owner unit does not exist: {unit}")
                if state in {"inactive", "failed"}:
                    inactive.add(unit)
            pending -= inactive
            if not pending:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(f"legacy queue owners did not drain: {sorted(pending)}")
            time.sleep(1)

    def retire_legacy_units(self, units: Sequence[str]) -> None:
        for unit in units:
            normalized = str(unit or "").strip()
            _run(("sudo", "systemctl", "disable", normalized))
            enabled = _run(
                ("sudo", "systemctl", "is-enabled", normalized),
                check=False,
                capture_output=True,
            )
            if str(enabled.stdout or "").strip().lower() == "enabled":
                raise RuntimeError(f"legacy queue owner remained enabled: {normalized}")
            _run(("sudo", "systemctl", "reset-failed", normalized), check=False)

    def verify_single_owner(
        self,
        *,
        legacy_triggers: Sequence[str],
        legacy_services: Sequence[str],
        legacy_persistent_services: Sequence[str],
        replacement_active: bool = False,
    ) -> None:
        for service in CANONICAL_RUNTIME_SERVICES:
            _run(("sudo", "systemctl", "is-active", "--quiet", service))
        for unit in tuple(dict.fromkeys((*legacy_triggers, *legacy_services, *legacy_persistent_services))):
            state = _run(
                ("sudo", "systemctl", "is-active", unit),
                check=False,
                capture_output=True,
            )
            normalized = str(state.stdout or "").strip().lower()
            if state.returncode == 4 or normalized in {"", "unknown"}:
                raise RuntimeError(f"legacy queue owner unit does not exist: {unit}")
            if state.returncode == 0 or normalized != "inactive":
                raise RuntimeError(f"legacy queue owner remained active: {unit}: {normalized}")
        for unit in tuple(dict.fromkeys((*legacy_triggers, *legacy_persistent_services))):
            enabled = _run(
                ("sudo", "systemctl", "is-enabled", unit),
                check=False,
                capture_output=True,
            )
            normalized = str(enabled.stdout or "").strip().lower()
            if normalized not in {"disabled", "masked"}:
                raise RuntimeError(f"legacy queue owner remained enabled: {unit}: {normalized or 'unknown'}")
        for timer, service in PR3_REPLACEMENT_TIMER_OWNERS:
            timer_active = _run(
                ("sudo", "systemctl", "is-active", timer),
                check=False,
                capture_output=True,
            )
            timer_enabled = _run(
                ("sudo", "systemctl", "is-enabled", timer),
                check=False,
                capture_output=True,
            )
            service_active = _run(
                ("sudo", "systemctl", "is-active", service),
                check=False,
                capture_output=True,
            )
            if replacement_active:
                if timer_active.returncode != 0 or timer_enabled.returncode != 0:
                    raise RuntimeError(f"post-cutover replacement timer is not active and enabled: {timer}")
            else:
                enabled_state = str(timer_enabled.stdout or "").strip().lower()
                if timer_active.returncode == 0 or enabled_state not in {"disabled", "masked"}:
                    raise RuntimeError(f"pre-cutover replacement timer is not disabled: {timer}")
            if service_active.returncode == 0:
                raise RuntimeError(f"cutover replacement oneshot service remained active: {service}")

    def activate_post_cutover_replacements(self, generation: int) -> None:
        self.write_generation_marker(generation=int(generation), committed=True, test_only=True)
        for timer, _service in PR3_REPLACEMENT_TIMER_OWNERS:
            _run(("sudo", "systemctl", "enable", timer))
            _run(("sudo", "systemctl", "restart", timer))
            _run(("sudo", "systemctl", "is-active", "--quiet", timer))

    def deactivate_post_cutover_replacements(self, generation: int) -> None:
        for timer, service in PR3_REPLACEMENT_TIMER_OWNERS:
            _run(("sudo", "systemctl", "disable", "--now", timer), check=False)
            _run(("sudo", "systemctl", "stop", service), check=False)
            _run(("sudo", "systemctl", "reset-failed", timer), check=False)
            _run(("sudo", "systemctl", "reset-failed", service), check=False)
        self.write_generation_marker(generation=int(generation), committed=False, test_only=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Atomically cut queue ownership to a numeric PostgreSQL generation.",
    )
    parser.add_argument("--expected-generation", type=int, required=True)
    parser.add_argument("--target-generation", type=int, required=True)
    parser.add_argument("--expected-policy-version", required=True)
    parser.add_argument("--lane", action="append", default=[], required=True)
    parser.add_argument(
        "--owner-inventory",
        required=True,
        choices=(PR3_OWNER_INVENTORY_NAME,),
        help="Use the reviewed, complete PR-3 old-owner inventory; manual subsets are forbidden.",
    )
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--readiness-timeout-seconds", type=int, default=60)
    parser.add_argument("--drain-timeout-seconds", type=int, default=600)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", default=False)
    action.add_argument("--verify-owner-state", action="store_true", default=False)
    parser.add_argument("--confirmation", default="")
    return parser.parse_args(argv)


def _request(args: argparse.Namespace) -> QueueRuntimeCutoverRequest:
    if str(args.owner_inventory) != PR3_OWNER_INVENTORY_NAME:
        raise ValueError("only the reviewed PR-3 owner inventory is supported")
    pairs = PR3_LEGACY_TIMER_OWNERS
    return QueueRuntimeCutoverRequest(
        expected_generation=int(args.expected_generation),
        target_generation=int(args.target_generation),
        expected_policy_version=str(args.expected_policy_version),
        lanes=tuple(str(lane) for lane in args.lane),
        actor=str(args.actor),
        reason=str(args.reason),
        legacy_triggers=tuple(timer for timer, _service in pairs),
        legacy_services=tuple(service for _timer, service in pairs),
        legacy_persistent_services=PR3_LEGACY_PERSISTENT_SERVICES,
        readiness_timeout_seconds=int(args.readiness_timeout_seconds),
        drain_timeout_seconds=int(args.drain_timeout_seconds),
    )


def _plan(request: QueueRuntimeCutoverRequest, *, owner_inventory: str) -> dict[str, object]:
    return {
        "ok": True,
        "applied": False,
        "expected_generation": request.expected_generation,
        "target_generation": request.target_generation,
        "policy_version": request.expected_policy_version,
        "lanes": list(request.lanes),
        "target_services": list(CANONICAL_RUNTIME_SERVICES),
        "post_cutover_replacement_timers": [timer for timer, _service in PR3_REPLACEMENT_TIMER_OWNERS],
        "owner_inventory": owner_inventory,
        "legacy_triggers": list(request.legacy_triggers),
        "legacy_services": list(request.legacy_services),
        "legacy_persistent_services": list(request.legacy_persistent_services),
        "claim_gate_change": "not_applied",
        "real_external_call_executed": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    request = _request(args)
    if not args.apply and not args.verify_owner_state:
        print(
            json.dumps(
                _plan(request, owner_inventory=str(args.owner_inventory)),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    repository = RuntimeGenerationRepository()
    lifecycle = SystemdQueueRuntimeLifecycle()
    if args.verify_owner_state:
        state = repository.read_state()
        if (
            state.active_generation != request.target_generation
            or not state.claim_enabled
            or state.policy_version != request.expected_policy_version
        ):
            raise RuntimeError("database generation is not the requested active owner")
        lifecycle.verify_single_owner(
            legacy_triggers=request.legacy_triggers,
            legacy_services=request.legacy_services,
            legacy_persistent_services=request.legacy_persistent_services,
            replacement_active=True,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "applied": False,
                    "verified": True,
                    "owner_inventory": str(args.owner_inventory),
                    "active_generation": state.active_generation,
                    "claim_enabled": state.claim_enabled,
                    "rollout_mode": state.rollout_mode,
                    "external_claim_scope": state.external_claim_scope,
                    "real_external_call_executed": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    confirmation = f"ACTIVATE_QUEUE_GENERATION_{request.target_generation}"
    if str(os.getenv(AUTHORIZATION_ENV) or "").strip() != "1":
        raise RuntimeError(f"{AUTHORIZATION_ENV}=1 is required for generation activation")
    if str(args.confirmation or "").strip() != confirmation:
        raise RuntimeError(f"--confirmation must equal {confirmation}")
    activation = QueueRuntimeCutoverCoordinator(
        repository=repository,
        lifecycle=lifecycle,
    ).activate(request)
    print(
        json.dumps(
            {
                "ok": True,
                "applied": True,
                "before_generation": activation.before.active_generation,
                "active_generation": activation.after.active_generation,
                "claim_enabled": activation.after.claim_enabled,
                "rollout_mode": activation.after.rollout_mode,
                "external_claim_scope": activation.after.external_claim_scope,
                "activated_lanes": list(activation.activated_lanes),
                "owner_inventory": str(args.owner_inventory),
                "freeze_revision": activation.freeze.freeze_revision if activation.freeze else "",
                "freeze_cutoff_at": activation.freeze.cutoff_at if activation.freeze else None,
                "frozen_counts": dict(activation.freeze.counts) if activation.freeze else {},
                "real_external_call_executed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
