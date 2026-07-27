from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from aicrm_next.shared.sync_request import read_request_body

from .application import (
    callback_config,
    diagnose_channel_runtime,
    dry_run_channel_entry,
    encrypted_success_reply,
    generate_channel_qrcode,
    repair_channel_entry,
    verify_callback_echostr,
)
from .callback_ingress import ingest_wecom_external_contact_callback
from .schemas import (
    DiagnoseChannelRuntimeQuery,
    GenerateChannelQrCodeCommand,
    ProcessChannelEntryCommand,
    RepairChannelEntryCommand,
)

callback_router = APIRouter()
router = APIRouter()


def _handle_callback(request: Request, body: bytes = b"") -> Response:
    query = {key: str(value) for key, value in request.query_params.items()}
    try:
        if request.method == "GET":
            return Response(verify_callback_echostr(query), media_type="text/plain")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        ingest_wecom_external_contact_callback(
            query=query,
            headers=dict(request.headers),
            body=body,
            route=str(request.url.path),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="webhook ingress unavailable") from exc
    return Response(encrypted_success_reply(query), media_type="application/xml")


@callback_router.api_route("/wecom/external-contact/callback", methods=["GET", "POST", "OPTIONS", "HEAD"])
def external_contact_callback(request: Request) -> Response:
    if request.method in {"OPTIONS", "HEAD"}:
        return Response("", media_type="text/plain")
    body = b"" if request.method == "GET" else read_request_body(request)
    return _handle_callback(request, body=body)


@callback_router.api_route("/api/wecom/events", methods=["GET", "POST", "OPTIONS", "HEAD"])
def wecom_events(request: Request) -> Response:
    if request.method in {"OPTIONS", "HEAD"}:
        return Response("", media_type="text/plain")
    body = b"" if request.method == "GET" else read_request_body(request)
    return _handle_callback(request, body=body)


router.include_router(callback_router)


@router.get("/api/admin/channels/runtime-diagnosis")
def runtime_diagnosis(scene_value: str = "") -> dict:
    return diagnose_channel_runtime(DiagnoseChannelRuntimeQuery(scene_value=scene_value))


@router.get("/api/admin/channels/{channel_id:int}/runtime-diagnosis")
def runtime_diagnosis_by_channel(channel_id: int) -> dict:
    return diagnose_channel_runtime(DiagnoseChannelRuntimeQuery(channel_id=int(channel_id)))


@router.post("/api/admin/channels/{channel_id:int}/qrcode/generate")
def generate_qrcode(channel_id: int, payload: dict | None = None) -> dict:
    payload = payload or {}
    try:
        result = generate_channel_qrcode(
            GenerateChannelQrCodeCommand(
                channel_id=int(channel_id),
                scene_value=str(payload.get("scene_value") or payload.get("state") or "").strip(),
                owner_staff_id=str(payload.get("owner_staff_id") or "").strip(),
                operator_id=str(payload.get("operator_id") or "").strip(),
                skip_verify=payload.get("skip_verify") if isinstance(payload.get("skip_verify"), bool) else None,
            )
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    return result


@router.post("/api/admin/channels/runtime-diagnosis/dry-run")
def runtime_diagnosis_dry_run(payload: dict) -> dict:
    corp_id = str(payload.get("corp_id") or payload.get("ToUserName") or callback_config().get("corp_id") or "").strip()
    result = dry_run_channel_entry(
        ProcessChannelEntryCommand(
            external_contact_id=str(payload.get("external_userid") or payload.get("external_contact_id") or "dry_run_external_userid").strip(),
            payload_json={"State": str(payload.get("state") or payload.get("scene_value") or "").strip(), "WelcomeCode": str(payload.get("welcome_code") or ""), "ToUserName": corp_id},
            follow_user_userid=str(payload.get("follow_user_userid") or "").strip(),
            event_action=str(payload.get("change_type") or "add_external_contact").strip(),
            send_welcome_message=bool(payload.get("welcome_code") or payload.get("welcome_code_present")),
            dry_run=True,
        )
    )
    return {"ok": True, "planned_actions": result, "source": "aicrm_next.channels.channel_entry"}


@router.post("/api/admin/channels/repair-entry")
def repair_entry(payload: dict) -> JSONResponse:
    result = repair_channel_entry(
        RepairChannelEntryCommand(
            event_log_id=int(payload.get("event_log_id") or 0) or None,
            external_userid=str(payload.get("external_userid") or payload.get("external_contact_id") or "").strip(),
            scene_value=str(payload.get("scene_value") or payload.get("state") or "").strip(),
            corp_id=str(payload.get("corp_id") or payload.get("ToUserName") or "").strip(),
        )
    )
    return JSONResponse(jsonable_encoder({"ok": bool(result.get("handled")), "result": result, "source": "aicrm_next.channels.channel_entry"}))
