from __future__ import annotations

from aicrm_next.engagement import send_content_media_repository_gateway as media_gateway
from aicrm_next.engagement.send_content import postgres_repo


def test_send_content_repository_uses_package_root_media_gateway(monkeypatch) -> None:
    captured: dict[str, str] = {}
    media_repository = object()

    def fake_repository(database_url: str):
        captured["database_url"] = database_url
        return media_repository

    monkeypatch.setattr(media_gateway, "_PostgresMediaLibraryRepository", fake_repository)

    repository = postgres_repo.PostgresSendContentRepository("postgresql://example.invalid/aicrm")

    assert repository._media_repo is media_repository
    assert captured == {"database_url": "postgresql://example.invalid/aicrm"}
    assert postgres_repo.build_send_content_media_repository.__module__ == "aicrm_next.engagement.send_content_media_repository_gateway"
