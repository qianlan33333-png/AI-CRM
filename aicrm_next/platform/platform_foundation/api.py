from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .application import GetSystemHealthQuery
from .readiness import runtime_readiness_payload
from aicrm_next.platform.shared.runtime import runtime_route_map_state
from aicrm_next.platform.shared.resource_admission import resource_admission_snapshot
from aicrm_next.platform.shared.runtime_metrics import runtime_metric_snapshots

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return GetSystemHealthQuery()()


@router.get("/api/system/health", response_model=dict[str, Any])
def system_health() -> JSONResponse:
    payload = runtime_readiness_payload()
    payload["resource_admission"] = resource_admission_snapshot()
    payload.update(runtime_metric_snapshots())
    return JSONResponse(payload, status_code=int(payload["http_status"]))


@router.get("/api/system/runtime-route-map")
def runtime_route_map() -> dict:
    return runtime_route_map_state()
