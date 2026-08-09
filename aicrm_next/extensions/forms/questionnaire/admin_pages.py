from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_shell_contract import shell_context

from .application import GetQuestionnaireEditorQuery, GetQuestionnairePreflightQuery, ListQuestionnairesQuery
from .external_push_logs import QuestionnaireExternalPushLogReadService
from .operations import (
    QuestionnaireOperationsNotFoundError,
    QuestionnaireOperationsService,
    QuestionnaireOperationsUnavailableError,
)

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_ADMIN_SHELL_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "app" / "admin_console" / "templates"
templates = Jinja2Templates(directory=[_TEMPLATES_DIR, _ADMIN_SHELL_TEMPLATES_DIR])


def _is_assessment_template_asset(questionnaire: dict | None) -> bool:
    if not questionnaire or not questionnaire.get("assessment_enabled"):
        return False
    config = questionnaire.get("assessment_config") if isinstance(questionnaire.get("assessment_config"), dict) else {}
    asset_kind = str(config.get("asset_kind") or "").strip()
    if asset_kind:
        return asset_kind == "assessment_template"
    return str(config.get("template_id") or "").strip() == "siyuan_ip_business"


def _placeholder_response(
    request: Request,
    *,
    page_title: str,
    page_summary: str,
    state_title: str,
    state_body: str,
    state_items: list[str],
    status_code: int,
    page_error: str = "",
) -> Response:
    context = shell_context(
        request=request,
        page_title=page_title,
        page_summary=page_summary,
        active_endpoint="api.admin_questionnaires",
    )
    context.update(
        {
            "breadcrumbs": [
                {"label": "客户管理后台", "href": "/admin"},
                {"label": "问卷管理", "href": "/admin/questionnaires"},
                {"label": page_title, "href": ""},
            ],
            "state_title": state_title,
            "state_body": state_body,
            "state_items": state_items,
            "actions": [{"label": "返回问卷管理", "href": "/admin/questionnaires", "variant": "secondary"}],
            "page_error": page_error,
        }
    )
    return templates.TemplateResponse(request, "admin_console/questionnaire_state.html", context, status_code=status_code)


def _questionnaire_editor_response(
    request: Request,
    *,
    questionnaire_id: int | None = None,
) -> Response:
    payload: dict | None = None
    page_error = ""
    if questionnaire_id is not None:
        try:
            payload = GetQuestionnaireEditorQuery()(questionnaire_id)
        except Exception as exc:
            return _placeholder_response(
                request,
                page_title="问卷不存在",
                page_summary="当前没有找到这个问卷。",
                state_title="问卷不存在",
                state_body="请确认问卷编号是否正确，或稍后重试。",
                state_items=["问卷可能已被删除", "当前环境也可能还没有初始化相关数据"],
                status_code=404,
                page_error=f"未找到问卷：{exc}",
            )
        if payload.get("source_status") == "production_unavailable":
            return _placeholder_response(
                request,
                page_title="问卷数据不可用",
                page_summary="当前生产问卷读模型不可用。",
                state_title="生产问卷数据不可用",
                state_body=str(payload.get("page_error") or "请确认生产问卷读模型已经完成同步。"),
                state_items=["source_status=production_unavailable", "fallback_used=false", "route_owner=ai_crm_next"],
                status_code=503,
                page_error=str(payload.get("page_error") or ""),
            )

    questionnaire = jsonable_encoder((payload or {}).get("questionnaire")) if payload else None
    if questionnaire is not None and isinstance(payload, dict):
        questionnaire["questions"] = jsonable_encoder(questionnaire.get("questions") or payload.get("questions") or [])
        for operation_key in (
            "lead_channel_id",
            "redirect_url",
            "completion_target",
            "completion_target_json",
            "completion_target_enabled",
            "completion_target_type",
            "external_push_config",
            "external_push_enabled",
            "external_push_url",
            "external_push_type",
            "external_push_expires_at_ts",
            "external_push_day",
            "external_push_frequency",
            "external_push_remark",
            "external_push_custom_params",
        ):
            questionnaire.pop(operation_key, None)
    default_assessment = (questionnaire_id is None and str(request.query_params.get("mode") or "").strip() == "assessment") or _is_assessment_template_asset(
        questionnaire
    )
    new_heading = "创建测评问卷模板" if default_assessment else "新建问卷"
    edit_heading = "编辑测评问卷模板" if default_assessment else "编辑问卷"
    new_subtitle = (
        "配置测评题目、维度分型和结果页规则，保存后可作为普通问卷的整组引用模板。" if default_assessment else "从空白模板开始搭建题目、标签和分数规则。"
    )
    edit_subtitle = "维护这个测评模板的题目、维度分型和结果页规则。" if default_assessment else "维护当前问卷的题目、分数规则和发布设置。"
    editor_page_title = (
        (questionnaire or {}).get("title")
        or (questionnaire or {}).get("name")
        or (edit_heading if questionnaire_id is not None else new_heading)
    )
    context = shell_context(
        request=request,
        page_title=editor_page_title,
        page_summary=edit_subtitle if questionnaire_id is not None else new_subtitle,
        active_endpoint="api.admin_questionnaires",
    )
    context.update(
        {
            "editor_mode": "edit" if questionnaire_id is not None else "new",
            "editor_page_title": editor_page_title,
            "editor_heading": edit_heading if questionnaire_id is not None else new_heading,
            "editor_subtitle": edit_subtitle if questionnaire_id is not None else new_subtitle,
            "editor_back_href": "/admin/questionnaires",
            "editor_default_assessment": default_assessment,
            "initial_questionnaire": questionnaire,
            "initial_questionnaire_id": questionnaire_id,
            "page_error": page_error,
        }
    )
    return templates.TemplateResponse(
        request,
        "admin_questionnaires.html",
        context,
    )


