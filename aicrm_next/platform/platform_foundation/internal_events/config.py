from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from aicrm_next.platform.shared.runtime import fixture_mode
from aicrm_next.platform.shared.runtime_settings import (
    managed_runtime_bool,
    managed_runtime_int,
    managed_runtime_setting,
)


DEFAULT_WORKER_BATCH_SIZE = 50
DEFAULT_AUTO_EXECUTE_MAX_BATCH_SIZE = 1
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED",
        "AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS",
        "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS",
        "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES",
        "AICRM_INTERNAL_EVENTS_AUTO_EXECUTE",
        "AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE",
        "AICRM_INTERNAL_EVENTS_BROADCAST_TASK_ENABLED",
        "AICRM_INTERNAL_EVENTS_CUSTOMER_IDENTITY_ENABLED",
        "AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED",
        "AICRM_INTERNAL_EVENTS_ENABLED",
        "AICRM_INTERNAL_EVENTS_LEGACY_PATH_MARKERS_ENABLED",
        "AICRM_INTERNAL_EVENTS_LEGACY_PATH_RETIRE_AFTER_DAYS",
        "AICRM_INTERNAL_EVENTS_OPS_PLAN_ENABLED",
        "AICRM_INTERNAL_EVENTS_OWNER_MIGRATION_ENABLED",
        "AICRM_INTERNAL_EVENTS_PAYMENT_ENABLED",
        "AICRM_INTERNAL_EVENTS_QUESTIONNAIRE_ENABLED",
        "AICRM_INTERNAL_EVENTS_SHADOW_ONLY",
        "AICRM_INTERNAL_EVENTS_WORKER_BATCH_SIZE",
        "AICRM_INTERNAL_EVENT_WORKER_BATCH_SIZE",
    }
)
CONSUMER_METADATA: dict[str, dict[str, str]] = {
    "automation_questionnaire_consumer": {
        "type": "placeholder",
        "expected_status": "skipped",
        "reason": "not_configured",
    },
    "customer_summary_consumer": {
        "type": "placeholder",
        "expected_status": "skipped",
        "reason": "not_configured",
    },
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def env_bool(name: str, *, default: bool = False) -> bool:
    return managed_runtime_bool(name, default)


def internal_events_enabled() -> bool:
    return env_bool("AICRM_INTERNAL_EVENTS_ENABLED", default=fixture_mode())


def payment_internal_events_enabled() -> bool:
    return internal_events_enabled() and env_bool("AICRM_INTERNAL_EVENTS_PAYMENT_ENABLED", default=False)


def questionnaire_internal_events_enabled() -> bool:
    return internal_events_enabled() and env_bool("AICRM_INTERNAL_EVENTS_QUESTIONNAIRE_ENABLED", default=False)


def customer_tags_internal_events_enabled() -> bool:
    return internal_events_enabled() and env_bool("AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED", default=False)


def customer_identity_internal_events_enabled() -> bool:
    return internal_events_enabled() and env_bool("AICRM_INTERNAL_EVENTS_CUSTOMER_IDENTITY_ENABLED", default=False)


def ai_campaign_internal_events_enabled() -> bool:
    return internal_events_enabled() and env_bool("AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED", default=False)


def ops_plan_internal_events_enabled() -> bool:
    return internal_events_enabled() and env_bool("AICRM_INTERNAL_EVENTS_OPS_PLAN_ENABLED", default=False)


def broadcast_task_internal_events_enabled() -> bool:
    return internal_events_enabled() and env_bool("AICRM_INTERNAL_EVENTS_BROADCAST_TASK_ENABLED", default=False)


def owner_migration_internal_events_enabled() -> bool:
    return internal_events_enabled() and env_bool("AICRM_INTERNAL_EVENTS_OWNER_MIGRATION_ENABLED", default=False)


def legacy_path_markers_enabled() -> bool:
    return env_bool("AICRM_INTERNAL_EVENTS_LEGACY_PATH_MARKERS_ENABLED", default=False)


def legacy_path_retire_after_days() -> int:
    return managed_runtime_int(
        "AICRM_INTERNAL_EVENTS_LEGACY_PATH_RETIRE_AFTER_DAYS",
        7,
        minimum=1,
        maximum=365,
    )


def internal_events_shadow_only() -> bool:
    return env_bool("AICRM_INTERNAL_EVENTS_SHADOW_ONLY", default=not fixture_mode())


def auto_execute_enabled() -> bool:
    return env_bool("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", default=fixture_mode())


def allowed_event_types() -> list[str]:
    raw = _text(managed_runtime_setting("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES"))
    return [item.strip() for item in raw.split(",") if item.strip()]


def allowed_consumers() -> list[str]:
    raw = _text(managed_runtime_setting("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS"))
    return [item.strip() for item in raw.split(",") if item.strip()]


def allowed_event_consumers() -> list[str]:
    raw = _text(managed_runtime_setting("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS"))
    normalized = raw.replace("\n", ",")
    pairs: list[str] = []
    seen: set[str] = set()
    for item in normalized.split(","):
        text = _text(item)
        if not text or ":" not in text:
            continue
        event_type, consumer_name = (_text(part) for part in text.split(":", 1))
        if not event_type or not consumer_name:
            continue
        pair = f"{event_type}:{consumer_name}"
        if pair not in seen:
            pairs.append(pair)
            seen.add(pair)
    return pairs


def allowed_event_consumer_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in allowed_event_consumers():
        event_type, consumer_name = item.split(":", 1)
        pairs.append((event_type, consumer_name))
    return pairs


def pair_allowlist_enabled() -> bool:
    return bool(allowed_event_consumers())


def consumer_metadata(consumer_name: str) -> dict[str, str]:
    return dict(CONSUMER_METADATA.get(_text(consumer_name), {}))


def placeholder_consumers() -> list[str]:
    return sorted(consumer_name for consumer_name, metadata in CONSUMER_METADATA.items() if _text(metadata.get("type")) == "placeholder")


def config_warnings() -> list[str]:
    warnings: list[str] = []
    if auto_execute_enabled() and len(allowed_event_types()) > 1 and not pair_allowlist_enabled():
        warnings.append("auto_execute_multi_event_without_pair_allowlist")
    return warnings


def event_type_allowed(event_type: str, *, configured: list[str] | None = None) -> bool:
    allowed = configured if configured is not None else allowed_event_types()
    return not allowed or _text(event_type) in set(allowed)


def worker_allows(
    event_type: str,
    consumer_name: str,
    *,
    configured_pairs: Iterable[tuple[str, str]] | None = None,
    configured_event_types: Iterable[str] | None = None,
    configured_consumers: Iterable[str] | None = None,
) -> bool:
    """Return whether the current worker rollout can execute one event/consumer pair.

    A configured pair allowlist is authoritative.  The broader event/consumer
    allowlists are only used when no pair allowlist exists, matching the worker
    and diagnostics semantics.
    """

    normalized_pair = (_text(event_type), _text(consumer_name))
    event_types = set(configured_event_types if configured_event_types is not None else allowed_event_types())
    if event_types and normalized_pair[0] not in {_text(item) for item in event_types}:
        return False

    pairs = set(configured_pairs if configured_pairs is not None else allowed_event_consumer_pairs())
    if pairs:
        return normalized_pair in {(_text(item[0]), _text(item[1])) for item in pairs}

    consumers = set(configured_consumers if configured_consumers is not None else allowed_consumers())
    if consumers and normalized_pair[1] not in {_text(item) for item in consumers}:
        return False
    return True


def worker_batch_size() -> int:
    raw = _text(managed_runtime_setting("AICRM_INTERNAL_EVENTS_WORKER_BATCH_SIZE"))
    if raw:
        return managed_runtime_int(
            "AICRM_INTERNAL_EVENTS_WORKER_BATCH_SIZE",
            DEFAULT_WORKER_BATCH_SIZE,
            minimum=1,
            maximum=500,
        )
    return managed_runtime_int(
        "AICRM_INTERNAL_EVENT_WORKER_BATCH_SIZE",
        DEFAULT_WORKER_BATCH_SIZE,
        minimum=1,
        maximum=500,
    )


def auto_execute_max_batch_size() -> int:
    return managed_runtime_int(
        "AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE",
        DEFAULT_AUTO_EXECUTE_MAX_BATCH_SIZE,
        minimum=1,
        maximum=500,
    )


def diagnostics_payload() -> dict[str, Any]:
    return {
        "internal_events_enabled": internal_events_enabled(),
        "payment_internal_events_enabled": payment_internal_events_enabled(),
        "questionnaire_internal_events_enabled": questionnaire_internal_events_enabled(),
        "customer_tags_internal_events_enabled": customer_tags_internal_events_enabled(),
        "customer_identity_internal_events_enabled": customer_identity_internal_events_enabled(),
        "ai_campaign_internal_events_enabled": ai_campaign_internal_events_enabled(),
        "ops_plan_internal_events_enabled": ops_plan_internal_events_enabled(),
        "broadcast_task_internal_events_enabled": broadcast_task_internal_events_enabled(),
        "owner_migration_internal_events_enabled": owner_migration_internal_events_enabled(),
        "legacy_path_markers_enabled": legacy_path_markers_enabled(),
        "legacy_path_retire_after_days": legacy_path_retire_after_days(),
        "shadow_only": internal_events_shadow_only(),
        "auto_execute_enabled": auto_execute_enabled(),
        "allowed_event_types": allowed_event_types(),
        "allowed_consumers": allowed_consumers(),
        "allowed_event_consumers": allowed_event_consumers(),
        "pair_allowlist_enabled": pair_allowlist_enabled(),
        "worker_batch_size": worker_batch_size(),
        "auto_execute_max_batch_size": auto_execute_max_batch_size(),
        "config_warnings": config_warnings(),
        "config_source": "config_release_with_environment_fallback",
    }
