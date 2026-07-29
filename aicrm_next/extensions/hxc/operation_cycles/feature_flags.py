from __future__ import annotations

from aicrm_next.platform.shared.runtime_settings import runtime_setting


OPERATION_CONTEXT_V1_FLAG = "AICRM_OPERATION_CONTEXT_V1_ENABLED"
OPERATION_FACT_PROJECTION_V1_FLAG = "AICRM_OPERATION_FACT_PROJECTION_V1_ENABLED"
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_OPERATION_CONTEXT_V1_ENABLED",
        "AICRM_OPERATION_FACT_PROJECTION_V1_ENABLED",
    }
)


def _enabled(name: str, *, default: bool = False) -> bool:
    value = str(runtime_setting(name, "true" if default else "false") or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def operation_context_v1_enabled() -> bool:
    return _enabled(OPERATION_CONTEXT_V1_FLAG)


def operation_fact_projection_v1_enabled() -> bool:
    return _enabled(OPERATION_FACT_PROJECTION_V1_FLAG)


__all__ = [
    "OPERATION_CONTEXT_V1_FLAG",
    "OPERATION_FACT_PROJECTION_V1_FLAG",
    "operation_context_v1_enabled",
    "operation_fact_projection_v1_enabled",
]
