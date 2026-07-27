from __future__ import annotations

from typing import Any

from aicrm_next.admin_read_model.application import (
    GetAdminAiAssistantPageQuery,
    GetAdminConfigPageQuery,
    GetAdminFunnelPageQuery,
    GetAdminJobsPageQuery,
    GetAdminMediaPageQuery,
    GetAdminProductsPageQuery,
    GetAdminTransactionsPageQuery,
    GetAdminWeComTagsPageQuery,
)


def ai_assistant_payload() -> dict[str, Any]:
    return GetAdminAiAssistantPageQuery()()


def funnel_payload() -> dict[str, Any]:
    return GetAdminFunnelPageQuery()()


def wecom_tags_payload() -> dict[str, Any]:
    return GetAdminWeComTagsPageQuery()()


def products_payload() -> dict[str, Any]:
    return GetAdminProductsPageQuery()()


def transactions_payload() -> dict[str, Any]:
    return GetAdminTransactionsPageQuery()()


def media_payload(kind: str) -> dict[str, Any]:
    return GetAdminMediaPageQuery()(kind)


def jobs_payload() -> dict[str, Any]:
    return GetAdminJobsPageQuery()()


def config_payload() -> dict[str, Any]:
    return GetAdminConfigPageQuery()()
