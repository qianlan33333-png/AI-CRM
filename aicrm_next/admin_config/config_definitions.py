from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from aicrm_next.capability_registry import capability_for_config_section, get_capability_spec
from aicrm_next.shared.secret_store import SENSITIVE_SETTING_KEYS, is_secret_reference

from .application_support import APP_SETTING_DEFINITIONS, EXTRA_SETTING_DEFINITIONS, _validate_known_setting
from .schema import CONFIG_SCHEMA


ConfigStorage = Literal["app_settings", "secret_store"]


@dataclass(frozen=True)
class ConfigDefinition:
    key: str
    capability_id: str
    section: str
    value_type: str
    label: str
    description: str
    required: bool
    sensitive: bool
    storage: ConfigStorage
    default: str = ""
    restart_required: bool = False
    deprecated_after: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self, value: Any) -> str:
        normalized = str(value if value is not None else "")
        if self.sensitive:
            if not normalized:
                return normalized
            if not is_secret_reference(normalized):
                raise ValueError(f"{self.key} must be stored as a secret reference")
            return normalized
        return _validate_known_setting(self.key, normalized)


def build_config_definitions() -> tuple[ConfigDefinition, ...]:
    schema_fields: dict[str, tuple[str, dict[str, Any]]] = {}
    for section, raw_section in CONFIG_SCHEMA.items():
        for key, raw_field in dict(raw_section.get("fields") or {}).items():
            if key in schema_fields:
                raise ValueError(f"config key is declared in more than one section: {key}")
            schema_fields[key] = (section, dict(raw_field or {}))

    metadata = {str(item["key"]): dict(item) for item in APP_SETTING_DEFINITIONS}
    for key, item in EXTRA_SETTING_DEFINITIONS.items():
        metadata.setdefault(str(key), dict(item))

    result: list[ConfigDefinition] = []
    for key in sorted(set(metadata) | set(schema_fields)):
        item = metadata.get(key, {})
        section, schema = schema_fields.get(key, (_inferred_section(key), {}))
        capability = capability_for_config_section(section)
        if capability is None:
            capability = get_capability_spec(_inferred_capability_id(key))
        if capability is None:
            raise ValueError(f"config key has no capability owner: {key}")
        value_type = str(schema.get("type") or item.get("input_type") or "string").strip()
        sensitive = bool(
            key in SENSITIVE_SETTING_KEYS
            or item.get("mode") == "masked"
            or value_type == "secret"
            or key.endswith("_SECRET_REF")
        )
        result.append(
            ConfigDefinition(
                key=key,
                capability_id=capability.capability_id,
                section=section,
                value_type=value_type,
                label=str(schema.get("label") or item.get("label") or key),
                description=str(schema.get("help") or item.get("description") or ""),
                required=bool(schema.get("required", False)),
                sensitive=sensitive,
                storage="secret_store" if sensitive else "app_settings",
                default=str(schema.get("default") or ""),
            )
        )
    return tuple(result)


def _inferred_section(key: str) -> str:
    if key.startswith(("WECHAT_PAY_", "ALIPAY_", "WECHAT_SHOP_")):
        return "wechat_pay_h5" if key.startswith(("WECHAT_PAY_", "WECHAT_SHOP_")) else "alipay_pay_wap"
    if key.startswith(("WECHAT_MP_", "QUESTIONNAIRE_", "AICRM_QUESTIONNAIRE_")):
        return "wechat_mp"
    if key.startswith("WECOM_ARCHIVE_") or key in {"WECOM_PRIVATE_KEY_PATH", "WECOM_SDK_LIB_PATH"}:
        return "wecom_archive"
    if key.startswith(("DEEPSEEK_", "AICRM_AI_")):
        return "ai_services"
    if key.startswith(("WECOM_", "AICRM_WECOM_", "AICRM_SIDEBAR_JSSDK_")):
        return "wecom_base"
    if key.startswith(("AICRM_EXTERNAL_EFFECT_", "OUTBOUND_WEBHOOK_", "OPENCLAW_")):
        return "reliability"
    if key.startswith(("AICRM_AUTH_", "ADMIN_")):
        return "admin_auth"
    return "infrastructure"


def _inferred_capability_id(key: str) -> str:
    section = _inferred_section(key)
    capability = capability_for_config_section(section)
    return capability.capability_id if capability else "core.platform"


CONFIG_DEFINITIONS = build_config_definitions()
CONFIG_DEFINITIONS_BY_KEY = {definition.key: definition for definition in CONFIG_DEFINITIONS}


def get_config_definition(key: str) -> ConfigDefinition | None:
    return CONFIG_DEFINITIONS_BY_KEY.get(str(key or "").strip())


def config_definition_summary() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "definition_count": len(CONFIG_DEFINITIONS),
        "sensitive_count": sum(1 for item in CONFIG_DEFINITIONS if item.sensitive),
        "restart_required_count": sum(1 for item in CONFIG_DEFINITIONS if item.restart_required),
        "definitions": [item.to_dict() for item in CONFIG_DEFINITIONS],
    }


__all__ = [
    "CONFIG_DEFINITIONS",
    "CONFIG_DEFINITIONS_BY_KEY",
    "ConfigDefinition",
    "config_definition_summary",
    "get_config_definition",
]
