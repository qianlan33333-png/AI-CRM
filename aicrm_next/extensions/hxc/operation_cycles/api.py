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
from .feature_flags import operation_context_v1_enabled
from .strategy_context import (
    create_strategy_change_proposal,
    decide_strategy_change_proposal,
    get_context_index,
    get_strategy_context,
    list_strategy_change_proposals,
)
from .strategy_context_dto import StrategyChangeDecisionRequest, StrategyChangeProposalV1


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


def _context_disabled() -> JSONResponse | None:
    if operation_context_v1_enabled():
        return None
    return _error("operation_context_v1_disabled", status_code=404)


def _context_exception(exc: Exception) -> JSONResponse:
    if isinstance(exc, OperationCycleConflictError):
        return _error(str(getattr(exc, "code", "") or exc), status_code=409)
    if isinstance(exc, LookupError):
        return _error(str(exc) or "operation_cycle_context_not_found", status_code=404)
    if isinstance(exc, ValidationError):
        return _error(
            "operation_cycle_context_validation_failed",
            status_code=422,
            validation_errors=exc.errors(include_url=False, include_input=False),
        )
    if isinstance(exc, ValueError):
        return _error(str(exc) or "operation_cycle_context_invalid", status_code=400)
    raise exc


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


@router.get("/api/operation-cycles/context-index", name="get_operation_cycle_context_index")
def get_operation_cycle_context_index(limit: int = 50, offset: int = 0) -> JSONResponse:
    if disabled := _context_disabled():
        return disabled
    return _json(get_context_index(limit=limit, offset=offset))


@router.get(
    "/api/operation-cycles/strategies/{strategy_key}/context",
    name="get_operation_cycle_strategy_context",
)
def get_operation_cycle_strategy_context(
    strategy_key: str,
    mode: str = "execution",
    limit: int = 3,
    offset: int = 0,
    date_from: str = "",
    date_to: str = "",
    execution_stage: str = "",
    review_status: str = "",
    delivery_status: str = "",
    metric_window: str = "",
    plan_id: str = "",
) -> JSONResponse:
    if disabled := _context_disabled():
        return disabled
    if mode not in {"execution", "retrospective", "optimization", "history"}:
        return _error("invalid_operation_context_mode", status_code=400)
    payload = get_strategy_context(
        strategy_key,
        mode=mode,
        limit=limit,
        offset=offset,
        filters={
            "date_from": date_from,
            "date_to": date_to,
            "execution_stage": execution_stage,
            "review_status": review_status,
            "delivery_status": delivery_status,
            "metric_window": metric_window,
            "plan_id": plan_id,
        },
    )
    if payload is None:
        return _error("operation_cycle_strategy_not_found", status_code=404)
    return _json(payload)


@router.post(
    "/api/operation-cycles/strategy-change-proposals",
    name="create_operation_cycle_strategy_change_proposal",
)
async def create_operation_cycle_strategy_change_proposal(request: Request) -> JSONResponse:
    if disabled := _context_disabled():
        return disabled
    idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
    if not idempotency_key:
        return _error("idempotency_key_required", status_code=400)
    try:
        proposal = StrategyChangeProposalV1.model_validate(await request.json())
        context = getattr(request.state, "auth_context", None)
        payload = create_strategy_change_proposal(
            proposal,
            idempotency_key=idempotency_key,
            submitted_by=str(getattr(context, "principal_id", "") or "").strip(),
            client_id=str(getattr(context, "client_id", "") or "").strip(),
        )
    except Exception as exc:
        return _context_exception(exc)
    return _json(payload, status_code=201)


@router.get(
    "/api/admin/operation-cycles/strategies/{strategy_key}/strategy-change-proposals",
    name="list_operation_cycle_strategy_change_proposals",
)
def list_operation_cycle_strategy_change_proposals(
    strategy_key: str,
    status: str = "",
    limit: int = 50,
    offset: int = 0,
) -> JSONResponse:
    if disabled := _context_disabled():
        return disabled
    try:
        return _json(
            list_strategy_change_proposals(
                strategy_key,
                status=status,
                limit=limit,
                offset=offset,
            )
        )
    except Exception as exc:
        return _context_exception(exc)


@router.post(
    "/api/admin/operation-cycles/strategy-change-proposals/{proposal_id}/decision",
    name="decide_operation_cycle_strategy_change_proposal",
)
async def decide_operation_cycle_strategy_change_proposal(
    proposal_id: str,
    request: Request,
) -> JSONResponse:
    if disabled := _context_disabled():
        return disabled
    try:
        decision = StrategyChangeDecisionRequest.model_validate(await request.json())
        context = getattr(request.state, "auth_context", None)
        payload = decide_strategy_change_proposal(
            proposal_id,
            decision,
            decided_by=str(getattr(context, "principal_id", "") or "admin").strip(),
        )
    except Exception as exc:
        return _context_exception(exc)
    return _json(payload)
