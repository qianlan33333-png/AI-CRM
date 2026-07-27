from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_shell_contract import shell_context

from .application import (
    DEFAULT_TRANSFER_WELCOME_MSG,
    OwnerMigrationCommand,
    build_owner_migration_service,
    clean_text,
    owner_migration_template_xlsx,
    query_wecom_transfer_result,
)

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "app" / "admin_console" / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


def _command_from_payload(payload: dict[str, Any], *, execute: bool) -> OwnerMigrationCommand:
    include_wecom_transfer = payload.get("include_wecom_transfer")
    if include_wecom_transfer is None:
        include_wecom_transfer = payload.get("perform_wecom_transfer")
    transfer_welcome_msg = payload.get("transfer_welcome_msg")
    if transfer_welcome_msg is None:
        transfer_welcome_msg = payload.get("transfer_success_msg")
    return OwnerMigrationCommand(
        source_owner_userid=clean_text(payload.get("source_owner_userid") or payload.get("source")),
        target_owner_userid=clean_text(payload.get("target_owner_userid") or payload.get("target")),
        operator=clean_text(payload.get("operator")),
        transfer_success_msg=clean_text(transfer_welcome_msg),
        batch_size=_safe_int(payload.get("batch_size"), default=100),
        perform_wecom_transfer=_safe_bool(include_wecom_transfer, default=True),
        execute=execute,
        confirm=bool(payload.get("confirm")),
        scope_type=clean_text(payload.get("scope_type")) or None,
        session_id=clean_text(payload.get("session_id")),
        preview_token=clean_text(payload.get("preview_token")),
        preview_hash=clean_text(payload.get("preview_hash")),
        confirm_phrase=clean_text(payload.get("confirm_phrase")),
    )


def _status_code(result: dict[str, Any]) -> int:
    return int(result.pop("status_code", 200) or 200)


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    normalized = clean_text(value).lower()
    if normalized in {"0", "false", "no", "off"}:
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    return bool(value)


def _operator_from_request(request: Request) -> str:
    state_user = getattr(getattr(request, "state", None), "admin_user", None)
    if isinstance(state_user, dict):
        return clean_text(state_user.get("userid") or state_user.get("username") or state_user.get("display_name")) or "crm_console"
    if state_user:
        return clean_text(state_user) or "crm_console"
    return "crm_console"


def _excel_response(content: bytes, filename: str) -> Response:
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _page_context(request: Request, *, result: dict[str, Any] | None = None, form: dict[str, Any] | None = None) -> dict[str, Any]:
    context = shell_context(
        request=request,
        page_title="客户负责人迁移",
        page_summary="批量迁移 CRM 本地客户负责人。",
        active_endpoint="api.admin_owner_migration_page",
    )
    context["owner_migration_form"] = {
        "source_owner_userid": clean_text((form or {}).get("source_owner_userid")) or "mengyu",
        "target_owner_userid": clean_text((form or {}).get("target_owner_userid")) or "huangyoucan",
        "operator": clean_text((form or {}).get("operator")) or "crm_console",
        "transfer_success_msg": clean_text((form or {}).get("transfer_success_msg")) or "您好，后续将由新的服务同事继续为您服务。",
        "perform_wecom_transfer": clean_text((form or {}).get("perform_wecom_transfer")) != "0",
    }
    context["owner_migration_result"] = result or {}
    if result and not result.get("ok"):
        context["page_error"] = clean_text(result.get("error"))
    elif result and result.get("mode") == "execute":
        context["page_notice"] = "迁移已执行"
    return context


@router.get("/admin/owner-migration", name="api.admin_owner_migration_page")
def admin_owner_migration_page(request: Request) -> Response:
    form = {
        "source_owner_userid": request.query_params.get("source_owner_userid", "mengyu"),
        "target_owner_userid": request.query_params.get("target_owner_userid", "huangyoucan"),
        "operator": request.query_params.get("operator", "crm_console"),
        "transfer_success_msg": request.query_params.get("transfer_success_msg", "您好，后续将由新的服务同事继续为您服务。"),
    }
    should_preview = bool(request.query_params.get("source_owner_userid") or request.query_params.get("target_owner_userid"))
    result = None
    if should_preview:
        result = build_owner_migration_service().run(_command_from_payload(form, execute=False))
    return templates.TemplateResponse(request, "admin_console/owner_migration.html", _page_context(request, result=result, form=form))


