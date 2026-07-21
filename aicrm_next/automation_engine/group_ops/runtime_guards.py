from __future__ import annotations

import time

from aicrm_next.shared.errors import ApplicationError

from .domain import clean_text


class ConflictError(ApplicationError):
    status_code = 409


_WEBHOOK_RATE_BUCKET: dict[str, list[float]] = {}


def assert_webhook_rate_limit(
    webhook_key: str,
    *,
    limit: int = 60,
    window_seconds: int = 60,
) -> None:
    now = time.time()
    bucket_key = clean_text(webhook_key)
    items = [ts for ts in _WEBHOOK_RATE_BUCKET.get(bucket_key, []) if now - ts <= window_seconds]
    if len(items) >= limit:
        raise ConflictError("webhook rate limit exceeded")
    items.append(now)
    _WEBHOOK_RATE_BUCKET[bucket_key] = items


__all__ = ["ConflictError", "assert_webhook_rate_limit"]
