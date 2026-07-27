from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .application import (
    data_health_check_detail,
    data_health_checks,
    data_health_summary,
)


router = APIRouter()


@router.get("/api/admin/data-health/summary")
def api_data_health_summary() -> JSONResponse:
    return _json(data_health_summary())


@router.get("/api/admin/data-health/checks")
def api_data_health_checks() -> JSONResponse:
    return _json(data_health_checks())


@router.get("/api/admin/data-health/checks/{check_id}")
def api_data_health_check_detail(check_id: str) -> JSONResponse:
    return _json(data_health_check_detail(check_id))


def _json(payload: dict) -> JSONResponse:
    return JSONResponse(payload, status_code=int(payload.get("status_code") or 200))
