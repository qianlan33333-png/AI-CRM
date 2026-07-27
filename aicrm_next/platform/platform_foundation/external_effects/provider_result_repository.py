from __future__ import annotations

import json
import hashlib
from typing import Any


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def encode_provider_result(value: Any) -> tuple[str, str]:
    payload = dict(value or {})
    serialized = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
    return serialized, hashlib.sha256(serialized.encode("utf-8")).hexdigest() if payload else ""


class ExternalEffectProviderResultRepositoryMixin:
    """Restricted handoff API intentionally absent from public attempt models."""

    def get_attempt_provider_result(self, attempt_id: str, *, job_id: int | None = None) -> dict[str, Any]:
        if job_id is None:
            statement = """
                SELECT provider_result_json FROM external_effect_attempt
                WHERE attempt_id = :attempt_id AND provider_result_consumed_at IS NULL
                LIMIT 1
            """
            parameters = {"attempt_id": str(attempt_id or "").strip()}
        else:
            statement = """
                SELECT provider_result_json FROM external_effect_attempt
                WHERE attempt_id = :attempt_id AND job_id = :job_id
                  AND provider_result_consumed_at IS NULL
                LIMIT 1
            """
            parameters = {
                "attempt_id": str(attempt_id or "").strip(),
                "job_id": int(job_id),
            }
        row = self._one(  # type: ignore[attr-defined]
            statement,
            parameters,
        )
        return _json_object((row or {}).get("provider_result_json"))

    def consume_attempt_provider_result(self, attempt_id: str, *, job_id: int) -> bool:
        row = self._write_one(  # type: ignore[attr-defined]
            """
            UPDATE external_effect_attempt
            SET provider_result_json = '{}'::jsonb, provider_result_consumed_at = CURRENT_TIMESTAMP
            WHERE attempt_id = :attempt_id AND job_id = :job_id
              AND provider_result_consumed_at IS NULL
            RETURNING id
            """,
            {"attempt_id": str(attempt_id or "").strip(), "job_id": int(job_id)},
        )
        return bool(row)


__all__ = ["ExternalEffectProviderResultRepositoryMixin", "encode_provider_result"]
