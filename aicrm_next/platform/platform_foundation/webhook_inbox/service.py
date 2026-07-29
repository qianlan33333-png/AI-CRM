from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import WebhookInboxItem, WebhookInboxMetrics
from .repository import WebhookInboxRepository, build_webhook_inbox_repository


class WebhookInboxService:
    def __init__(self, repository: WebhookInboxRepository | None = None) -> None:
        self._repo = repository or build_webhook_inbox_repository()

    def ingest(self, **kwargs: Any) -> dict[str, Any]:
        return self._repo.ingest(**kwargs)

    def preview_due(self, *, provider: str, limit: int = 50) -> list[WebhookInboxItem]:
        return [WebhookInboxItem.from_row(row) for row in self._repo.preview_due(provider=provider, limit=limit)]

    def list_items(self, filters: dict[str, Any] | None = None, *, limit: int = 50, offset: int = 0) -> list[WebhookInboxItem]:
        return [
            WebhookInboxItem.from_row(row)
            for row in self._repo.list_items(filters or {}, limit=limit, offset=offset)
        ]

    def acquire_due(self, *, provider: str, limit: int = 50, locked_by: str = "webhook-inbox-worker") -> list[WebhookInboxItem]:
        return [
            WebhookInboxItem.from_row(row)
            for row in self._repo.acquire_due(provider=provider, limit=limit, locked_by=locked_by)
        ]

    def mark_succeeded(self, inbox_id: int) -> WebhookInboxItem | None:
        row = self._repo.mark_succeeded(inbox_id)
        return WebhookInboxItem.from_row(row) if row else None

    def mark_failed_retryable(
        self,
        inbox_id: int,
        *,
        error_code: str,
        error_message: str,
        next_retry_at: datetime | None = None,
    ) -> WebhookInboxItem | None:
        row = self._repo.mark_failed_retryable(
            inbox_id,
            error_code=error_code,
            error_message=error_message,
            next_retry_at=next_retry_at,
        )
        return WebhookInboxItem.from_row(row) if row else None

    def mark_failed_terminal(self, inbox_id: int, *, error_code: str, error_message: str) -> WebhookInboxItem | None:
        row = self._repo.mark_failed_terminal(inbox_id, error_code=error_code, error_message=error_message)
        return WebhookInboxItem.from_row(row) if row else None

    def mark_dead_letter(self, inbox_id: int, *, error_code: str = "", error_message: str = "") -> WebhookInboxItem | None:
        row = self._repo.mark_dead_letter(inbox_id, error_code=error_code, error_message=error_message)
        return WebhookInboxItem.from_row(row) if row else None

    def mark_retryable_now(self, inbox_id: int, *, reason: str = "") -> WebhookInboxItem | None:
        row = self._repo.mark_retryable_now(inbox_id, reason=reason)
        return WebhookInboxItem.from_row(row) if row else None

    def mark_ignored(self, inbox_id: int, *, reason: str = "") -> WebhookInboxItem | None:
        row = self._repo.mark_ignored(inbox_id, reason=reason)
        return WebhookInboxItem.from_row(row) if row else None

    def queue_metrics(self, filters: dict[str, Any] | None = None) -> WebhookInboxMetrics:
        return WebhookInboxMetrics.from_payload(self._repo.queue_metrics(filters or {}))
