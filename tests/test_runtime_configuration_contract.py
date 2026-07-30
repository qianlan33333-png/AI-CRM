from __future__ import annotations

from pathlib import Path

import pytest

from aicrm_next.platform.admin_config.config_definitions import CONFIG_DEFINITIONS_BY_KEY
from aicrm_next.runtime_configuration import (
    BUSINESS_RUNTIME_SETTING_KEYS,
    DOMAIN_RUNTIME_SETTING_KEYS,
    INTEGRATION_GATEWAY_RUNTIME_SETTING_KEYS,
    MANAGED_RUNTIME_SETTING_KEYS,
    STARTUP_ENVIRONMENT_SETTING_KEYS,
)
from tools.check_runtime_configuration_contract import (
    MIGRATED_PATHS,
    check_runtime_configuration_contract,
)


ROOT = Path(__file__).resolve().parents[1]


def test_migrated_contexts_satisfy_runtime_configuration_contract() -> None:
    assert check_runtime_configuration_contract(root=ROOT) == []


def test_integration_gateway_runtime_catalog_is_complete_owned_and_gated() -> None:
    gateway_keys = INTEGRATION_GATEWAY_RUNTIME_SETTING_KEYS

    assert MIGRATED_PATHS == ("aicrm_next",)
    assert len(gateway_keys) == 126
    assert len(MANAGED_RUNTIME_SETTING_KEYS) == 244
    assert gateway_keys <= MANAGED_RUNTIME_SETTING_KEYS
    assert gateway_keys <= set(CONFIG_DEFINITIONS_BY_KEY)
    assert all(CONFIG_DEFINITIONS_BY_KEY[key].capability_id for key in gateway_keys)

    expected_owners = {
        "AICRM_NEXT_CUSTOMER_CONTEXT_TOOL_MODE": "extension.ai",
        "AICRM_NEXT_MEDIA_STORAGE_MODE": "core.engagement",
        "AICRM_NEXT_ENABLE_REAL_WECOM_DISPATCH": "core.automation",
        "AICRM_NEXT_ENABLE_REAL_WECOM_TAG": "core.crm",
        "AICRM_NEXT_ENABLE_REAL_WECHAT_PAY": "extension.commerce",
        "AICRM_NEXT_ENABLE_REAL_QUESTIONNAIRE_WEBHOOK": "extension.forms",
        "AICRM_WECOM_CONTACT_CALLBACK_TOKEN": "core.channels",
        "AICRM_HUANGYOUCAN_DB_PASSWORD": "extension.hxc",
        "AICRM_ENABLE_REAL_WECOM_GROUP_MESSAGE": "core.automation",
        "AICRM_ENABLE_REAL_WECOM_PRIVATE_MESSAGE": "core.automation",
    }
    assert {
        key: CONFIG_DEFINITIONS_BY_KEY[key].capability_id for key in expected_owners
    } == expected_owners


def test_ai_and_commerce_runtime_catalog_is_complete_owned_and_gated() -> None:
    assert MIGRATED_PATHS == ("aicrm_next",)
    assert len(BUSINESS_RUNTIME_SETTING_KEYS) == 13
    assert BUSINESS_RUNTIME_SETTING_KEYS <= MANAGED_RUNTIME_SETTING_KEYS
    assert BUSINESS_RUNTIME_SETTING_KEYS <= set(CONFIG_DEFINITIONS_BY_KEY)
    expected_owners = {
        "AICRM_AI_AUDIENCE_AGENT_FAKE_ALLOWED": "extension.ai",
        "AICRM_AI_AUDIENCE_SPEC_ALLOW_PUBLISH": "extension.ai",
        "WECHAT_PAY_PENDING_ORDER_TTL_HOURS": "extension.commerce",
        "WECHAT_PAY_REFUND_NOTIFY_URL": "extension.commerce",
        "WECHAT_SHOP_APPID": "extension.commerce",
    }
    assert {
        key: CONFIG_DEFINITIONS_BY_KEY[key].capability_id for key in expected_owners
    } == expected_owners


