from __future__ import annotations

from aicrm_next.external_effect_composition import (
    AUTOMATION_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
    BROADCAST_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
    EXTERNAL_EFFECT_PROVIDER_RESULT_ACCESS_ALLOWLIST,
    EXTERNAL_PUSH_EFFECT_CONTINUATION_CONSUMER,
    GROUP_OPS_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
    IDENTITY_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
    QUESTIONNAIRE_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
    WELCOME_MEDIA_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
    build_external_effect_adapter_registry,
    build_external_effect_continuation_consumers,
    build_external_effect_continuation_registry,
    build_external_effect_settlement_consumers,
)
from aicrm_next.main import create_app


def test_external_effect_continuation_composition_is_explicit_and_deterministic() -> None:
    first = build_external_effect_continuation_registry()
    second = build_external_effect_continuation_registry()

    assert first is not second
    assert first.names == (
        "identity_external_contact_detail_continuation",
        "group_ops_media_dependency_release",
        "channel_welcome_media_dependency_release",
        "broadcast_external_effect_read_model",
        "questionnaire_contact_tags",
        "external_push_delivery",
        "automation_agent_audience_webhook",
    )
    assert second.names == first.names


def test_external_effect_continuations_have_independent_durable_consumer_names() -> None:
    consumers = build_external_effect_continuation_consumers()

    assert tuple(item.consumer_name for item in consumers) == (
        IDENTITY_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
        GROUP_OPS_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
        WELCOME_MEDIA_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
        BROADCAST_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
        QUESTIONNAIRE_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
        EXTERNAL_PUSH_EFFECT_CONTINUATION_CONSUMER,
        AUTOMATION_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
    )
    assert tuple(item.continuation.name for item in consumers) == build_external_effect_continuation_registry().names
    assert all(item.max_attempts == 5 for item in consumers)
    assert {
        (item.consumer_name, item.continuation.name)
        for item in consumers
        if item.continuation.requires_provider_result
    } == EXTERNAL_EFFECT_PROVIDER_RESULT_ACCESS_ALLOWLIST
    assert EXTERNAL_EFFECT_PROVIDER_RESULT_ACCESS_ALLOWLIST == {
        (
            IDENTITY_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
            "identity_external_contact_detail_continuation",
        )
    }


def test_terminal_settlement_continuations_are_separate_and_provider_result_free() -> None:
    consumers = build_external_effect_settlement_consumers()

    assert tuple(item.continuation.name for item in consumers) == (
        "identity_external_effect_settlement",
        "group_ops_effect_graph_settlement",
        "welcome_effect_graph_settlement",
        "broadcast_external_effect_settlement",
        "external_push_delivery_settlement",
    )
    assert len({item.consumer_name for item in consumers}) == 5
    assert all(item.continuation.requires_provider_result is False for item in consumers)


def test_web_app_owns_its_external_effect_continuation_registry() -> None:
    first_app = create_app()
    second_app = create_app()

    assert first_app.state.external_effect_continuation_registry.names == (
        "identity_external_contact_detail_continuation",
        "group_ops_media_dependency_release",
        "channel_welcome_media_dependency_release",
        "broadcast_external_effect_read_model",
        "questionnaire_contact_tags",
        "external_push_delivery",
        "automation_agent_audience_webhook",
    )
    assert first_app.state.external_effect_continuation_registry is not second_app.state.external_effect_continuation_registry


def test_web_apps_do_not_share_external_effect_adapter_instances() -> None:
    first = build_external_effect_adapter_registry()
    second = build_external_effect_adapter_registry()
    first_app = create_app()
    second_app = create_app()

    assert first is not second
    assert first._adapters.keys() == second._adapters.keys()  # type: ignore[attr-defined]
    assert all(
        first._adapters[name] is not second._adapters[name]  # type: ignore[attr-defined]
        for name in first._adapters  # type: ignore[attr-defined]
    )
    assert first_app.state.external_effect_adapter_registry is not second_app.state.external_effect_adapter_registry


def test_callback_workers_use_their_own_app_effect_registry() -> None:
    first_app = create_app()
    second_app = create_app()
    first_worker = first_app.state.wecom_callback_inbox_worker_factory()
    second_worker = second_app.state.wecom_callback_inbox_worker_factory()

    assert first_worker is not second_worker
    assert (
        first_worker._processor.keywords["external_effect_adapter_registry"]  # type: ignore[attr-defined]
        is first_app.state.external_effect_adapter_registry
    )
    assert (
        second_worker._processor.keywords["external_effect_adapter_registry"]  # type: ignore[attr-defined]
        is second_app.state.external_effect_adapter_registry
    )
