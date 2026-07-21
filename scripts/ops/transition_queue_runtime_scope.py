#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    from scripts.script_runtime import ensure_repo_root_on_path
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.script_runtime import ensure_repo_root_on_path

ensure_repo_root_on_path()

from aicrm_next.platform_foundation.execution_runtime.cutover import (  # noqa: E402
    GenerationCASConflict,
    RuntimeGenerationRepository,
)
from aicrm_next.platform_foundation.external_effects.wecom_canary_policy import (  # noqa: E402
    wecom_canary_policy_snapshot,
)
from aicrm_next.shared.wecom_runtime import load_wecom_execution_config  # noqa: E402
from scripts.ops.cutover_queue_runtime_generation import (  # noqa: E402
    CANONICAL_RUNTIME_SERVICES,
    SystemdQueueRuntimeLifecycle,
)


AUTHORIZATION_ENV = "AICRM_QUEUE_SCOPE_TRANSITION_AUTHORIZED"
EXTERNAL_RUNTIME_SERVICE = "aicrm-external-queue-runtime.service"
EXTERNAL_HEARTBEAT_NAME = "aicrm-external_effect-runtime"
EXTERNAL_TARGET_EFFECT_TYPES = frozenset(
    {
        "wecom.message.private.send",
        "wecom.welcome_message.send",
        "wecom.contact.tag.mark",
        "wecom.contact.tag.unmark",
        "wecom.profile.update",
        "wecom.external_contact.detail.fetch",
    }
)
OWNER_EFFECT_TYPES = frozenset(
    {
        "wecom.message.private.send",
        "wecom.message.group.send",
        "wecom.welcome_message.send",
        "wecom.contact.tag.mark",
        "wecom.contact.tag.unmark",
        "wecom.profile.update",
    }
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CAS transition the durable external claim scope after draining every queue lane.",
    )
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--expected-policy-version", required=True)
    parser.add_argument("--target-policy-version", required=True)
    parser.add_argument("--expected-scope", choices=("test_loopback", "allowlisted"), required=True)
    parser.add_argument("--target-scope", choices=("test_loopback", "allowlisted"), required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--drain-timeout-seconds", type=int, default=60)
    parser.add_argument("--listener-timeout-seconds", type=int, default=60)
    parser.add_argument("--apply", action="store_true", default=False)
    parser.add_argument("--confirmation", default="")
    return parser.parse_args(argv)


def _confirmation(args: argparse.Namespace) -> str:
    return (
        f"TRANSITION_QUEUE_SCOPE_{int(args.generation)}_"
        f"{str(args.expected_scope).upper()}_TO_{str(args.target_scope).upper()}"
    )


def _policy_preflight(target_scope: str) -> dict[str, object]:
    if target_scope != "allowlisted":
        return {"required": False, "ready": True}
    policy = wecom_canary_policy_snapshot()
    config = load_wecom_execution_config()
    counts = dict(policy.get("allowlist_counts") or {})
    enabled_effect_types = frozenset(config.enabled_effect_types)
    blocking_reasons = list(policy.get("blocking_reasons") or [])
    if config.execution_mode != "execute":
        blocking_reasons.append("wecom_execution_mode_not_execute")
    if not config.real_calls_enabled:
        blocking_reasons.append("wecom_provider_config_not_ready")
    if not config.enabled_effect_types:
        blocking_reasons.append("wecom_enabled_effect_types_empty")
    if enabled_effect_types & EXTERNAL_TARGET_EFFECT_TYPES and int(
        counts.get("external_userid") or 0
    ) < 1:
        blocking_reasons.append("wecom_external_target_allowlist_empty")
    if enabled_effect_types & OWNER_EFFECT_TYPES and int(counts.get("owner_userid") or 0) < 1:
        blocking_reasons.append("wecom_owner_allowlist_empty")
    if "wecom.message.group.send" in enabled_effect_types and int(
        counts.get("group_chat_id") or 0
    ) < 1:
        blocking_reasons.append("wecom_group_chat_allowlist_empty")
    if "wecom.media.upload" in enabled_effect_types and int(
        counts.get("media_target") or 0
    ) < 1:
        blocking_reasons.append("wecom_media_target_allowlist_empty")
    return {
        "required": True,
        "ready": not blocking_reasons,
        "provider_target_policy": policy.get("provider_target_policy"),
        "allowlist_counts": counts,
        "enabled_effect_type_count": len(config.enabled_effect_types),
        "blocking_reasons": list(dict.fromkeys(blocking_reasons)),
    }


def _restart_external_runtime() -> None:
    if EXTERNAL_RUNTIME_SERVICE not in CANONICAL_RUNTIME_SERVICES:
        raise RuntimeError("external runtime service is outside the canonical inventory")
    subprocess.run(
        ("sudo", "systemctl", "restart", EXTERNAL_RUNTIME_SERVICE),
        check=True,
    )
    subprocess.run(
        ("sudo", "systemctl", "is-active", "--quiet", EXTERNAL_RUNTIME_SERVICE),
        check=True,
    )


def _wait_external_listener(
    repository: RuntimeGenerationRepository,
    *,
    generation: int,
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + max(1, int(timeout_seconds or 60))
    while True:
        names = repository.fresh_listener_heartbeat_names(
            generation=generation,
            freshness_seconds=30,
        )
        if EXTERNAL_HEARTBEAT_NAME in names:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("external runtime listener heartbeat did not become ready")
        time.sleep(1)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.expected_scope == args.target_scope:
        raise ValueError("expected_scope and target_scope must differ")
    policy_preflight = _policy_preflight(str(args.target_scope))
    plan = {
        "ok": True,
        "applied": False,
        "generation": int(args.generation),
        "expected_policy_version": str(args.expected_policy_version),
        "target_policy_version": str(args.target_policy_version),
        "expected_scope": str(args.expected_scope),
        "target_scope": str(args.target_scope),
        "claim_sequence": [
            "disable_claims",
            "drain_all_lanes",
            "append_policy_snapshot_and_scope_cas",
            "rewrite_generation_marker",
            "restart_external_runtime",
            "verify_listener",
            "resume_claims",
        ],
        "policy_preflight": policy_preflight,
        "real_external_call_executed": False,
    }
    if not args.apply:
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        return 0
    if str(os.getenv(AUTHORIZATION_ENV) or "").strip() != "1":
        raise RuntimeError(f"{AUTHORIZATION_ENV}=1 is required")
    if str(args.confirmation or "").strip() != _confirmation(args):
        raise RuntimeError(f"--confirmation must equal {_confirmation(args)}")
    if not bool(policy_preflight.get("ready")):
        raise RuntimeError(
            "allowlisted canary policy is not ready: "
            + ",".join(str(item) for item in policy_preflight.get("blocking_reasons") or [])
        )

    repository = RuntimeGenerationRepository()
    state = repository.read_state()
    source_matches = (
        state.active_generation == int(args.generation)
        and state.policy_version == str(args.expected_policy_version)
        and state.external_claim_scope == str(args.expected_scope)
    )
    target_matches = (
        state.active_generation == int(args.generation)
        and state.policy_version == str(args.target_policy_version)
        and state.external_claim_scope == str(args.target_scope)
    )
    if not source_matches and not target_matches:
        raise GenerationCASConflict("runtime state matches neither the source nor recovery target")
    if state.claim_enabled:
        repository.disable_claims(
            expected_generation=int(args.generation),
            actor=str(args.actor),
            reason=f"scope transition drain: {str(args.reason)}",
        )
    repository.wait_claims_drained(timeout_seconds=int(args.drain_timeout_seconds))
    if source_matches:
        state = repository.transition_external_claim_scope(
            expected_generation=int(args.generation),
            expected_policy_version=str(args.expected_policy_version),
            target_policy_version=str(args.target_policy_version),
            expected_scope=str(args.expected_scope),
            target_scope=str(args.target_scope),
            actor=str(args.actor),
            reason=str(args.reason),
        )

    lifecycle = SystemdQueueRuntimeLifecycle()
    lifecycle.write_generation_marker(
        generation=int(args.generation),
        committed=True,
        test_only=str(args.target_scope) == "test_loopback",
    )
    _restart_external_runtime()
    _wait_external_listener(
        repository,
        generation=int(args.generation),
        timeout_seconds=int(args.listener_timeout_seconds),
    )
    resumed = repository.resume_claims(
        expected_generation=int(args.generation),
        expected_policy_version=str(args.target_policy_version),
        expected_scope=str(args.target_scope),
        actor=str(args.actor),
        reason=f"listener verified after scope transition: {str(args.reason)}",
    )
    print(
        json.dumps(
            {
                **plan,
                "applied": True,
                "policy_version": resumed.policy_version,
                "external_claim_scope": resumed.external_claim_scope,
                "claim_enabled": resumed.claim_enabled,
                "listener_verified": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
