from __future__ import annotations

import json
from dataclasses import replace

import pytest

from aicrm_next.capability_registry import default_capability_ids
from aicrm_next.deployment_profile import (
    DEPLOYMENT_PROFILE_PATH_ENV,
    RUNTIME_ROLES,
    DeploymentProfile,
    default_deployment_profile,
    deployment_profile_from_environment,
    load_deployment_profile,
    validate_deployment_profile,
)
from aicrm_next.external_effect_composition import (
    build_external_effect_adapter_registry,
    build_external_effect_continuation_registry,
)
from aicrm_next.internal_event_composition import build_internal_event_consumer_registry
from aicrm_next.main import create_app
from aicrm_next.platform_foundation.external_effects import (
    ExternalEffectService,
    InMemoryExternalEffectRepository,
)
from aicrm_next.platform_foundation.external_effects.adapters import ExternalEffectAdapterRegistry
from aicrm_next.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.router_registry import active_router_specs
from tools.check_deployment_profiles import DEFAULT_PROFILE_DIR, validate_deployment_profiles


def test_default_profile_is_single_corp_core_only_and_observational() -> None:
    profile = default_deployment_profile()

    assert profile.corp_mode == "single_corp_multi_app"
    assert profile.activation_mode == "observe"
    assert profile.enabled_capabilities == default_capability_ids()
    assert profile.runtime_roles == RUNTIME_ROLES
    assert validate_deployment_profile(profile) == []


def test_checked_in_profiles_are_valid_and_secret_free() -> None:
    assert validate_deployment_profiles(DEFAULT_PROFILE_DIR) == []
    profile = load_deployment_profile(DEFAULT_PROFILE_DIR / "wecom-core.json")

    assert profile == default_deployment_profile()
    assert all(not capability_id.startswith("extension.") for capability_id in profile.enabled_capabilities)


def test_profile_rejects_missing_dependency_and_runtime_role() -> None:
    profile = DeploymentProfile(
        profile_id="broken-profile",
        core_version="0.1.0",
        enabled_capabilities=("core.app",),
        runtime_roles=("web",),
        config_schema_version=1,
    )

    errors = validate_deployment_profile(profile)
    assert any("core capabilities cannot be disabled" in error for error in errors)
    assert any("missing dependencies" in error for error in errors)
    assert any("required runtime roles are missing" in error for error in errors)


def test_profile_loader_rejects_embedded_secrets(tmp_path) -> None:
    payload = default_deployment_profile().to_dict()
    payload["wecom_secret"] = "must-not-be-here"
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="must not contain secrets"):
        load_deployment_profile(path)


def test_profile_can_be_selected_with_the_single_startup_environment_reference(tmp_path) -> None:
    payload = default_deployment_profile().to_dict()
    payload["profile_id"] = "customer-one"
    path = tmp_path / "customer-one.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = deployment_profile_from_environment({DEPLOYMENT_PROFILE_PATH_ENV: str(path)})

    assert loaded.profile_id == "customer-one"
    assert loaded.to_dict() == payload


def test_enforce_mode_removes_disabled_routes_consumers_and_provider_adapters() -> None:
    profile = replace(default_deployment_profile(), activation_mode="enforce")

    route_groups = {spec.route_group for spec in active_router_specs(profile)}
    assert "admin_config" in route_groups
    assert "questionnaire" not in route_groups
    assert "commerce" not in route_groups
    assert "ai_audience_ops" not in route_groups

    registry = build_internal_event_consumer_registry(profile)
    assert "questionnaire.submitted" not in registry.to_dict()
    assert "payment.succeeded" not in registry.to_dict()
    assert all("ai_audience" not in event_type for event_type in registry.to_dict())

    adapters = build_external_effect_adapter_registry(profile)
    assert type(adapters.get("wechat_payment")).__name__ == "DisabledAdapter"
    legacy_continuations = build_external_effect_continuation_registry(profile)
    assert all("questionnaire" not in name for name in legacy_continuations.names)
    assert all("automation_agent" not in name for name in legacy_continuations.names)

    effects = ExternalEffectService(
        InMemoryExternalEffectRepository(),
        deployment_profile=profile,
    )
    with pytest.raises(ValueError, match="extension.commerce"):
        effects.plan_effect(
            effect_type="payment.wechat.order.query",
            adapter_name="wechat_payment",
            operation="query",
            target_type="order",
            target_id="order:1",
            idempotency_key="order:1:query",
        )


def test_enforce_mode_removes_disabled_routes_and_static_mounts_from_the_app() -> None:
    profile = replace(default_deployment_profile(), activation_mode="enforce")

    app = create_app(deployment_profile=profile)
    paths = {str(getattr(route, "path", "")) for route in app.routes}

    assert "/admin/config" in paths
    assert "/questionnaires" not in paths
    assert "/api/admin/commerce/transactions" not in paths
    assert "/static/questionnaire" not in paths
    assert "/static/service-period" not in paths


def test_external_worker_blocks_residual_disabled_capability_before_adapter_dispatch() -> None:
    profile = replace(default_deployment_profile(), activation_mode="enforce")
    repository = InMemoryExternalEffectRepository()
    job = ExternalEffectService(repository).plan_effect(
        effect_type="payment.wechat.order.query",
        adapter_name="must_not_dispatch",
        operation="query",
        target_type="order",
        target_id="order:residual",
        idempotency_key="order:residual:query",
    )
    worker = ExternalEffectWorker(
        repository,
        ExternalEffectAdapterRegistry({"must_not_dispatch": _ExplodingAdapter()}),
        deployment_profile=profile,
    )

    result = worker.dispatch_one(job["id"])

    assert result["job"]["status"] == "blocked"
    assert result["attempt"]["error_code"] == "capability_disabled"
    assert result["real_external_call_executed"] is False


class _ExplodingAdapter:
    def dispatch(self, _job):
        raise AssertionError("disabled capability reached provider adapter")
