from __future__ import annotations

from pathlib import Path

from aicrm_next import integration_ports
from aicrm_next.integration_gateway.payment_adapters import build_wechat_pay_adapter
from aicrm_next.integration_gateway.questionnaire_adapters import build_wechat_oauth_adapter
from aicrm_next.integration_gateway.wecom_channel_entry_client import build_default_wecom_channel_entry_adapter


ROOT = Path(__file__).resolve().parents[1]


def test_integration_ports_preserve_concrete_adapter_bindings() -> None:
    assert integration_ports.build_wechat_pay_adapter is build_wechat_pay_adapter
    assert integration_ports.build_wechat_oauth_adapter is build_wechat_oauth_adapter
    assert integration_ports.build_default_wecom_channel_entry_adapter is build_default_wecom_channel_entry_adapter
    assert len(integration_ports.__all__) == len(set(integration_ports.__all__))
    assert set(integration_ports.__all__) == {
        name for name in integration_ports.__all__ if hasattr(integration_ports, name)
    }


def test_business_contexts_do_not_import_integration_gateway_implementations() -> None:
    offenders: list[str] = []
    package_root = ROOT / "aicrm_next"

    for path in package_root.rglob("*.py"):
        relative = path.relative_to(package_root)
        if len(relative.parts) < 2 or relative.parts[0] == "integration_gateway":
            continue
        source = path.read_text(encoding="utf-8")
        if "from aicrm_next.integration_gateway." in source or "import aicrm_next.integration_gateway." in source:
            offenders.append(relative.as_posix())

    assert offenders == []
