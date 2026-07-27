from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from aicrm_next.platform.shared.typing import JsonDict


class AudienceTargetQueryPort(Protocol):
    def rows_for_package(self, package_id: int) -> list[JsonDict]: ...


AudienceTargetQueryFactory = Callable[[], AudienceTargetQueryPort]
_FACTORY: AudienceTargetQueryFactory | None = None


def configure_audience_target_query(factory: AudienceTargetQueryFactory) -> None:
    global _FACTORY
    _FACTORY = factory


def build_audience_target_query() -> AudienceTargetQueryPort:
    if _FACTORY is None:
        raise RuntimeError("audience_target_query_not_configured")
    return _FACTORY()


__all__ = [
    "AudienceTargetQueryFactory",
    "AudienceTargetQueryPort",
    "build_audience_target_query",
    "configure_audience_target_query",
]
