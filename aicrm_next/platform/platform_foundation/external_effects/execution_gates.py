from __future__ import annotations

from aicrm_next.platform.shared.wecom_runtime import load_wecom_execution_config

WECOM_EXECUTION_MODE_KEY = "AICRM_WECOM_EXECUTION_MODE"
WECOM_EXECUTION_DISABLED_CODE = "wecom_execution_disabled"
WECOM_EFFECT_TYPE_NOT_ENABLED_CODE = "effect_type_not_allowed"
_MISSING_SETTING = "__aicrm_wecom_execution_mode_missing__"


def is_wecom_effect_type(effect_type: str) -> bool:
    return str(effect_type or "").strip().startswith("wecom.")


def explicit_wecom_execution_disabled() -> bool:
    config = load_wecom_execution_config()
    return config.conflict or (
        config.execution_mode_source == WECOM_EXECUTION_MODE_KEY
        and config.execution_mode != "execute"
    )


def typed_wecom_execution_block_reason(effect_type: str = "") -> str:
    config = load_wecom_execution_config()
    if config.conflict or config.execution_mode != "execute":
        return WECOM_EXECUTION_DISABLED_CODE
    normalized = str(effect_type or "").strip()
    if normalized and normalized not in config.enabled_effect_types:
        return WECOM_EFFECT_TYPE_NOT_ENABLED_CODE
    return ""


def wecom_execution_disabled_message(*, effect_type: str = "") -> str:
    config = load_wecom_execution_config()
    reasons = ",".join(config.blocking_reasons) or "not_execute"
    if config.execution_mode_source == WECOM_EXECUTION_MODE_KEY and config.execution_mode != "execute":
        return f"{WECOM_EXECUTION_MODE_KEY}={config.execution_mode} blocks WeCom external effects before adapter dispatch; typed reasons: {reasons}."
    if config.conflict or config.execution_mode != "execute":
        return f"Typed WeCom execution config blocks external effects before adapter dispatch: {reasons}."
    if effect_type and effect_type not in config.enabled_effect_types:
        return f"Typed WeCom execution config does not enable effect_type={effect_type}."
    return f"Typed WeCom execution config blocks external effects before adapter dispatch: {reasons}."