def test_repository_wide_domain_runtime_catalog_is_complete_owned_and_gated() -> None:
    assert len(DOMAIN_RUNTIME_SETTING_KEYS) == 62
    assert len(STARTUP_ENVIRONMENT_SETTING_KEYS) == 32
    assert DOMAIN_RUNTIME_SETTING_KEYS <= MANAGED_RUNTIME_SETTING_KEYS
    assert DOMAIN_RUNTIME_SETTING_KEYS <= set(CONFIG_DEFINITIONS_BY_KEY)
    expected_owners = {
        "AICRM_DATA_HEALTH_PROJECTION_FRESHNESS_MAX_MINUTES": "core.insights",
        "AICRM_GROUP_OPS_TIMEZONE": "core.automation",
        "AICRM_NEXT_CLOUD_ORCHESTRATOR_MEDIA_UPLOAD_MODE": "core.engagement",
        "AICRM_QUESTIONNAIRE_OAUTH_STATE_SECRET": "extension.forms",
        "AICRM_WECOM_TAG_LIVE_ADAPTER_ENABLED": "core.crm",
        "AI_ASSIST_EXTERNAL_EFFECT_SEND_MODE": "extension.ai",
        "WECHAT_PAY_OAUTH_SCOPE": "extension.commerce",
        "WECOM_TIMEOUT_SECONDS": "core.channels",
    }
    assert {
        key: CONFIG_DEFINITIONS_BY_KEY[key].capability_id for key in expected_owners
    } == expected_owners


def test_integration_gateway_secret_definitions_require_secret_references() -> None:
    password = CONFIG_DEFINITIONS_BY_KEY["AICRM_HUANGYOUCAN_DB_PASSWORD"]

    assert password.sensitive is True
    assert password.storage == "secret_store"
    with pytest.raises(ValueError, match="secret reference"):
        password.validate("raw-password")
    reference = (
        "secretref:file:AICRM_HUANGYOUCAN_DB_PASSWORD:"
        "v1_0000000000000000_0123456789abcdef"
    )
    assert password.validate(reference) == reference


def test_direct_environment_access_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "aicrm_next" / "example.py"
    path.parent.mkdir(parents=True)
    path.write_text('import os\nVALUE = os.getenv("AICRM_EXAMPLE")\n', encoding="utf-8")

    violations = check_runtime_configuration_contract(
        root=tmp_path,
        migrated_paths=("aicrm_next/example.py",),
        definition_keys={"AICRM_EXAMPLE"},
    )

    assert {item.rule for item in violations} == {"direct_environment_access_forbidden"}


def test_runtime_setting_requires_config_definition(tmp_path: Path) -> None:
    path = tmp_path / "aicrm_next" / "example.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        'from aicrm_next.platform.shared.runtime_settings import runtime_setting\nVALUE = runtime_setting("AICRM_UNKNOWN")\n',
        encoding="utf-8",
    )

    violations = check_runtime_configuration_contract(
        root=tmp_path,
        migrated_paths=("aicrm_next/example.py",),
        definition_keys=set(),
    )

    assert [item.rule for item in violations] == ["runtime_setting_missing_definition"]


def test_startup_environment_setting_rejects_publishable_business_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / "aicrm_next" / "example.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from aicrm_next.platform.shared.runtime_settings import startup_environment_setting\n"
        'VALUE = startup_environment_setting("AICRM_BUSINESS_RULE")\n',
        encoding="utf-8",
    )

    violations = check_runtime_configuration_contract(
        root=tmp_path,
        migrated_paths=("aicrm_next/example.py",),
        definition_keys={"AICRM_BUSINESS_RULE"},
    )

    assert [item.rule for item in violations] == ["non_startup_environment_setting"]


def test_dynamic_runtime_setting_requires_declared_key_catalog(tmp_path: Path) -> None:
    path = tmp_path / "aicrm_next" / "example.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from aicrm_next.platform.shared.runtime_settings import runtime_bool\n"
        "def enabled(key: str) -> bool:\n"
        "    return runtime_bool(key)\n",
        encoding="utf-8",
    )

    violations = check_runtime_configuration_contract(
        root=tmp_path,
        migrated_paths=("aicrm_next/example.py",),
        definition_keys=set(),
    )

    assert [item.rule for item in violations] == ["dynamic_runtime_setting_without_declaration"]


def test_managed_runtime_setting_requires_cutover_registration(tmp_path: Path) -> None:
    path = tmp_path / "aicrm_next" / "example.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting\n"
        'VALUE = managed_runtime_setting("AICRM_UNREGISTERED")\n',
        encoding="utf-8",
    )

    violations = check_runtime_configuration_contract(
        root=tmp_path,
        migrated_paths=("aicrm_next/example.py",),
        definition_keys={"AICRM_UNREGISTERED"},
    )

    assert [item.rule for item in violations] == [
        "managed_runtime_setting_missing_cutover_registration"
    ]


def test_cutover_key_direct_environment_access_is_rejected_globally(
    tmp_path: Path,
) -> None:
    path = tmp_path / "aicrm_next" / "outside_migrated_context.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        'import os\nVALUE = os.getenv("WECOM_CORP_ID")\n',
        encoding="utf-8",
    )

    violations = check_runtime_configuration_contract(
        root=tmp_path,
        migrated_paths=(),
        definition_keys={"WECOM_CORP_ID"},
    )

    assert [item.rule for item in violations] == [
        "cutover_key_direct_environment_access"
    ]
