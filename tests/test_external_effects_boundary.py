from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.check_external_effects_boundary import check_external_effects_boundary, load_config


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _config(*, allowlist: list[dict] | None = None, effects: list[dict] | None = None) -> dict:
    return {
        "effects": effects
        if effects is not None
        else [
            {
                "effect_key": "demo.effect",
                "provider": "demo",
                "owner": "demo_context",
                "boundary": "existing_direct_client_allowlisted",
                "allowed_runtime": "real_requires_approval",
                "adapter_module": "aicrm_next/demo_context/api.py",
                "migration_target": "aicrm_next/integration_gateway/demo_client.py",
                "idempotency_required": True,
                "audit_required": True,
            }
        ],
        "temporary_allowlist": allowlist or [],
    }


def _write_config(path: Path, *, allowlist: list[dict] | None = None, effects: list[dict] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(_config(allowlist=allowlist, effects=effects), sort_keys=False), encoding="utf-8")


def test_external_effects_boundary_allows_approved_boundary_paths(tmp_path: Path) -> None:
    _write_config(tmp_path / "external_effects_registry.yml")
    _write(tmp_path / "aicrm_next" / "integration_gateway" / "foo.py", "import requests\nrequests.post('https://example.test')\n")
    _write(
        tmp_path / "aicrm_next" / "platform_foundation" / "external_effects" / "foo.py",
        "import httpx\nhttpx.Client()\n",
    )

    violations = check_external_effects_boundary(root=tmp_path, config_path=tmp_path / "external_effects_registry.yml")

    assert violations == []


def test_external_effects_boundary_blocks_business_direct_call_with_details(tmp_path: Path) -> None:
    _write_config(tmp_path / "external_effects_registry.yml")
    _write(tmp_path / "aicrm_next" / "demo_context" / "api.py", "import requests\nrequests.post('https://example.test')\n")

    violations = check_external_effects_boundary(root=tmp_path, config_path=tmp_path / "external_effects_registry.yml")

    assert len(violations) == 1
    violation = violations[0]
    assert violation.path.as_posix().endswith("aicrm_next/demo_context/api.py")
    assert violation.line == 2
    assert violation.rule == "direct_external_effect_call"
    assert violation.detected_callable == "requests.post"
    assert "aicrm_next.integration_gateway" in violation.suggestion
    assert "platform_foundation.external_effects" in violation.suggestion


@pytest.mark.parametrize(
    ("source", "detected"),
    [
        ("import requests as rq\nrq.post('https://example.test')\n", "requests.post"),
        ("from requests import post\npost('https://example.test')\n", "requests.post"),
        ("import httpx as hx\nhx.AsyncClient()\n", "httpx.AsyncClient"),
        ("from httpx import Client\nClient()\n", "httpx.Client"),
    ],
)
def test_external_effects_boundary_detects_aliases(tmp_path: Path, source: str, detected: str) -> None:
    _write_config(tmp_path / "external_effects_registry.yml")
    _write(tmp_path / "aicrm_next" / "demo_context" / "application.py", source)

    violations = check_external_effects_boundary(root=tmp_path, config_path=tmp_path / "external_effects_registry.yml")

    assert len(violations) == 1
    assert violations[0].detected_callable == detected
    assert violations[0].rule == "direct_external_effect_call"


def test_external_effects_boundary_allows_precise_temporary_allowlist(tmp_path: Path) -> None:
    allowlist = [
        {
            "path": "aicrm_next/demo_context/api.py",
            "rule": "direct_external_effect_call",
            "owner": "demo_context",
            "effect_key": "demo.effect",
            "reason": "Existing direct client predates checker.",
            "migration_target": "aicrm_next/integration_gateway/demo_client.py",
            "matches": ["response = requests.post("],
        }
    ]
    _write_config(tmp_path / "external_effects_registry.yml", allowlist=allowlist)
    _write(
        tmp_path / "aicrm_next" / "demo_context" / "api.py",
        "import requests\nresponse = requests.post(\n    'https://example.test'\n)\n",
    )

    violations = check_external_effects_boundary(root=tmp_path, config_path=tmp_path / "external_effects_registry.yml")

    assert violations == []


