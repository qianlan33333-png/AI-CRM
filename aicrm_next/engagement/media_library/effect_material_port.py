from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class EphemeralImageMaterialRequest:
    name: str
    file_name: str
    content_type: str
    file_bytes: bytes
    description: str


class ImageLibraryEffectMaterialPort(Protocol):
    """Public owner port for durable images created inside another domain transaction."""

    def create_group_ops_ephemeral_image_in_session(
        self,
        session: Any,
        *,
        request: EphemeralImageMaterialRequest,
    ) -> int: ...


def build_image_library_effect_material_port() -> ImageLibraryEffectMaterialPort:
    from .effect_material_repository import PostgresImageLibraryEffectMaterialRepository

    return PostgresImageLibraryEffectMaterialRepository()


__all__ = [
    "EphemeralImageMaterialRequest",
    "ImageLibraryEffectMaterialPort",
    "build_image_library_effect_material_port",
]
