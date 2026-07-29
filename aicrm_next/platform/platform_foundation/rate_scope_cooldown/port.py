from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class RateScopeCooldownRequest:
    rate_scope_key: str
    blocked_until: datetime
    provider: str = ""
    corp_id: str = ""
    app_id: str = ""
    operation: str = ""
    reason: str = "provider_429"
    source_attempt_id: str = ""


class RateScopeCooldownPort(Protocol):
    """Canonical writer boundary for provider rate-scope cooldown state."""

    def persist_dbapi(self, executor: Any, *, request: RateScopeCooldownRequest) -> None: ...

    def persist_sqlalchemy(self, executor: Any, *, request: RateScopeCooldownRequest) -> None: ...


def build_rate_scope_cooldown_port() -> RateScopeCooldownPort:
    from .repository import PostgresRateScopeCooldownRepository

    return PostgresRateScopeCooldownRepository()


__all__ = ["RateScopeCooldownPort", "RateScopeCooldownRequest", "build_rate_scope_cooldown_port"]