@router.get("/admin/questionnaires", name="api.admin_questionnaires")
def admin_questionnaires(request: Request) -> Response:
    list_payload = ListQuestionnairesQuery()(limit=100, offset=0)
    preflight_error = str(list_payload.get("page_error") or "") if list_payload.get("degraded") else ""
    preflight_payload = GetQuestionnairePreflightQuery()()
    context = shell_context(
        request=request,
        page_title="问卷管理",
        page_summary="读取生产问卷列表，保留新建、编辑、停用、删除和导出入口。",
        active_endpoint="api.admin_questionnaires",
    )
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": "/admin"},
        {"label": "问卷管理", "href": ""},
    ]
    context["page_actions"] = [
        {"label": "创建新问卷", "href": "/admin/questionnaires/new", "variant": "primary"},
        {"label": "创建测评问卷模板", "href": "/admin/questionnaires/new?mode=assessment", "variant": "secondary"},
        {"label": "刷新", "href": "/admin/questionnaires", "variant": "ghost"},
    ]
    questionnaires = jsonable_encoder(list_payload.get("questionnaires") or list_payload.get("items") or [])
    context["questionnaire_payload"] = {
        "questionnaires": questionnaires,
        "preflight": preflight_payload["checks"],
        "preflight_error": preflight_error,
        "total": list_payload.get("total", len(questionnaires)),
        "source_status": list_payload.get("source_status", "local_contract_probe"),
        "read_model_status": list_payload.get("read_model_status", ""),
        "route_owner": list_payload.get("route_owner", "ai_crm_next"),
        "fallback_used": bool(list_payload.get("fallback_used", False)),
        "degraded": bool(list_payload.get("degraded", False)),
    }
    if preflight_error:
        context["page_error"] = preflight_error
    return templates.TemplateResponse(request, "admin_console/questionnaires.html", context)


@router.get("/admin/questionnaires/ui", name="api.admin_console_questionnaires")
def admin_questionnaires_legacy_ui_alias() -> RedirectResponse:
    return RedirectResponse("/admin/questionnaires", status_code=302)


@router.get("/admin/questionnaires/new", name="api.admin_console_questionnaire_new")
def admin_questionnaire_new(request: Request) -> Response:
    return _questionnaire_editor_response(request)


