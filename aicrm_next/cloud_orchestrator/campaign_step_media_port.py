from __future__ import annotations

from typing import Any, Protocol


class CampaignStepMediaReferencePort(Protocol):
    """Public write port owned by cloud orchestration for campaign-step media references."""

    def clear_image_references_dbapi(
        self,
        executor: Any,
        *,
        image_library_id: int | str,
    ) -> int: ...


def build_campaign_step_media_reference_port() -> CampaignStepMediaReferencePort:
    from .campaign_step_media_repository import PostgresCampaignStepMediaReferenceRepository

    return PostgresCampaignStepMediaReferenceRepository()


__all__ = ["CampaignStepMediaReferencePort", "build_campaign_step_media_reference_port"]
