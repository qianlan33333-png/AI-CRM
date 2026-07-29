from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .application import (
    delivery_lineage_daily_metrics,
    get_delivery_lineage,
    list_delivery_lineage,
    list_delivery_lineage_by_trace,
    list_delivery_lineage_by_unionid,
)


router = APIRouter()


@router.get("/api/admin/delivery-lineage")
def api_delivery_lineage_list(limit: int = 50, offset: int = 0) -> JSONResponse:
    return _json(list_delivery_lineage(limit=limit, offset=offset))


@router.get("/api/admin/delivery-lineage/metrics/daily")
def api_delivery_lineage_daily_metrics(days: int = 7) -> JSONResponse:
    return _json(delivery_lineage_daily_metrics(days=days))


@router.get("/api/admin/delivery-lineage/by-unionid/{unionid}")
def api_delivery_lineage_by_unionid(unionid: str, limit: int = 50, offset: int = 0) -> JSONResponse:
    return _json(list_delivery_lineage_by_unionid(unionid, limit=limit, offset=offset))


@router.get("/api/admin/delivery-lineage/by-trace/{trace_id}")
def api_delivery_lineage_by_trace(trace_id: str, limit: int = 50, offset: int = 0) -> JSONResponse:
    return _json(list_delivery_lineage_by_trace(trace_id, limit=limit, offset=offset))


@router.get("/api/admin/delivery-lineage/{lineage_id}")
def api_delivery_lineage_detail(lineage_id: str) -> JSONResponse:
    return _json(get_delivery_lineage(lineage_id))


def _json(payload: dict) -> JSONResponse:
    return JSONResponse(payload, status_code=int(payload.get("status_code") or 200))
