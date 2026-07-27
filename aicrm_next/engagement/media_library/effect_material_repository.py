from __future__ import annotations

import base64
from typing import Any

from sqlalchemy import text

from .effect_material_port import EphemeralImageMaterialRequest


class PostgresImageLibraryEffectMaterialRepository:
    """Canonical image-library writer for material rows created by effect graphs."""

    def create_group_ops_ephemeral_image_in_session(
        self,
        session: Any,
        *,
        request: EphemeralImageMaterialRequest,
    ) -> int:
        file_bytes = bytes(request.file_bytes)
        if not file_bytes:
            raise ValueError("ephemeral image material requires file_bytes")
        row = (
            session.execute(
                text(
                    """
                    INSERT INTO image_library (
                        name, file_name, source, source_url, data_base64, mime_type,
                        file_size, enabled, description, category, created_at, updated_at
                    ) VALUES (
                        :name, :file_name, 'group_ops_effect_graph', '', :data_base64,
                        :mime_type, :file_size, TRUE,
                        :description, 'group_ops_ephemeral', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    RETURNING id
                    """
                ),
                {
                    "name": str(request.name or "Group Ops material").strip(),
                    "file_name": str(request.file_name or "group-ops-image").strip(),
                    "data_base64": base64.b64encode(file_bytes).decode("ascii"),
                    "mime_type": str(request.content_type or "image/png").strip(),
                    "file_size": len(file_bytes),
                    "description": str(request.description or "").strip(),
                },
            )
            .mappings()
            .one()
        )
        return int(row["id"])


__all__ = ["PostgresImageLibraryEffectMaterialRepository"]
