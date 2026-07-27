from __future__ import annotations

from .consumer_registry import (
    DEFAULT_INTERNAL_EVENT_CONSUMER_REGISTRY,
    InternalEventConsumerRegistry,
    current_internal_event_consumer_registry,
    internal_event_consumer_registry_scope,
)
from .models import (
    InternalEvent,
    InternalEventConsumerAttempt,
    InternalEventConsumerResult,
    InternalEventConsumerRun,
)
from .repository import InMemoryInternalEventRepository, reset_internal_event_fixture_state
from .service import InternalEventService
from .legacy_path_markers import (
    legacy_path_marker_diagnostics,
    mark_legacy_path_invoked,
    reset_legacy_path_marker_state,
)
from .customer_identity import CUSTOMER_PHONE_BOUND_EVENT_TYPE, register_customer_identity_event_consumers
from .payment import PAYMENT_SUCCEEDED_EVENT_TYPE, PAYMENT_SUCCEEDED_EVENT_TYPES, register_payment_succeeded_consumers
from .refund import REFUND_SUCCEEDED_EVENT_TYPE, register_refund_succeeded_consumers
from .questionnaire import QUESTIONNAIRE_SUBMITTED_EVENT_TYPE, register_questionnaire_event_consumers
from .shadow import register_shadow_event_consumers
from .consumer_run_write_port import (
    InternalEventConsumerRunWritePort,
    build_internal_event_consumer_run_write_port,
)
from .outbox_runtime_write_port import (
    InternalEventOutboxRuntimeWritePort,
    build_internal_event_outbox_runtime_write_port,
)

__all__ = [
    "DEFAULT_INTERNAL_EVENT_CONSUMER_REGISTRY",
    "InMemoryInternalEventRepository",
    "InternalEvent",
    "InternalEventConsumerAttempt",
    "InternalEventConsumerRegistry",
    "current_internal_event_consumer_registry",
    "internal_event_consumer_registry_scope",
    "InternalEventConsumerResult",
    "InternalEventConsumerRun",
    "InternalEventConsumerRunWritePort",
    "InternalEventOutboxRuntimeWritePort",
    "InternalEventService",
    "legacy_path_marker_diagnostics",
    "mark_legacy_path_invoked",
    "PAYMENT_SUCCEEDED_EVENT_TYPE",
    "PAYMENT_SUCCEEDED_EVENT_TYPES",
    "REFUND_SUCCEEDED_EVENT_TYPE",
    "QUESTIONNAIRE_SUBMITTED_EVENT_TYPE",
    "CUSTOMER_PHONE_BOUND_EVENT_TYPE",
    "register_customer_identity_event_consumers",
    "register_payment_succeeded_consumers",
    "register_refund_succeeded_consumers",
    "register_questionnaire_event_consumers",
    "register_shadow_event_consumers",
    "build_internal_event_consumer_run_write_port",
    "build_internal_event_outbox_runtime_write_port",
    "reset_legacy_path_marker_state",
    "reset_internal_event_fixture_state",
]
