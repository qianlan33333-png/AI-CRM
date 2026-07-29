from __future__ import annotations

from typing import Any

from .capability_registry import PushCapability, get_push_capability
from .capability_repository import PushCapabilitySettingRepository

ALLOWED_TYPES_KEY = "AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES"
WEBHOOK_EXECUTE_KEY = "AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE"
_TRUE_VALUES = {"1", "true", "yes", "y", "on"}


def _enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _csv(value: Any) -> set[str]:
    return {
        item.strip()
        for item in str(value or "").replace("\n", ",").split(",")
        if item.strip()
    }


def _requires_webhook_gate(capability: PushCapability) -> bool:
    return capability.adapter_family in {"webhook", "legacy_webhook"} or any(
        effect_type.startswith("webhook.") for effect_type in capability.effect_types
    )


class PushCapabilityStatusReadService:
    """Small platform-owned projection for runtime capability eligibility."""

    def __init__(self, repository: PushCapabilitySettingRepository | None = None) -> None:
        self._repository = repository or PushCapabilitySettingRepository()

    def get_capability_status(self, key: str) -> dict[str, Any]:
        capability = get_push_capability(key)
        if capability is None:
            raise LookupError("push capability not found")
        values = self._repository.get_values(
            [capability.setting_key, ALLOWED_TYPES_KEY, WEBHOOK_EXECUTE_KEY]
        )
        configured_enabled = _enabled(values.get(capability.setting_key))
        readonly_reason = str(capability.readonly_reason or "").strip()
        gate_problem = ""
        if configured_enabled:
            allowed_types = _csv(values.get(ALLOWED_TYPES_KEY))
            if any(effect_type not in allowed_types for effect_type in capability.effect_types):
                gate_problem = "effect_type_allowlist_missing"
            elif _requires_webhook_gate(capability) and not _enabled(values.get(WEBHOOK_EXECUTE_KEY)):
                gate_problem = "webhook_execute_disabled"
        enabled = bool(
            capability.toggleable
            and capability.supports_real_execution
            and configured_enabled
            and not readonly_reason
            and not gate_problem
        )
        return {
            "key": capability.key,
            "enabled": enabled,
            "configured_enabled": configured_enabled,
            "readonly": bool(readonly_reason),
            "readonly_reason": readonly_reason,
            "gate_problem": gate_problem,
            "reason": readonly_reason or gate_problem or ("" if enabled else "capability_disabled"),
        }


__all__ = ["PushCapabilityStatusReadService"]