@pytest.mark.parametrize(
    "bad_entry",
    [
        {
            "path": "aicrm_next/demo_context/api.py",
            "rule": "direct_external_effect_call",
            "effect_key": "demo.effect",
            "reason": "Existing direct client predates checker.",
            "migration_target": "aicrm_next/integration_gateway/demo_client.py",
            "matches": ["response = requests.post("],
        },
        {
            "path": "aicrm_next/demo_context/api.py",
            "rule": "direct_external_effect_call",
            "owner": "demo_context",
            "effect_key": "demo.effect",
            "reason": "",
            "migration_target": "aicrm_next/integration_gateway/demo_client.py",
            "matches": ["response = requests.post("],
        },
        {
            "path": "aicrm_next/demo_context/api.py",
            "rule": "direct_external_effect_call",
            "owner": "demo_context",
            "effect_key": "demo.effect",
            "reason": "Existing direct client predates checker.",
            "matches": ["response = requests.post("],
        },
        {
            "path": "aicrm_next/demo_context/api.py",
            "rule": "direct_external_effect_call",
            "owner": "demo_context",
            "effect_key": "missing.effect",
            "reason": "Existing direct client predates checker.",
            "migration_target": "aicrm_next/integration_gateway/demo_client.py",
            "matches": ["response = requests.post("],
        },
        {
            "path": "aicrm_next/demo_context/**",
            "rule": "direct_external_effect_call",
            "owner": "demo_context",
            "effect_key": "demo.effect",
            "reason": "Existing direct client predates checker.",
            "migration_target": "aicrm_next/integration_gateway/demo_client.py",
            "matches": ["response = requests.post("],
        },
        {
            "path": "aicrm_next/demo_context/api.py",
            "rule": "direct_external_effect_call",
            "owner": "demo_context",
            "effect_key": "demo.effect",
            "reason": "Existing direct client predates checker.",
            "migration_target": "aicrm_next/integration_gateway/demo_client.py",
            "matches": ["requests"],
        },
    ],
)
def test_external_effects_boundary_rejects_imprecise_allowlist(tmp_path: Path, bad_entry: dict) -> None:
    _write_config(tmp_path / "external_effects_registry.yml", allowlist=[bad_entry])

    with pytest.raises(ValueError):
        load_config(tmp_path / "external_effects_registry.yml")


def test_external_effects_boundary_blocks_unmatched_call_in_allowlisted_file(tmp_path: Path) -> None:
    allowlist = [
        {
            "path": "aicrm_next/demo_context/api.py",
            "rule": "direct_external_effect_call",
            "owner": "demo_context",
            "effect_key": "demo.effect",
            "reason": "Existing direct client predates checker.",
            "migration_target": "aicrm_next/integration_gateway/demo_client.py",
            "matches": ["response = requests.post("],
        }
    ]
    _write_config(tmp_path / "external_effects_registry.yml", allowlist=allowlist)
    _write(
        tmp_path / "aicrm_next" / "demo_context" / "api.py",
        "import requests\nresponse = requests.post(\n    'https://example.test'\n)\nrequests.get('https://example.test')\n",
    )

    violations = check_external_effects_boundary(root=tmp_path, config_path=tmp_path / "external_effects_registry.yml")

    assert len(violations) == 1
    assert violations[0].detected_callable == "requests.get"


def test_external_effects_boundary_current_repository_passes() -> None:
    violations = check_external_effects_boundary()

    assert violations == []


def test_external_effects_registry_no_longer_allowlists_channel_entry_wecom_adapter() -> None:
    config = load_config(Path("docs/architecture/external_effects_registry.yml"))

    allowlisted_paths = {entry["path"] for entry in config["temporary_allowlist"]}
    wecom_effect = next(effect for effect in config["effects"] if effect["effect_key"] == "wecom.channel_entry.api")

    assert "aicrm_next/channel_entry/wecom_adapter.py" not in allowlisted_paths
    assert wecom_effect["boundary"] == "integration_gateway"
    assert wecom_effect["adapter_module"] == "aicrm_next/integration_gateway/wecom_channel_entry_client.py"
    assert wecom_effect["migration_target"] == "aicrm_next/integration_gateway/wecom_channel_entry_client.py"


def test_external_effects_registry_no_longer_allowlists_commerce_wechat_pay_client() -> None:
    config = load_config(Path("docs/architecture/external_effects_registry.yml"))

    allowlisted_paths = {entry["path"] for entry in config["temporary_allowlist"]}
    pay_effect = next(effect for effect in config["effects"] if effect["effect_key"] == "wechat_pay.commerce.api")

    assert "aicrm_next/extensions/commerce/commerce/wechat_pay_client.py" not in allowlisted_paths
    assert pay_effect["boundary"] == "integration_gateway"
    assert pay_effect["adapter_module"] == "aicrm_next/integration_gateway/wechat_pay_client.py"
    assert pay_effect["migration_target"] == "aicrm_next/integration_gateway/wechat_pay_client.py"


def test_external_effects_registry_no_longer_has_temporary_allowlist() -> None:
    config = load_config(Path("docs/architecture/external_effects_registry.yml"))

    shop_effect = next(effect for effect in config["effects"] if effect["effect_key"] == "wechat_shop.commerce.order_read")

    assert config["temporary_allowlist"] == []
    assert shop_effect["boundary"] == "integration_gateway"
    assert shop_effect["adapter_module"] == "aicrm_next/integration_gateway/wechat_shop_client.py"
    assert shop_effect["migration_target"] == "aicrm_next/integration_gateway/wechat_shop_client.py"
