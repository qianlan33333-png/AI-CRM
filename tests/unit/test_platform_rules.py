from __future__ import annotations

import pytest

from aicrm_next.capability_registry import (
    CAPABILITY_SPECS,
    capability_for_route_group,
    default_capability_ids,
    validate_capability_registry,
)
from aicrm_next.deployment_profile import (
    DeploymentProfile,
    RUNTIME_ROLES,
    default_deployment_profile,
    validate_deployment_profile,
)
from aicrm_next.platform.admin_auth.capabilities import capabilities_for_roles, normalize_roles
from aicrm_next.platform.shared.mobile import normalize_mainland_mobile
from aicrm_next.platform.shared.product_code_aliases import (
    canonical_product_code,
    canonical_product_name,
    product_code_filter_values,
)
from aicrm_next.platform.shared.runtime import runtime_health_state, runtime_route_map_state


pytestmark = pytest.mark.unit


def test_capability_registry_owns_every_declared_route_group() -> None:
    assert validate_capability_registry() == []
    route_groups = {group for spec in CAPABILITY_SPECS for group in spec.route_groups}
    assert route_groups
    assert all(capability_for_route_group(group) is not None for group in route_groups)


def test_default_profile_enables_the_current_core_and_runtime_roles() -> None:
    profile = default_deployment_profile()
    assert validate_deployment_profile(profile) == []
    assert set(default_capability_ids()) <= set(profile.enabled_capabilities)
    assert set(profile.runtime_roles) == set(RUNTIME_ROLES)


def test_profile_validation_fails_closed_for_unknown_capability() -> None:
    current = default_deployment_profile()
    invalid = DeploymentProfile(
        profile_id=current.profile_id,
        core_version=current.core_version,
        enabled_capabilities=(*current.enabled_capabilities, "extension.unknown"),
        runtime_roles=current.runtime_roles,
        config_schema_version=current.config_schema_version,
    )
    assert "unknown capability: extension.unknown" in validate_deployment_profile(invalid)


@pytest.mark.parametrize(
    ("raw", "allow_country_code", "expected"),
    [
        ("13800138000", False, "13800138000"),
        ("+86 138-0013-8000", True, "13800138000"),
        ("23800138000", False, ""),
        ([], False, ""),
    ],
)
def test_mobile_normalization_is_explicit(raw: object, allow_country_code: bool, expected: str) -> None:
    assert normalize_mainland_mobile(raw, allow_country_code=allow_country_code) == expected


def test_product_aliases_share_one_canonical_vocabulary() -> None:
    legacy = "prd_20260518095708_9f77db"
    canonical = canonical_product_code(legacy)
    assert canonical == "subscription_trial_month"
    assert canonical_product_name(canonical) == "订阅会员"
    assert {legacy, canonical} <= set(product_code_filter_values(canonical))


def test_role_capabilities_are_deduplicated_and_unknown_roles_add_nothing() -> None:
    assert normalize_roles(["viewer", "viewer", "", None]) == ("viewer",)
    assert "admin_read" in capabilities_for_roles(["viewer", "unknown"])


def test_runtime_state_declares_next_as_the_only_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    health = runtime_health_state()
    route_map = runtime_route_map_state()
    assert health["runtime_owner"] == "ai_crm_next"
    assert health["legacy_runtime_enabled"] is False
    assert route_map["route_owner"] == "ai_crm_next"
    assert route_map["legacy_callback_fallback_enabled"] is False
