from __future__ import annotations

from typing import Any

from .projection import PushCenterProjectionService


def _text(value: Any) -> str:
    return str(value or "").strip()


class PushCenterRepository:
    def __init__(self, service: PushCenterProjectionService | None = None) -> None:
        self._service = service or PushCenterProjectionService()

    def list_jobs(self, filters: dict[str, Any] | None = None, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        return self._service.list_projections(filters or {}, limit=limit, offset=offset)

    def list_jobs_with_summary(
        self,
        filters: dict[str, Any] | None = None,
        *,
        limit: int = 50,
        offset: int = 0,
        cursor: str = "",
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any], list[dict[str, Any]], str, bool]:
        return self._service.query_projections(filters or {}, limit=limit, offset=offset, cursor=cursor)

    def get_job(self, job_id: int | str) -> dict[str, Any] | None:
        return self._service.get_projection(str(job_id))

    def list_attempts(self, job_id: int | str) -> list[dict[str, Any]]:
        job = self.get_job(job_id)
        if not job:
            return []
        linked = job.get("linked_records") if isinstance(job.get("linked_records"), dict) else {}
        return list(linked.get("external_effect_attempts") or [])

    def counts(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._service.counts(filters or {})

    def sections(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return self._service.sections(filters or {})

    def summary(self, filters: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return self._service.summary(filters or {})
