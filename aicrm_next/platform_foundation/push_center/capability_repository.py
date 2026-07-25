from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from aicrm_next.shared.db_session import get_engine
from aicrm_next.shared.repository_provider import RepositoryProviderError
from aicrm_next.shared.runtime import production_repository_required
from aicrm_next.shared.runtime_settings import environment_fallback


class PushCapabilitySettingRepository:
    """Strict runtime-setting read boundary for push capability consumers."""

    def __init__(self, *, engine: Engine | None = None) -> None:
        self._engine = engine

    def get_values(self, keys: list[str]) -> dict[str, str]:
        normalized_keys = list(dict.fromkeys(str(key or "").strip() for key in keys if str(key or "").strip()))
        if not normalized_keys:
            return {}
        if self._engine is None and not production_repository_required():
            return {key: environment_fallback(key).strip() for key in normalized_keys}
        try:
            engine = self._engine or get_engine()
            with engine.connect() as conn:
                rows: dict[str, str] = {}
                for key in normalized_keys:
                    row = conn.execute(
                        text("SELECT value FROM app_settings WHERE key = :key"),
                        {"key": key},
                    ).mappings().first()
                    rows[key] = str((row or {}).get("value") or environment_fallback(key)).strip()
        except (AttributeError, RuntimeError, SQLAlchemyError) as exc:
            raise RepositoryProviderError("push capability setting source unavailable") from exc
        return rows


__all__ = ["PushCapabilitySettingRepository"]
