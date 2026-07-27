from __future__ import annotations

from typing import Any

from sqlalchemy import text

from .port import RateScopeCooldownRequest


def _required_scope_key(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("rate_scope_key is required")
    return normalized


class PostgresRateScopeCooldownRepository:
    """Sole SQL writer; caller-owned transactions are never committed here."""

    def persist_dbapi(self, executor: Any, *, request: RateScopeCooldownRequest) -> None:
        executor.execute(
            """
            INSERT INTO queue_rate_scope_cooldown (
                rate_scope_key, provider, corp_id, app_id, operation,
                blocked_until, reason, source_attempt_id, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (rate_scope_key) DO UPDATE
            SET blocked_until = GREATEST(
                    queue_rate_scope_cooldown.blocked_until,
                    EXCLUDED.blocked_until
                ),
                provider = EXCLUDED.provider,
                corp_id = EXCLUDED.corp_id,
                app_id = EXCLUDED.app_id,
                operation = EXCLUDED.operation,
                reason = EXCLUDED.reason,
                source_attempt_id = EXCLUDED.source_attempt_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                _required_scope_key(request.rate_scope_key),
                str(request.provider or ""),
                str(request.corp_id or ""),
                str(request.app_id or ""),
                str(request.operation or ""),
                request.blocked_until,
                str(request.reason or ""),
                str(request.source_attempt_id or ""),
            ),
        )

    def persist_sqlalchemy(self, executor: Any, *, request: RateScopeCooldownRequest) -> None:
        executor.execute(
            text(
                """
                INSERT INTO queue_rate_scope_cooldown (
                    rate_scope_key, provider, corp_id, app_id, operation,
                    blocked_until, reason, source_attempt_id, updated_at
                ) VALUES (
                    :rate_scope_key, :provider, :corp_id, :app_id, :operation,
                    CAST(:blocked_until AS timestamptz), :reason,
                    :source_attempt_id, CURRENT_TIMESTAMP
                )
                ON CONFLICT (rate_scope_key) DO UPDATE
                SET blocked_until = GREATEST(
                        queue_rate_scope_cooldown.blocked_until,
                        EXCLUDED.blocked_until
                    ),
                    provider = EXCLUDED.provider,
                    corp_id = EXCLUDED.corp_id,
                    app_id = EXCLUDED.app_id,
                    operation = EXCLUDED.operation,
                    reason = EXCLUDED.reason,
                    source_attempt_id = EXCLUDED.source_attempt_id,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "rate_scope_key": _required_scope_key(request.rate_scope_key),
                "provider": str(request.provider or ""),
                "corp_id": str(request.corp_id or ""),
                "app_id": str(request.app_id or ""),
                "operation": str(request.operation or ""),
                "blocked_until": request.blocked_until,
                "reason": str(request.reason or ""),
                "source_attempt_id": str(request.source_attempt_id or ""),
            },
        )


__all__ = ["PostgresRateScopeCooldownRepository"]
