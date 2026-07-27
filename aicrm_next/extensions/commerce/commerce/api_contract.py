from __future__ import annotations

import logging

from fastapi import HTTPException

from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.safe_logging import safe_log_exception


LOGGER = logging.getLogger(__name__)


def raise_http(exc: Exception) -> None:
    if isinstance(exc, NotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ContractError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    safe_log_exception(LOGGER, "commerce api unexpected error", exc)
    raise HTTPException(
        status_code=500,
        detail={"error_code": "commerce_internal_error", "message": "internal commerce error"},
    ) from exc


def checkout_order_headers(*, order_create_executed: str = "false") -> dict[str, str]:
    return {
        "X-AICRM-Route-Owner": "ai_crm_next",
        "X-AICRM-Fallback-Used": "false",
        "X-AICRM-Real-External-Call-Executed": "false",
        "X-AICRM-Payment-Request-Executed": "false",
        "X-AICRM-Order-Create-Executed": order_create_executed,
    }
