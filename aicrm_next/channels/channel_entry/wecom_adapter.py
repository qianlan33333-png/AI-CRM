from __future__ import annotations

from typing import Any

from aicrm_next.integration_ports import (
    GuardedWeComAdapter,
    ProductionWeComAdapter,
    WeComAdapterBlocked,
    WeComApiError,
    build_default_wecom_channel_entry_adapter,
    missing_wecom_config,
    real_wecom_calls_enabled,
    wecom_adapter_diagnostics,
)


_adapter: Any | None = None


def _real_calls_enabled() -> bool:
    return real_wecom_calls_enabled()


def _build_default_adapter() -> Any:
    return build_default_wecom_channel_entry_adapter()


def set_wecom_adapter(adapter: Any) -> None:
    global _adapter
    _adapter = adapter


def get_wecom_adapter() -> Any:
    return _adapter if _adapter is not None else _build_default_adapter()


__all__ = [
    "GuardedWeComAdapter",
    "ProductionWeComAdapter",
    "WeComAdapterBlocked",
    "WeComApiError",
    "_build_default_adapter",
    "_real_calls_enabled",
    "get_wecom_adapter",
    "missing_wecom_config",
    "set_wecom_adapter",
    "wecom_adapter_diagnostics",
]