@router.get(
    "/admin/questionnaires/{questionnaire_id:int}/operations",
    name="api.admin_console_questionnaire_operations",
)
def admin_questionnaire_operations(request: Request, questionnaire_id: int) -> Response:
    try:
        payload = QuestionnaireOperationsService().get_operations(questionnaire_id)
    except QuestionnaireOperationsNotFoundError as exc:
        return _placeholder_response(
            request,
            page_title="问卷不存在",
            page_summary="当前没有找到这个问卷。",
            state_title="无法打开运营配置",
            state_body=str(exc),
            state_items=["问卷可能已被删除", "请返回问卷管理后重试"],
            status_code=404,
        )
    except QuestionnaireOperationsUnavailableError as exc:
        return _placeholder_response(
            request,
            page_title="问卷运营配置不可用",
            page_summary="当前生产数据源不可用。",
            state_title="暂时无法读取运营配置",
            state_body=str(exc),
            state_items=["source_status=production_unavailable", "fallback_used=false"],
            status_code=503,
        )
    if _is_assessment_template_asset(payload.get("questionnaire")):
        return _placeholder_response(
            request,
            page_title="评测模板无需运营配置",
            page_summary="评测模板资产不能直接发布。",
            state_title="该资产不支持运营配置",
            state_body="请在普通问卷中引用评测模板后，再配置提交后动作。",
            state_items=["评测模板仅用于内容复用", "不会直接生成公开问卷"],
            status_code=404,
        )
    context = shell_context(
        request=request,
        page_title="问卷运营配置",
        page_summary="独立配置提交后动作与外部推送。",
        active_endpoint="api.admin_questionnaires",
    )
    context.update(
        {
            "show_page_header": False,
            "breadcrumbs": [
                {"label": "客户管理后台", "href": "/admin"},
                {"label": "问卷管理", "href": "/admin/questionnaires"},
                {"label": "运营配置", "href": ""},
            ],
            "operations_payload": jsonable_encoder(payload),
        }
    )
    return templates.TemplateResponse(request, "admin_console/questionnaire_operations.html", context)


@router.get("/admin/questionnaires/{questionnaire_id:int}", name="api.admin_console_questionnaire_detail")
def admin_questionnaire_detail(request: Request, questionnaire_id: int) -> Response:
    return _questionnaire_editor_response(request, questionnaire_id=questionnaire_id)


def _render_questionnaire_external_push_logs(request: Request) -> Response:
    questionnaire_id = request.path_params.get("questionnaire_id")
    service = QuestionnaireExternalPushLogReadService()
    if questionnaire_id:
        payload = service.questionnaire_logs(
            int(questionnaire_id),
            status=str(request.query_params.get("status") or ""),
            limit=str(request.query_params.get("limit") or "50"),
        )
        if payload is None:
            return JSONResponse({"ok": False, "error": "questionnaire not found", "source_status": "not_found"}, status_code=404)
    else:
        payload = service.global_logs(
            questionnaire_id=str(request.query_params.get("questionnaire_id") or ""),
            questionnaire_title=str(request.query_params.get("questionnaire_title") or ""),
            status=str(request.query_params.get("status") or ""),
            user_id=str(request.query_params.get("user_id") or ""),
            target_url=str(request.query_params.get("target_url") or ""),
            limit=str(request.query_params.get("limit") or "50"),
        )
    context = shell_context(
        request=request,
        page_title="问卷外推记录",
        page_summary="查看问卷提交后的外部推送结果、失败原因和补发计划。",
        active_endpoint="api.admin_questionnaires",
    )
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": "/admin"},
        {"label": "问卷管理", "href": "/admin/questionnaires"},
        {"label": "问卷外推记录", "href": ""},
    ]
    context["logs_payload"] = payload
    return templates.TemplateResponse(request, "admin_console/questionnaire_external_push_logs.html", context)


@router.get(
    "/admin/questionnaires/external-push-logs",
    name="api.admin_console_global_questionnaire_external_push_logs",
)
@router.get(
    "/admin/questionnaires/{questionnaire_id:int}/external-push-logs",
    name="api.admin_console_questionnaire_external_push_logs",
)
def admin_questionnaire_external_push_logs(request: Request) -> Response:
    return _render_questionnaire_external_push_logs(request)
