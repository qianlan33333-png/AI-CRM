from __future__ import annotations

from typing import Any

from aicrm_next.engagement.media_library.postgres_repo import PostgresMediaLibraryRepository as _PostgresMediaLibraryRepository


def build_send_content_media_repository(database_url: str) -> Any:
    """Compose Send Content reads with the canonical Media Library repository."""

    return _PostgresMediaLibraryRepository(database_url)
