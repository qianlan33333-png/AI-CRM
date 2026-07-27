from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .errors import AdminReadModelError
from .projections import (
    ai_assistant_payload,
    config_payload,
    funnel_payload,
    jobs_payload,
    media_payload,
    page_row_count as page_row_count,
    products_payload,
    transactions_payload,
    wecom_tags_payload,
)
from .repo import AdminReadRepository, build_admin_read_repository


PayloadBuilder = Callable[[AdminReadRepository], dict[str, Any]]


def _production_unavailable_payload(exc: AdminReadModelError, *, page_name: str) -> dict[str, Any]:
    error_code = exc.error_code or "admin_read_model_query_failed"
    message = str(exc)
    return {
        "ok": False,
        "degraded": True,
        "source_status": "production_unavailable",
        "error_code": error_code,
        "page_error": f"生产{page_name}数据读取失败：{message}",
        "diagnostics": {
            "source_status": "production_unavailable",
            "error_code": error_code,
            "message": message,
        },
        "cards": [{"label": "生产数据", "value": "degraded", "description": "production_unavailable"}],
        "sections": [
            {
                "title": "数据读取状态",
                "headers": ["项目", "状态"],
                "rows": [
                    ["source_status", "production_unavailable"],
                    ["error_code", error_code],
                    ["message", message],
                ],
            }
        ],
    }


class _AdminPageQuery:
    page_name = "后台"

    def __init__(self, repository: AdminReadRepository | None = None) -> None:
        self._repository = repository

    def _repository_for_call(self) -> AdminReadRepository:
        return self._repository or build_admin_read_repository()

    def _run(self, builder: PayloadBuilder) -> dict[str, Any]:
        repo = self._repository_for_call()
        try:
            return builder(repo)
        except AdminReadModelError as exc:
            if repo.is_production:
                return _production_unavailable_payload(exc, page_name=self.page_name)
            raise


class GetAdminAiAssistantPageQuery(_AdminPageQuery):
    page_name = "AI 助手"

    def __call__(self) -> dict[str, Any]:
        return self._run(ai_assistant_payload)


class GetAdminFunnelPageQuery(_AdminPageQuery):
    page_name = "漏斗"

    def __call__(self) -> dict[str, Any]:
        return self._run(funnel_payload)


class GetAdminWeComTagsPageQuery(_AdminPageQuery):
    page_name = "企微标签"

    def __call__(self) -> dict[str, Any]:
        return self._run(wecom_tags_payload)


class GetAdminProductsPageQuery(_AdminPageQuery):
    page_name = "商品"

    def __call__(self) -> dict[str, Any]:
        return self._run(products_payload)


class GetAdminTransactionsPageQuery(_AdminPageQuery):
    page_name = "交易"

    def __call__(self) -> dict[str, Any]:
        return self._run(transactions_payload)


class GetAdminMediaPageQuery(_AdminPageQuery):
    page_name = "素材"

    def __call__(self, kind: str) -> dict[str, Any]:
        return self._run(lambda repo: media_payload(repo, kind))


class GetAdminJobsPageQuery(_AdminPageQuery):
    page_name = "任务"

    def __call__(self) -> dict[str, Any]:
        return self._run(jobs_payload)


class GetAdminConfigPageQuery(_AdminPageQuery):
    page_name = "配置"

    def __call__(self) -> dict[str, Any]:
        return self._run(config_payload)
