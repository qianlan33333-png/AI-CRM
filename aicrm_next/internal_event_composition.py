from __future__ import annotations

from functools import partial

from .capability_registry import capability_for_event_type
from .extensions.commerce.commerce.external_push_admin import plan_order_paid_external_push_effect
from .extensions.commerce.commerce.repo import execute_commerce_transaction
from .extensions.commerce.commerce.payment_tagging import (
    product_paid_wecom_tag_consumer,
    resolve_payment_tag_identity,
)
from .crm.identity_contact.payment_projection import project_payment_order_mobile
from .external_effect_composition import (
    AUTOMATION_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
    EXTERNAL_EFFECT_PROVIDER_RESULT_ACCESS_ALLOWLIST,
    QUESTIONNAIRE_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
    build_external_effect_continuation_consumers,
    build_external_effect_continuation_registry,
    build_external_effect_settlement_consumers,
)
from .extensions.ai.ai_audience_ops import register_ai_audience_event_consumers
from .operation_cycle_fact_composition import (
    register_operation_cycle_system_fact_consumers,
)
from .crm.customer_read_model.events import register_customer_read_model_event_consumers
from .extensions.growth.cloud_orchestrator.repository import build_cloud_plan_repository
from .extensions.forms.questionnaire.event_consumers import (
    automation_questionnaire_consumer,
    customer_summary_consumer,
    questionnaire_projection_consumer,
    questionnaire_tag_consumer,
    questionnaire_webhook_consumer,
)
from .extensions.commerce.service_period.payment_consumer import service_period_entitlement_consumer
from .extensions.commerce.service_period.refund_consumer import service_period_refund_consumer
from .platform.platform_foundation.internal_events.shadow import broadcast_task_planner_consumer
from .platform.platform_foundation.internal_events.payment import webhook_order_paid_consumer
from .platform.platform_foundation.external_effects.completion_events import (
    register_external_effect_completed_consumers,
)
from .platform.platform_foundation.external_effects.settlement_events import (
    register_external_effect_settled_consumers,
)
from .platform.platform_foundation.external_effects.repo import build_external_effect_repository
from .platform.platform_foundation.execution_runtime.commands import (
    register_queue_runtime_command_consumer,
)
from .platform.shared.runtime import production_data_ready
from .deployment_profile import DeploymentProfile


def _plan_order_paid_external_push_effect_from_db(
    *,
    order: dict,
    transaction: dict,
    domain_event_outbox_id: object,
) -> dict | None:
    if not production_data_ready():
        raise RuntimeError("production database is required for order-paid external push planning")

    def _plan(conn):
        return plan_order_paid_external_push_effect(
            conn,
            order=order,
            transaction=transaction,
            outbox={"id": domain_event_outbox_id},
            source_module="platform_foundation.internal_events.payment",
            source_route="/internal-events/payment.succeeded/webhook_order_paid_consumer",
        )

    return execute_commerce_transaction(_plan)


def _project_payment_order_identity_from_db(*, order: dict, source_route: str) -> dict:
    if not production_data_ready():
        return {"ok": True, "projected": False, "reason": "production_database_required"}

    return execute_commerce_transaction(lambda conn: project_payment_order_mobile(conn, order, source_route=source_route))


def _resolve_payment_tag_identity_from_db(order: dict, owner_userid: str) -> dict:
    if not production_data_ready():
        return {"ok": False, "reason": "production_database_required"}
    return execute_commerce_transaction(lambda conn: resolve_payment_tag_identity(conn, order, owner_userid))


from .platform.platform_foundation.internal_events import (
    InternalEventConsumerRegistry,
    current_internal_event_consumer_registry,
    register_payment_succeeded_consumers as _register_payment_succeeded_consumers,
    register_questionnaire_event_consumers as _register_questionnaire_event_consumers,
    register_refund_succeeded_consumers as _register_refund_succeeded_consumers,
    register_shadow_event_consumers as _register_shadow_event_consumers,
)


def register_payment_succeeded_consumers(registry: InternalEventConsumerRegistry | None = None) -> None:
    registry = registry or current_internal_event_consumer_registry()
    _register_payment_succeeded_consumers(
        registry,
        payment_identity_projector=_project_payment_order_identity_from_db,
        service_period_consumer=service_period_entitlement_consumer,
        product_paid_tag_consumer=partial(
            product_paid_wecom_tag_consumer,
            identity_resolver=_resolve_payment_tag_identity_from_db,
        ),
        webhook_order_paid_handler=partial(
            webhook_order_paid_consumer,
            external_push_planner=_plan_order_paid_external_push_effect_from_db,
        ),
    )


