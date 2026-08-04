from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any


RuntimeMetricProvider = Callable[[], dict[str, Any]]
_LOCK = Lock()
_PROVIDERS: dict[str, RuntimeMetricProvider] = {}


def configure_runtime_metric_provider(name: str, provider: RuntimeMetricProvider) -> None:
    normalized = str(name or "").strip()
    if not normalized:
        raise ValueError("runtime metric provider name is required")
    with _LOCK:
        _PROVIDERS[normalized] = provider


def runtime_metric_snapshots() -> dict[str, Any]:
    with _LOCK:
        providers = dict(_PROVIDERS)
    snapshots: dict[str, Any] = {}
    for name, provider in sorted(providers.items()):
        try:
            snapshots[name] = dict(provider() or {})
        except Exception:
            snapshots[name] = {"status": "unavailable"}
    return snapshots


__all__ = ["configure_runtime_metric_provider", "runtime_metric_snapshots"]
