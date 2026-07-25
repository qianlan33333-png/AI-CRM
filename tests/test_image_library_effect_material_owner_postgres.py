from __future__ import annotations

import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from aicrm_next.media_library.effect_material_port import (
    EphemeralImageMaterialRequest,
    build_image_library_effect_material_port,
)


def test_group_ops_ephemeral_image_owner_persists_in_caller_transaction(next_pg_schema) -> None:
    database_url = os.environ["DATABASE_URL"]
    if database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgresql://") :]
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            material_id = build_image_library_effect_material_port().create_group_ops_ephemeral_image_in_session(
                session,
                request=EphemeralImageMaterialRequest(
                    name="Group Ops poster execution-pg",
                    file_name="poster-pg.png",
                    content_type="image/png",
                    file_bytes=b"postgres-image-bytes",
                    description="durable source for execution-pg:poster",
                ),
            )
            row = session.execute(
                text(
                    """
                    SELECT source, category, file_name, mime_type, file_size, data_base64
                    FROM image_library
                    WHERE id = :material_id
                    """
                ),
                {"material_id": material_id},
            ).mappings().one()
            session.rollback()
    finally:
        engine.dispose()

    assert dict(row) == {
        "source": "group_ops_effect_graph",
        "category": "group_ops_ephemeral",
        "file_name": "poster-pg.png",
        "mime_type": "image/png",
        "file_size": len(b"postgres-image-bytes"),
        "data_base64": "cG9zdGdyZXMtaW1hZ2UtYnl0ZXM=",
    }
