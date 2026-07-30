#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.script_runtime import ensure_repo_root_on_path
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.script_runtime import ensure_repo_root_on_path

ensure_repo_root_on_path()

from aicrm_next.extensions.ai.ai_audience_ops.agent_gateway import (  # noqa: E402
    agent_gateway_configuration_snapshot,
)
from aicrm_next.platform.platform_foundation.execution_runtime.lane_rollout import (  # noqa: E402
    AiAutomationLaneRolloutRepository,
)
from aicrm_next.platform.platform_foundation.external_effects import (  # noqa: E402
    WECOM_MESSAGE_PRIVATE_SEND,
)
from aicrm_next.platform.shared.sensitive_data import redact_sensitive_data  # noqa: E402
from aicrm_next.platform.shared.wecom_runtime import load_wecom_execution_config  # noqa: E402


AUTHORIZATION_ENV = "AICRM_AI_AUTOMATION_LANE_ROLLOUT_AUTHORIZED"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CAS, audit, and wake one fail-closed AI automation queue lane.",
    )
    parser.add_argument("--lane", choices=("ai_generation", "wecom_ai_assistant_bulk"), required=True)
    parser.add_argument("--expected-generation", type=int, required=True)
    parser.add_argument("--expected-policy-version", required=True)
    parser.add_argument("--expected-mode", choices=("blocked", "canary", "execute"), required=True)
    parser.add_argument("--target-mode", choices=("blocked", "canary", "execute"), required=True)
    parser.add_argument("--expected-capacity", type=int, required=True)
    parser.add_argument("--target-capacity", type=int, required=True)
    parser.add_argument("--expected-open-job-id", type=int, action="append", default=[])
    parser.add_argument("--max-open-jobs", type=int, default=0)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true", default=False)
    parser.add_argument("--confirmation", default="")
    return parser.parse_args(argv)


def _confirmation(args: argparse.Namespace) -> str:
    return (
        f"PROMOTE_AI_AUTOMATION_LANE_{str(args.lane).upper()}_"
        f"{str(args.expected_mode).upper()}_TO_{str(args.target_mode).upper()}_"
        f"G{int(args.expected_generation)}"
    )


def _provider_preflight(lane: str, target_mode: str) -> dict[str, Any]:
    if target_mode == "blocked":
        return {"required": False, "ready": True, "blocking_reasons": []}
    if lane == "ai_generation":
        return {"required": True, **agent_gateway_configuration_snapshot()}
    config = load_wecom_execution_config()
    diagnostics = config.diagnostics()
    blocking_reasons = list(diagnostics.get("blocking_reasons") or [])
    if not config.real_calls_enabled:
        blocking_reasons.append("wecom_real_calls_not_ready")
    if WECOM_MESSAGE_PRIVATE_SEND not in config.enabled_effect_types:
        blocking_reasons.append("wecom_private_send_effect_not_enabled")
    return {
        "required": True,
        "ready": not blocking_reasons,
        "execution_mode": config.execution_mode,
        "enabled_effect_type_count": len(config.enabled_effect_types),
        "private_send_effect_enabled": WECOM_MESSAGE_PRIVATE_SEND in config.enabled_effect_types,
        "blocking_reasons": list(dict.fromkeys(blocking_reasons)),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repository = AiAutomationLaneRolloutRepository()
    request = {
        "lane": str(args.lane),
        "expected_generation": int(args.expected_generation),
        "expected_policy_version": str(args.expected_policy_version),
        "expected_mode": str(args.expected_mode),
        "target_mode": str(args.target_mode),
        "expected_capacity": int(args.expected_capacity),
        "target_capacity": int(args.target_capacity),
        "expected_open_job_ids": tuple(args.expected_open_job_id or ()),
        "max_open_jobs": int(args.max_open_jobs),
    }
    plan = repository.plan(**request)
    provider_preflight = _provider_preflight(plan.lane, plan.to_mode)
    payload = {
        **plan.as_dict(),
        "provider_preflight": provider_preflight,
        "confirmation": _confirmation(args),
        "real_external_call_executed": False,
    }
    if not args.apply:
        print(json.dumps(redact_sensitive_data(payload), ensure_ascii=False, sort_keys=True))
        return 0
    if str(os.getenv(AUTHORIZATION_ENV) or "").strip() != "1":
        raise RuntimeError(f"{AUTHORIZATION_ENV}=1 is required")
    if str(args.confirmation or "").strip() != _confirmation(args):
        raise RuntimeError(f"--confirmation must equal {_confirmation(args)}")
    if not bool(provider_preflight.get("ready")):
        raise RuntimeError(
            "provider preflight failed: "
            + ",".join(str(item) for item in provider_preflight.get("blocking_reasons") or [])
        )
    applied = repository.apply(
        **request,
        actor=str(args.actor),
        reason=str(args.reason),
    )
    print(
        json.dumps(
            redact_sensitive_data(
                {
                    **payload,
                    **applied.as_dict(),
                    "provider_preflight": provider_preflight,
                    "real_external_call_executed": False,
                }
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
