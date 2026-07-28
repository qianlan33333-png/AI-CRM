from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .application import GetAiAssistContractQuery
from .external_campaigns import create_external_campaigns_response
from .external_campaigns import create_direct_wecom_private_send_response
from .external_campaigns import get_external_campaign_status_response
from .campaign_preparations import (
    CampaignPreparationError,
    commit_campaign_preparation,
    create_campaign_preparation,
    get_campaign_preparation,
)
from .campaign_preparations_dto import (
    CommitCampaignPreparationRequestV1,
    CreateCampaignPreparationRequestV1,
)
from .feature_flags import campaign_preparation_v1_enabled, dynamic_miniprogram_card_v1_enabled

router = APIRouter()
_CAMPAIGN_PREPARATION_MAX_BYTES = 16 * 1024 * 1024
_MACHINE_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Real-External-Call-Executed": "false",
}


def _machine_json(payload: dict, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(jsonable_encoder(payload), status_code=status_code, headers=_MACHINE_HEADERS)


def _preparation_disabled() -> JSONResponse | None:
    if campaign_preparation_v1_enabled() and dynamic_miniprogram_card_v1_enabled():
        return None
    return _machine_json({"ok": False, "error": "campaign_preparation_v1_disabled"}, status_code=404)


def _preparation_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, CampaignPreparationError):
        return _machine_json({"ok": False, "error": exc.code}, status_code=exc.status_code)
    if isinstance(exc, ValidationError):
        return _machine_json(
            {
                "ok": False,
                "error": "campaign_preparation_validation_failed",
                "validation_errors": exc.errors(include_url=False, include_input=False),
            },
            status_code=422,
        )
    raise exc


def _actor_id(request: Request) -> str:
    context = getattr(request.state, "auth_context", None)
    return str(getattr(context, "principal_id", "") or "campaign_agent").strip()


@router.get("/api/admin/ai-assist/contract")
def ai_assist_contract() -> dict:
    return GetAiAssistContractQuery()()


@router.post("/api/ai-assist/external/campaigns")
async def create_external_campaigns(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "invalid_json", "route_owner": "ai_crm_next"},
            status_code=400,
        )
    return create_external_campaigns_response(payload)


@router.get("/api/ai-assist/external/campaigns/{campaign_code}")
async def get_external_campaign(campaign_code: str, request: Request):
    return get_external_campaign_status_response(campaign_code)


@router.post("/api/ai-assist/external/campaign-preparations")
async def create_external_campaign_preparation(request: Request) -> JSONResponse:
    if disabled := _preparation_disabled():
        return disabled
    body = await request.body()
    if len(body) > _CAMPAIGN_PREPARATION_MAX_BYTES:
        return _machine_json({"ok": False, "error": "campaign_preparation_too_large"}, status_code=413)
    try:
        model = CreateCampaignPreparationRequestV1.model_validate_json(body)
        header_key = str(request.headers.get("Idempotency-Key") or "").strip()
        if header_key and header_key != model.idempotency_key:
            raise CampaignPreparationError("idempotency_key_mismatch", status_code=409)
        result = create_campaign_preparation(model, actor_id=_actor_id(request))
    except Exception as exc:
        return _preparation_error(exc)
    return _machine_json(result, status_code=200 if result.get("idempotent_existing") else 201)


@router.get("/api/ai-assist/external/campaign-preparations/{preparation_id}")
def get_external_campaign_preparation(preparation_id: str) -> JSONResponse:
    if disabled := _preparation_disabled():
        return disabled
    result = get_campaign_preparation(preparation_id)
    if result is None:
        return _machine_json({"ok": False, "error": "preparation_not_found"}, status_code=404)
    return _machine_json(result)


@router.post("/api/ai-assist/external/campaign-preparations/{preparation_id}/commit")
async def commit_external_campaign_preparation(preparation_id: str, request: Request) -> JSONResponse:
    if disabled := _preparation_disabled():
        return disabled
    try:
        model = CommitCampaignPreparationRequestV1.model_validate(await request.json())
        result = commit_campaign_preparation(
            preparation_id,
            model,
            actor_id=_actor_id(request),
        )
    except Exception as exc:
        return _preparation_error(exc)
    return _machine_json(result, status_code=200 if result.get("status") == "reused" else 201)


@router.post("/api/internal/direct-send/wecom-private")
async def create_internal_direct_wecom_private_send(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "invalid_json", "route_owner": "ai_crm_next"},
            status_code=400,
        )
    return create_direct_wecom_private_send_response(
        payload,
        source="internal_direct_send_api",
    )


@router.post("/api/admin/direct-send/wecom-private")
async def create_admin_direct_wecom_private_send(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "invalid_json", "route_owner": "ai_crm_next"},
            status_code=400,
        )
    return create_direct_wecom_private_send_response(
        payload,
        source="admin_direct_send_api",
    )
