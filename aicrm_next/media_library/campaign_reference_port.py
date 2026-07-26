from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class CampaignMediaReferencePort(Protocol):
    def list_image_references_dbapi(
        self,
        executor: Any,
        *,
        image_library_id: int | str,
    ) -> list[dict[str, Any]]: ...

    def clear_image_references_dbapi(
        self,
        executor: Any,
        *,
        image_library_id: int | str,
    ) -> int: ...


CampaignMediaReferenceFactory = Callable[[], CampaignMediaReferencePort]
_FACTORY: CampaignMediaReferenceFactory | None = None


def configure_campaign_media_reference_port(factory: CampaignMediaReferenceFactory) -> None:
    global _FACTORY
    _FACTORY = factory


def build_campaign_media_reference_port() -> CampaignMediaReferencePort:
    if _FACTORY is None:
        raise RuntimeError("campaign_media_reference_port_not_configured")
    return _FACTORY()


__all__ = [
    "CampaignMediaReferenceFactory",
    "CampaignMediaReferencePort",
    "build_campaign_media_reference_port",
    "configure_campaign_media_reference_port",
]
