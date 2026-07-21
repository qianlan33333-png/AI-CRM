from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.customer_read_model.refresh_intents import (
    CUSTOMER_REFRESH_COMPLETED_CONSUMER,
    CUSTOMER_REFRESH_COMPLETED_EVENT,
)
from aicrm_next.external_effect_composition import (
    build_external_effect_continuation_consumers,
    build_external_effect_settlement_consumers,
)
from aicrm_next.internal_event_composition import build_internal_event_consumer_registry
from aicrm_next.main import create_app
from aicrm_next.platform_foundation.external_effects.completion_events import (
    EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE,
    LEGACY_EXTERNAL_EFFECT_COMPLETION_CONSUMER,
)
from aicrm_next.platform_foundation.external_effects.settlement_events import (
    EXTERNAL_EFFECT_SETTLED_EVENT_TYPE,
)
from aicrm_next.platform_foundation.internal_events import (
    DEFAULT_INTERNAL_EVENT_CONSUMER_REGISTRY,
    InternalEventConsumerRegistry,
    InternalEventService,
    current_internal_event_consumer_registry,
    internal_event_consumer_registry_scope,
)


def _consumer_names(registry: InternalEventConsumerRegistry, event_type: str) -> set[str]:
    return {consumer.consumer_name for consumer in registry.list_for_event_type(event_type)}


def _unused_handler(event, run):  # pragma: no cover - registration probe only
    raise AssertionError("registry isolation probe must not execute")


def test_composition_builds_complete_isolated_registries() -> None:
    first = build_internal_event_consumer_registry()
    second = build_internal_event_consumer_registry()

    assert first is not second
    assert first.is_fanout_authoritative is True
    assert second.is_fanout_authoritative is True
    assert _consumer_names(first, "payment.succeeded") == _consumer_names(second, "payment.succeeded")
    assert "service_period_entitlement_consumer" in _consumer_names(first, "payment.succeeded")
    continuation_consumers = {item.consumer_name for item in build_external_effect_continuation_consumers()}
    assert _consumer_names(first, EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE) == continuation_consumers
    assert LEGACY_EXTERNAL_EFFECT_COMPLETION_CONSUMER not in continuation_consumers
    assert first.get_handler(EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE, LEGACY_EXTERNAL_EFFECT_COMPLETION_CONSUMER) is not None
    assert {
        (item["event_type"], item["consumer_name"])
        for item in first.aliases_to_dict()
    } >= {(EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE, LEGACY_EXTERNAL_EFFECT_COMPLETION_CONSUMER)}

    manifest = first.fanout_manifest_for(EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE)
    assert manifest["expected_consumer_count"] == len(continuation_consumers)
    assert {item["consumer_name"] for item in manifest["consumers"]} == continuation_consumers
    assert LEGACY_EXTERNAL_EFFECT_COMPLETION_CONSUMER not in {
        item["consumer_name"] for item in manifest["consumers"]
    }
    customer_refresh_manifest = first.fanout_manifest_for(CUSTOMER_REFRESH_COMPLETED_EVENT)
    assert customer_refresh_manifest["expected_consumer_count"] == 1
    assert {item["consumer_name"] for item in customer_refresh_manifest["consumers"]} == {
        CUSTOMER_REFRESH_COMPLETED_CONSUMER
    }
    settlement_consumers = {item.consumer_name for item in build_external_effect_settlement_consumers()}
    assert _consumer_names(first, EXTERNAL_EFFECT_SETTLED_EVENT_TYPE) == settlement_consumers
    settlement_manifest = first.fanout_manifest_for(EXTERNAL_EFFECT_SETTLED_EVENT_TYPE)
    assert settlement_manifest["expected_consumer_count"] == len(settlement_consumers)
    assert {item["consumer_name"] for item in settlement_manifest["consumers"]} == settlement_consumers

    with pytest.raises(RuntimeError, match="fanout contract is sealed"):
        first.register("registry.probe", "first_only", _unused_handler)

    assert _consumer_names(first, "registry.probe") == set()
    assert _consumer_names(second, "registry.probe") == set()


def test_sealed_registry_allows_handler_rebind_without_changing_fanout_manifest() -> None:
    registry = build_internal_event_consumer_registry()
    before = registry.fanout_manifest_for("payment.succeeded")
    current = registry.list_for_event_type("payment.succeeded")[0]

    registry.register(
        current.event_type,
        current.consumer_name,
        _unused_handler,
        consumer_type=current.consumer_type,
        max_attempts=current.max_attempts,
    )

    assert registry.get_handler(current.event_type, current.consumer_name) is _unused_handler
    assert registry.fanout_manifest_for("payment.succeeded") == before


def test_create_app_owns_registry_without_mutating_process_default() -> None:
    before = DEFAULT_INTERNAL_EVENT_CONSUMER_REGISTRY.to_dict()

    first_app = create_app()
    second_app = create_app()

    assert first_app.state.internal_event_consumer_registry is not second_app.state.internal_event_consumer_registry
    assert DEFAULT_INTERNAL_EVENT_CONSUMER_REGISTRY.to_dict() == before


def test_registry_scope_binds_default_services_and_restores_cli_fallback() -> None:
    registry = build_internal_event_consumer_registry()

    assert current_internal_event_consumer_registry() is DEFAULT_INTERNAL_EVENT_CONSUMER_REGISTRY
    with internal_event_consumer_registry_scope(registry):
        assert current_internal_event_consumer_registry() is registry
        assert InternalEventService()._registry is registry
    assert current_internal_event_consumer_registry() is DEFAULT_INTERNAL_EVENT_CONSUMER_REGISTRY


def test_web_request_uses_its_own_immutable_app_registry() -> None:
    first_app = create_app()
    second_app = create_app()

    first_payload = TestClient(first_app).get("/api/admin/internal-events/diagnostics").json()
    second_payload = TestClient(second_app).get("/api/admin/internal-events/diagnostics").json()

    assert first_app.state.internal_event_consumer_registry is not second_app.state.internal_event_consumer_registry
    assert first_app.state.internal_event_consumer_registry.is_fanout_authoritative is True
    with pytest.raises(RuntimeError, match="fanout contract is sealed"):
        first_app.state.internal_event_consumer_registry.register("registry.probe", "first_only", _unused_handler)
    assert "registry.probe" not in first_payload["registered_consumers"]
    assert "registry.probe" not in second_payload["registered_consumers"]
