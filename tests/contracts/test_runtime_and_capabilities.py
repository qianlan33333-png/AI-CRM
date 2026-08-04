from __future__ import annotations

from pathlib import Path

import pytest

from aicrm_next.capability_registry import CAPABILITY_SPECS, validate_capability_registry
from aicrm_next.deployment_profile import load_deployment_profile, validate_deployment_profile
from aicrm_next.router_registry import ROUTER_SPECS, active_router_specs, router_registry_summary


pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]


def test_router_registry_uses_current_capability_owners() -> None:
    assert validate_capability_registry(CAPABILITY_SPECS) == []
    assert all(spec.logical_capability_id for spec in ROUTER_SPECS)
    summary = router_registry_summary()
    assert {item["route_group"] for item in summary} == {spec.route_group for spec in ROUTER_SPECS}
    assert all(item["route_count"] > 0 for item in summary)


def test_production_profile_can_only_activate_declared_current_capabilities() -> None:
    profile = load_deployment_profile(ROOT / "deploy" / "deployment_profiles" / "production-current.json")
    assert validate_deployment_profile(profile) == []
    active = active_router_specs(profile)
    assert active
    assert all(profile.allows_runtime(spec.logical_capability_id) for spec in active)


def test_retired_runtime_trees_are_not_current_sources() -> None:
    assert not (ROOT / "production_compat").exists()
    assert not (ROOT / "openclaw_service").exists()
    assert not (ROOT / "legacy_flask" / "openclaw_legacy").exists()