def register_refund_succeeded_consumers(registry: InternalEventConsumerRegistry | None = None) -> None:
    registry = registry or current_internal_event_consumer_registry()
    _register_refund_succeeded_consumers(
        registry,
        service_period_consumer=service_period_refund_consumer,
    )


def register_questionnaire_event_consumers(registry: InternalEventConsumerRegistry | None = None) -> None:
    registry = registry or current_internal_event_consumer_registry()
    _register_questionnaire_event_consumers(
        registry,
        handlers={
            "questionnaire_projection_consumer": questionnaire_projection_consumer,
            "questionnaire_webhook_consumer": questionnaire_webhook_consumer,
            "questionnaire_tag_consumer": questionnaire_tag_consumer,
            "automation_questionnaire_consumer": automation_questionnaire_consumer,
            "customer_summary_consumer": customer_summary_consumer,
        },
    )


def register_shadow_event_consumers(registry: InternalEventConsumerRegistry | None = None) -> None:
    registry = registry or current_internal_event_consumer_registry()
    _register_shadow_event_consumers(
        registry,
        broadcast_task_planner_handler=partial(
            broadcast_task_planner_consumer,
            repository_factory=build_cloud_plan_repository,
        ),
    )


_CONTINUATION_CAPABILITY = {
    QUESTIONNAIRE_EXTERNAL_EFFECT_CONTINUATION_CONSUMER: "extension.forms",
    AUTOMATION_EXTERNAL_EFFECT_CONTINUATION_CONSUMER: "extension.ai",
}


def _runtime_consumers(consumers, profile: DeploymentProfile | None):
    if profile is None or profile.activation_mode == "observe":
        return consumers
    return tuple(
        consumer
        for consumer in consumers
        if profile.allows_runtime(_CONTINUATION_CAPABILITY.get(consumer.consumer_name, "core.platform"))
    )


def register_external_effect_completion_consumers(
    registry: InternalEventConsumerRegistry | None = None,
    *,
    profile: DeploymentProfile | None = None,
) -> None:
    registry = registry or current_internal_event_consumer_registry()
    register_external_effect_completed_consumers(
        registry,
        consumers=_runtime_consumers(build_external_effect_continuation_consumers(), profile),
        repository_factory=build_external_effect_repository,
        legacy_continuation_registry_factory=partial(build_external_effect_continuation_registry, profile),
        provider_result_access_allowlist=EXTERNAL_EFFECT_PROVIDER_RESULT_ACCESS_ALLOWLIST,
    )


def register_external_effect_settlement_consumers(
    registry: InternalEventConsumerRegistry | None = None,
    *,
    profile: DeploymentProfile | None = None,
) -> None:
    registry = registry or current_internal_event_consumer_registry()
    register_external_effect_settled_consumers(
        registry,
        consumers=_runtime_consumers(build_external_effect_settlement_consumers(), profile),
        repository_factory=build_external_effect_repository,
    )


def build_internal_event_consumer_registry(
    profile: DeploymentProfile | None = None,
) -> InternalEventConsumerRegistry:
    registry = InternalEventConsumerRegistry()
    register_payment_succeeded_consumers(registry)
    register_refund_succeeded_consumers(registry)
    register_questionnaire_event_consumers(registry)
    register_shadow_event_consumers(registry)
    register_ai_audience_event_consumers(registry)
    register_customer_read_model_event_consumers(registry)
    register_operation_cycle_system_fact_consumers(registry)
    register_external_effect_completion_consumers(registry, profile=profile)
    register_external_effect_settlement_consumers(registry, profile=profile)
    register_queue_runtime_command_consumer(registry)
    if profile is not None and profile.activation_mode == "enforce":
        for event_type in tuple(registry.to_dict()):
            capability = capability_for_event_type(event_type)
            if capability is not None and not profile.allows_runtime(capability.capability_id):
                registry.remove_event_type(event_type)
    registry.seal_fanout_contract()
    return registry


__all__ = [
    "build_internal_event_consumer_registry",
    "register_payment_succeeded_consumers",
    "register_questionnaire_event_consumers",
    "register_refund_succeeded_consumers",
    "register_shadow_event_consumers",
    "register_external_effect_completion_consumers",
    "register_external_effect_settlement_consumers",
]
