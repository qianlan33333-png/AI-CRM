from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .application import GetSystemHealthQuery
from .readiness import runtime_readiness_payload
from aicrm_next.platform.shared.runtime import runtime_route_map_state

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return GetSystemHealthQuery()()


@router.get("/api/system/health", response_model=dict[str, Any])
def system_health() -> JSONResponse:
    payload = runtime_readiness_payload()
    return JSONResponse(payload, status_code=int(payload["http_status"]))


@router.get("/api/system/runtime-route-map")
def runtime_route_map() -> dict:
    return runtime_route_map_state()
