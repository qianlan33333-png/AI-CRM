from __future__ import annotations

from aicrm_next.admin_config.schema import CONFIG_SCHEMA
from aicrm_next.capability_registry import (
    CAPABILITY_SPECS,
    capability_for_config_section,
    capability_for_context,
    capability_for_effect_type,
    capability_for_event_type,
    capability_for_module,
    capability_for_route_group,
    default_capability_ids,
    get_capability_spec,
    registry_summary,
    validate_capability_registry,
)
from aicrm_next.internal_event_composition import build_internal_event_consumer_registry
from aicrm_next.platform_foundation.external_effects import models as external_effect_models
from aicrm_next.router_registry import ROUTER_SPECS
from tools.check_capability_registry import EFFECT_PREFIXES, validate_capability_ownership
from tools.check_import_graph import scan_import_graph


def test_registry_has_seven_stable_core_capabilities_and_opt_in_extensions() -> None:
    assert validate_capability_registry() == []
    core = [spec for spec in CAPABILITY_SPECS if spec.tier == "core"]
    extensions = [spec for spec in CAPABILITY_SPECS if spec.tier == "extension"]

    assert len(core) == 7
    assert extensions
    assert {spec.capability_id for spec in core} == set(default_capability_ids())
    assert all(not spec.enabled_by_default for spec in extensions)
    assert all(spec.package_root.startswith("aicrm_next.extensions.") for spec in extensions)
    assert len({spec.package_root for spec in extensions}) == len(extensions)
    assert registry_summary()["logical_domains"] == [
        "app",
        "automation",
        "channels",
        "crm",
        "engagement",
        "extensions",
        "insights",
        "platform",
    ]


def test_current_contexts_routes_and_config_sections_have_one_logical_owner() -> None:
    contexts = scan_import_graph().contexts
    assert len(contexts) == 39
    assert all(capability_for_context(context) is not None for context in contexts)
    assert len({context for spec in CAPABILITY_SPECS for context in spec.current_contexts}) == len(contexts)

    route_groups = [spec.route_group for spec in ROUTER_SPECS]
    assert len(route_groups) == len(set(route_groups))
    assert all(capability_for_route_group(group) is not None for group in route_groups)
    assert all(capability_for_config_section(section) is not None for section in CONFIG_SCHEMA)


def test_event_and_external_effect_types_have_one_logical_owner() -> None:
    registry = build_internal_event_consumer_registry()
    assert all(capability_for_event_type(event_type) is not None for event_type in registry.to_dict())

    effect_types = {
        value
        for name, value in vars(external_effect_models).items()
        if name.isupper() and isinstance(value, str) and value.startswith(EFFECT_PREFIXES)
    }
    assert effect_types
    assert all(capability_for_effect_type(effect_type) is not None for effect_type in effect_types)
    assert capability_for_effect_type("wecom.message.broadcast.send").capability_id == "core.automation"
    assert capability_for_effect_type("payment.wechat.refund.request").capability_id == "extension.commerce"


def test_capability_ownership_checker_covers_all_current_manifests() -> None:
    assert validate_capability_ownership() == []


def test_capability_dependencies_are_explicit_and_resolvable() -> None:
    for spec in CAPABILITY_SPECS:
        for dependency in spec.dependencies:
            assert get_capability_spec(dependency) is not None
            assert dependency != spec.capability_id


def test_legacy_contexts_are_assigned_by_business_owner_not_runtime_filename() -> None:
    assert capability_for_context("background_jobs").capability_id == "core.automation"
    assert capability_for_context("navigation_target").capability_id == "core.platform"


def test_capability_module_resolution_supports_physical_extension_packages() -> None:
    assert capability_for_module("aicrm_next.extensions.ai.ai_audience_ops.service").capability_id == "extension.ai"
    assert capability_for_module("aicrm_next.extensions.forms.questionnaire.api").capability_id == "extension.forms"
    assert capability_for_module("aicrm_next.admin_auth.service").capability_id == "core.platform"
