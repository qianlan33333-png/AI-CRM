#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.capability_registry import CAPABILITY_SPECS  # noqa: E402
from aicrm_next.deployment_profile import (  # noqa: E402
    DEPLOYMENT_PROFILE_PATH_ENV,
    deployment_profile_from_environment,
)


def validate_production_deployment_profile(
    *,
    environment: Mapping[str, str],
    expected_profile_path: Path,
) -> dict[str, Any]:
    expected_path = Path(expected_profile_path)
    if not expected_path.is_absolute():
        raise ValueError("expected production deployment profile path must be absolute")
    configured_path = Path(str(environment.get(DEPLOYMENT_PROFILE_PATH_ENV) or "").strip())
    if configured_path != expected_path:
        raise ValueError(
            "production deployment profile selection mismatch: "
            f"expected {expected_path}, got {configured_path or 'missing'}"
        )

    profile = deployment_profile_from_environment(environment)
    expected_capabilities = tuple(spec.capability_id for spec in CAPABILITY_SPECS)
    if profile.profile_id != "production-current":
        raise ValueError(f"production profile_id must be production-current, got {profile.profile_id}")
    if profile.activation_mode != "enforce":
        raise ValueError(f"production profile activation_mode must be enforce, got {profile.activation_mode}")
    if profile.enabled_capabilities != expected_capabilities:
        missing = sorted(set(expected_capabilities) - set(profile.enabled_capabilities))
        extra = sorted(set(profile.enabled_capabilities) - set(expected_capabilities))
        raise ValueError(
            "production profile must enable the exact current capability registry: "
            f"missing={missing} extra={extra}"
        )
    return {
        "ok": True,
        "profile_id": profile.profile_id,
        "activation_mode": profile.activation_mode,
        "capability_count": len(profile.enabled_capabilities),
        "runtime_roles": list(profile.runtime_roles),
        "profile_path": str(expected_path),
        "target_values_redacted": True,
        "real_external_call_executed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail closed before selecting the production Deployment Profile.")
    parser.add_argument("--expected-profile-path", required=True, type=Path)
    args = parser.parse_args()
    result = validate_production_deployment_profile(
        environment=os.environ,
        expected_profile_path=args.expected_profile_path,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
