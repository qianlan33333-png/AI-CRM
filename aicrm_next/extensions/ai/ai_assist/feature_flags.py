from __future__ import annotations

from aicrm_next.platform.shared.runtime_settings import runtime_setting


CAMPAIGN_PREPARATION_V1_FLAG = "AICRM_CAMPAIGN_PREPARATION_V1_ENABLED"
DYNAMIC_MINIPROGRAM_CARD_V1_FLAG = "AICRM_DYNAMIC_MINIPROGRAM_CARD_V1_ENABLED"
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_CAMPAIGN_PREPARATION_V1_ENABLED",
        "AICRM_DYNAMIC_MINIPROGRAM_CARD_V1_ENABLED",
    }
)


def _enabled(name: str) -> bool:
    return str(runtime_setting(name, "") or "").strip().lower() in {"1", "true", "yes", "on"}


def campaign_preparation_v1_enabled() -> bool:
    return _enabled(CAMPAIGN_PREPARATION_V1_FLAG)


def dynamic_miniprogram_card_v1_enabled() -> bool:
    return _enabled(DYNAMIC_MINIPROGRAM_CARD_V1_FLAG)


__all__ = [
    "CAMPAIGN_PREPARATION_V1_FLAG",
    "DYNAMIC_MINIPROGRAM_CARD_V1_FLAG",
    "campaign_preparation_v1_enabled",
    "dynamic_miniprogram_card_v1_enabled",
]