@router.post("/admin/owner-migration", name="api.admin_owner_migration_action")
async def admin_owner_migration_action(request: Request) -> Response:
    form_data = await request.form()
    form = dict(form_data)
    action = clean_text(form.get("action")) or "preview"
    result = build_owner_migration_service().run(
        OwnerMigrationCommand(
            source_owner_userid=clean_text(form.get("source_owner_userid")),
            target_owner_userid=clean_text(form.get("target_owner_userid")),
            operator=clean_text(form.get("operator")),
            transfer_success_msg=clean_text(form.get("transfer_success_msg")),
            perform_wecom_transfer=clean_text(form.get("perform_wecom_transfer")) != "0",
            execute=action == "execute",
            confirm=clean_text(form.get("confirm")) in {"1", "true", "on", "yes"},
        )
    )
    return templates.TemplateResponse(request, "admin_console/owner_migration.html", _page_context(request, result=result, form=form))


@router.post("/api/admin/owner-migration/preview")
async def preview_owner_migration(request: Request) -> JSONResponse:
    payload = await request.json()
    payload["operator"] = _operator_from_request(request)
    result = build_owner_migration_service().run(_command_from_payload(payload, execute=False))
    return JSONResponse(jsonable_encoder(result), status_code=_status_code(result))


@router.post("/api/admin/owner-migration/execute")
async def execute_owner_migration(request: Request) -> JSONResponse:
    payload = await request.json()
    payload["operator"] = _operator_from_request(request)
    result = build_owner_migration_service().run(_command_from_payload(payload, execute=True))
    return JSONResponse(jsonable_encoder(result), status_code=_status_code(result))


@router.get("/api/admin/owner-migration/template.xlsx")
async def owner_migration_template() -> Response:
    return _excel_response(owner_migration_template_xlsx(), "owner_migration_template.xlsx")


@router.post("/api/admin/owner-migration/import")
async def import_owner_migration(
    request: Request,
    file: UploadFile = File(...),
    source_owner_userid: str = Form(...),
    target_owner_userid: str = Form(...),
    include_wecom_transfer: str = Form("true"),
    transfer_welcome_msg: str = Form(DEFAULT_TRANSFER_WELCOME_MSG),
) -> JSONResponse:
    content = await file.read()
    result = build_owner_migration_service().import_file(
        filename=file.filename or "",
        content=content,
        source_owner_userid=source_owner_userid,
        target_owner_userid=target_owner_userid,
        include_wecom_transfer=_safe_bool(include_wecom_transfer, default=True),
        transfer_welcome_msg=transfer_welcome_msg,
        operator=_operator_from_request(request),
    )
    return JSONResponse(jsonable_encoder(result), status_code=_status_code(result))


@router.get("/api/admin/owner-migration/sessions/{session_id}/errors.xlsx")
async def owner_migration_session_errors(session_id: str) -> Response:
    result = build_owner_migration_service().export_session_errors(session_id)
    if not result.get("ok"):
        return JSONResponse(jsonable_encoder(result), status_code=_status_code(result))
    return _excel_response(result["content"], result["filename"])


@router.get("/api/admin/owner-migration/results/{result_id}.xlsx")
async def owner_migration_result_file(result_id: str) -> Response:
    result = build_owner_migration_service().export_result(result_id)
    if not result.get("ok"):
        return JSONResponse(jsonable_encoder(result), status_code=_status_code(result))
    return _excel_response(result["content"], result["filename"])


@router.post("/api/admin/owner-migration/transfer-result")
async def owner_migration_transfer_result(request: Request) -> JSONResponse:
    payload = await request.json()
    result = query_wecom_transfer_result(
        source_owner_userid=clean_text(payload.get("source_owner_userid") or payload.get("source")),
        target_owner_userid=clean_text(payload.get("target_owner_userid") or payload.get("target")),
        cursor=clean_text(payload.get("cursor")),
    )
    return JSONResponse(jsonable_encoder(result), status_code=_status_code(result))
