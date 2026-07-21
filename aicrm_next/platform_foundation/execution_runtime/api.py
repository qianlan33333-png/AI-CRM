from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .read_model import ExecutionRuntimeReadModel


ROUTE_OWNER = "aicrm_next.platform_foundation"
router = APIRouter()


def _response(payload: dict, *, status_code: int = 200) -> JSONResponse:
    body = {"route_owner": ROUTE_OWNER, **payload}
    return JSONResponse(
        body,
        status_code=status_code,
        headers={"X-AICRM-Route-Owner": ROUTE_OWNER},
    )


@router.get("/api/admin/execution-runtime")
def get_execution_runtime() -> JSONResponse:
    try:
        payload = ExecutionRuntimeReadModel().runtime_snapshot()
    except Exception as exc:
        return _response(
            {
                "ok": False,
                "error": "execution_runtime_unavailable",
                "error_class": exc.__class__.__name__,
                "pii_in_output": False,
                "secrets_in_output": False,
            },
            status_code=503,
        )
    return _response(payload)


@router.get("/api/admin/executions/{execution_id}")
def get_execution_timeline(execution_id: str) -> JSONResponse:
    normalized = str(execution_id or "").strip()
    if not normalized.startswith("exe_") or len(normalized) > 100:
        raise HTTPException(status_code=404, detail="execution_not_found")
    try:
        payload = ExecutionRuntimeReadModel().execution_timeline(normalized)
    except Exception as exc:
        return _response(
            {
                "ok": False,
                "execution_id": normalized,
                "error": "execution_timeline_unavailable",
                "error_class": exc.__class__.__name__,
                "pii_in_output": False,
                "secrets_in_output": False,
            },
            status_code=503,
        )
    if payload is None:
        raise HTTPException(status_code=404, detail="execution_not_found")
    return _response({"ok": True, **payload})


__all__ = ["router"]
