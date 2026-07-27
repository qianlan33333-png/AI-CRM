from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .application import (
    get_run,
    get_strategy,
    list_strategies,
    list_strategy_runs,
    report_operation_cycle,
)
from .domain import OperationCycleConflictError
from .dto import OperationCycleSnapshotV1


router = APIRouter()
MAX_REPORT_BYTES = 512 * 1024
_REPORT_OPENAPI_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": OperationCycleSnapshotV1.model_json_schema(),
            }
        },
    }
}
_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Real-External-Call-Executed": "false",
}


def _json(payload: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(jsonable_encoder(payload), status_code=status_code, headers=_HEADERS)


def _error(error: str, *, status_code: int, **extra: Any) -> JSONResponse:
    return _json(
        {
            "ok": False,
            "error": error,
            "real_external_call_executed": False,
            **extra,
        },
        status_code=status_code,
    )


@router.post(
    "/api/operation-cycles/reports",
    name="report_operation_cycle_snapshot",
    openapi_extra=_REPORT_OPENAPI_BODY,
)
async def report_operation_cycle_snapshot(request: Request) -> JSONResponse:
    content_length = str(request.headers.get("content-length") or "").strip()
    if content_length:
        try:
            if int(content_length) > MAX_REPORT_BYTES:
                return _error("operation_cycle_report_too_large", status_code=413)
        except ValueError:
            return _error("invalid_content_length", status_code=400)

    idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
    if not idempotency_key:
        return _error("idempotency_key_required", status_code=400)
    if len(idempotency_key) > 200:
        return _error("idempotency_key_too_long", status_code=400)

    body = await request.body()
    if len(body) > MAX_REPORT_BYTES:
        return _error("operation_cycle_report_too_large", status_code=413)
    try:
        raw_payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error("operation_cycle_report_invalid_json", status_code=400)
    try:
        snapshot = OperationCycleSnapshotV1.model_validate(raw_payload)
    except ValidationError as exc:
        return _error(
            "operation_cycle_report_validation_failed",
            status_code=422,
            validation_errors=exc.errors(include_url=False, include_input=False),
        )

    context = getattr(request.state, "auth_context", None)
    reporter_id = str(getattr(context, "principal_id", "") or "").strip()
    client_id = str(getattr(context, "client_id", "") or "").strip()
    try:
        receipt = report_operation_cycle(
            snapshot,
            idempotency_key=idempotency_key,
            reporter_id=reporter_id,
            client_id=client_id,
        )
    except OperationCycleConflictError as exc:
        return _error(
            str(getattr(exc, "code", "") or str(exc) or "operation_cycle_report_conflict"),
            status_code=409,
        )
    return _json(receipt)


@router.get("/api/admin/operation-cycles/strategies", name="list_operation_cycle_strategies")
def list_operation_cycle_strategies(limit: int = 50, offset: int = 0) -> JSONResponse:
    return _json(list_strategies(limit=limit, offset=offset))


@router.get(
    "/api/admin/operation-cycles/strategies/{strategy_key}",
    name="get_operation_cycle_strategy",
)
def get_operation_cycle_strategy(strategy_key: str) -> JSONResponse:
    payload = get_strategy(strategy_key)
    if payload is None:
        return _error("operation_cycle_strategy_not_found", status_code=404)
    return _json(payload)


@router.get(
    "/api/admin/operation-cycles/strategies/{strategy_key}/runs",
    name="list_operation_cycle_strategy_runs",
)
def list_operation_cycle_strategy_runs(strategy_key: str, limit: int = 50, offset: int = 0) -> JSONResponse:
    return _json(list_strategy_runs(strategy_key, limit=limit, offset=offset))


@router.get("/api/admin/operation-cycles/runs/{run_key}", name="get_operation_cycle_run")
def get_operation_cycle_run(run_key: str) -> JSONResponse:
    payload = get_run(run_key)
    if payload is None:
        return _error("operation_cycle_run_not_found", status_code=404)
    return _json(payload)
