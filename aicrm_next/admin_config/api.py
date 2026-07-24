from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_read_model.application import GetAdminConfigPageQuery, page_row_count
from aicrm_next.admin_shell import admin_path_for, shell_context
from aicrm_next.shared.admin_action_runtime import ensure_admin_action_token, validate_admin_action_token
from aicrm_next.capability_registry import registry_summary

from .api_docs_view_model import build_api_docs_view_model
from .config_definitions import config_definition_summary
from .config_releases import ConfigReleaseService
from .application import (
    AdminConfigReadService,
    AdminConfigWriteCommand,
    LoginAccessSaveCommand,
    McpToolSettingSaveCommand,
    SetupWizardSaveCommand,
    SetupWizardStateService,
    SignupConversionConfigSaveCommand,
    _bool,
    _text,
)

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "frontend_compat" / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)
ADMIN_ACCESS_DETAIL_PATH = "/admin/config/detail/admin_access"
CONFIG_RELEASE_ROWS = 8


def _operator_from_request(request: Request, payload: dict[str, Any] | None = None, form: Any | None = None) -> str:
    return (
        _text(request.headers.get("X-Admin-Operator"))
        or _text((form or {}).get("operator") if form is not None else "")
        or _text((payload or {}).get("operator") if payload else "")
        or "crm_console"
    )


def _config_release_service(request: Request) -> ConfigReleaseService:
    return ConfigReleaseService(profile=getattr(request.app.state, "deployment_profile", None))


def _config_context(
    request: Request,
    *,
    active_tab: str,
    page_title: str,
    page_summary: str,
    page_notice: str = "",
    page_error: str = "",
    **extra: Any,
) -> dict[str, Any]:
    read_service = AdminConfigReadService()
    context = shell_context(
        request=request,
        page_title=page_title,
        page_summary=page_summary,
        active_endpoint="api.admin_config",
    )
    context.update(
        {
            "breadcrumbs": [
                {"label": "客户管理后台", "href": "/admin"},
                {"label": "配置中心", "href": "/admin/config"},
                {"label": page_title, "href": ""},
            ],
            "config_tabs": read_service.config_tabs(active_tab),
            "page_notice": page_notice,
            "page_error": page_error,
            "admin_action_token": ensure_admin_action_token(),
            "url_for": admin_path_for,
        }
    )
    context.update(extra)
    return context


def _real_data_context(context: dict, *, payload: dict, title: str, summary: str) -> dict:
    context.update(
        {
            "real_data_payload": payload,
            "data_title": title,
            "data_summary": summary,
            "real_data_row_count": page_row_count(payload),
        }
    )
    if payload.get("page_error"):
        context["page_error"] = payload["page_error"]
    return context


def _redirect(url: str, **query: Any) -> RedirectResponse:
    if query:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode({key: value for key, value in query.items() if _text(value)})}"
    return RedirectResponse(url=url, status_code=302)


def _admin_access_detail_url(request: Request | None = None, **query: Any) -> str:
    merged: dict[str, Any] = {}
    if request is not None:
        merged.update(dict(parse_qsl(str(request.url.query), keep_blank_values=False)))
    merged.update({key: value for key, value in query.items() if _text(value)})
    if not merged:
        return ADMIN_ACCESS_DETAIL_PATH
    return f"{ADMIN_ACCESS_DETAIL_PATH}?{urlencode(merged)}"


def _build_admin_access_context(request: Request, detail: dict[str, Any]) -> dict[str, Any]:
    payload = AdminConfigReadService().build_login_access_payload()
    edit_id = _text(request.query_params.get("edit_id"))
    candidate_userid = _text(request.query_params.get("wecom_userid"))
    directory_candidate = next((row for row in payload["directory_members"] if row["wecom_userid"] == candidate_userid), None)
    default_form_row = {
        "is_active": True,
        "login_enabled": True,
        "admin_level": "admin",
        "roles": ["viewer"],
        "wecom_corpid": payload.get("corp_id", ""),
    }
    if directory_candidate:
        default_form_row.update(
            {
                "wecom_userid": directory_candidate["wecom_userid"],
                "display_name": directory_candidate["display_name"],
                "wecom_corpid": directory_candidate["wecom_corpid"] or payload.get("corp_id", ""),
                "auth_source": "wecom_sso",
            }
        )
    form_row = next((row for row in payload["rows"] if str(row["id"]) == edit_id), default_form_row)
    return _config_context(
        request,
        active_tab="login_access",
        page_title="后台访问",
        page_summary="配置后台认证参数，并维护允许访问 CRM 后台的企微成员。",
        page_notice="保存成功" if _bool(request.query_params.get("saved")) else _text(request.query_params.get("notice")),
        page_error=_text(request.query_params.get("error")),
        config_category_detail=detail,
        form_row=form_row,
        can_manage_accounts=True,
        can_manage_super_admin=True,
        can_manage_form=True,
        **payload,
    )


async def _form_dict(request: Request) -> dict[str, Any]:
    form = await request.form()
    payload: dict[str, Any] = {}
    for key, value in form.multi_items():
        if key in payload:
            existing = payload[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                payload[key] = [existing, value]
        else:
            payload[key] = value
    return payload


async def _token_error_from_form(request: Request) -> tuple[str, dict[str, Any]]:
    form = await _form_dict(request)
    return validate_admin_action_token(_text(form.get("admin_action_token")), request=request), form


def _token_error_from_payload(request: Request, payload: dict[str, Any]) -> str:
    token = _text(request.headers.get("X-Admin-Action-Token")) or _text(payload.get("admin_action_token"))
    return validate_admin_action_token(token, request=request)


def _category_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, KeyError):
        return JSONResponse({"ok": False, "error": "config category not found"}, status_code=404)
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


def _push_capability_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, KeyError):
        return JSONResponse({"ok": False, "error": "push_capability_not_found"}, status_code=404)
    if isinstance(exc, PermissionError):
        return JSONResponse({"ok": False, "error": "push_capability_not_toggleable"}, status_code=409)
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


def _config_release_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, KeyError):
        return JSONResponse({"ok": False, "error": "config release not found"}, status_code=404)
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


def _config_release_status_label(status: Any) -> str:
    return {
        "draft": "草稿",
        "validated": "校验通过",
        "validation_failed": "校验失败",
        "published": "当前生效",
        "superseded": "已被替代",
    }.get(_text(status), _text(status) or "未知")


def _config_release_view(release: dict[str, Any]) -> dict[str, Any]:
    return {
        **release,
        "status_label": _config_release_status_label(release.get("status")),
        "change_count": len(release.get("changed_keys") or []),
    }


def _config_release_changes_from_form(form: dict[str, Any]) -> dict[str, str | None]:
    changes: dict[str, str | None] = {}
    for index in range(CONFIG_RELEASE_ROWS):
        key = _text(form.get(f"key__{index}"))
        if not key:
            continue
        if key in changes:
            raise ValueError(f"duplicate config key: {key}")
        changes[key] = None if _bool(form.get(f"remove__{index}")) else _text(form.get(f"value__{index}"))
    return changes


@router.get("/admin/config", name="api.admin_config", response_class=HTMLResponse)
def admin_config_home(request: Request):
    payload = AdminConfigReadService().build_home_payload()
    return templates.TemplateResponse(
        request,
        "admin_console/config_center.html",
        _config_context(
            request,
            active_tab="overview",
            page_title="系统配置",
            page_summary="查看配置类目的生效状态，进入配置页维护明细。",
            config_categories=payload["categories"],
        ),
    )


@router.get("/admin/config/detail/{category_key}", name="api.admin_config_category_detail")
def admin_config_category_detail(request: Request, category_key: str):
    try:
        detail = AdminConfigReadService().get_config_category_detail(category_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="config category not found") from exc
    category = detail["category"]
    if _text(category.get("key")) == "admin_access":
        return templates.TemplateResponse(
            request,
            "admin_console/config_admin_access_detail.html",
            _build_admin_access_context(request, detail),
        )
    if _text(category.get("special_view")) == "push_capabilities":
        return templates.TemplateResponse(
            request,
            "admin_console/config_push_capabilities.html",
            _config_context(
                request,
                active_tab="overview",
                page_title="推送能力配置",
                page_summary="运营只管理业务推送能力开关；工程参数由后端派生和保护。",
                config_category_detail=detail,
                push_capabilities_api="/api/admin/config/push-capabilities",
                push_capabilities_scheduler_api="/api/admin/config/push-capabilities/scheduler",
            ),
        )
    return templates.TemplateResponse(
        request,
        "admin_console/config_category_detail.html",
        _config_context(
            request,
            active_tab="overview",
            page_title=_text(category.get("label")) or "配置明细",
            page_summary="配置明细",
            config_category_detail=detail,
        ),
    )


@router.get("/admin/runtime-config", name="api.admin_runtime_config", response_class=HTMLResponse)
def admin_runtime_config(request: Request):
    context = shell_context(
        request=request,
        page_title="运行配置",
        page_summary="查看 Next 运行时、发布和外部回调预检状态。",
        active_endpoint="api.admin_runtime_config",
    )
    _real_data_context(
        context,
        payload=GetAdminConfigPageQuery()(),
        title="运行状态快照",
        summary="展示数据库模式、release、callback fallback、OAuth、企微和支付配置预检状态；不展示 secrets。",
    )
    return templates.TemplateResponse(request, "admin_console/real_data_page.html", context)


@router.get("/admin/api-docs", name="api.admin_api_docs", response_class=HTMLResponse)
def admin_api_docs(request: Request):
    context = shell_context(
        request=request,
        page_title="API 文档",
        page_summary="查看 AI-CRM 后台和外部集成 API 文档。",
        active_endpoint="api.admin_api_docs",
    )
    context.update(
        {
            "breadcrumbs": [
                {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
                {"label": "API 文档"},
            ],
            **build_api_docs_view_model(),
        }
    )
    return templates.TemplateResponse(request, "admin_console/api_docs.html", context)


@router.get("/admin/config/wecom-tags", name="api.admin_config_wecom_tags", response_class=HTMLResponse)
def admin_config_wecom_tags():
    return _redirect("/admin/wecom-tags")


@router.get("/admin/config/app-settings", name="api.admin_config_app_settings", response_class=HTMLResponse)
def admin_config_app_settings(request: Request):
    query = _text(request.query_params.get("q"))
    scope = _text(request.query_params.get("scope"))
    payload = AdminConfigReadService().list_app_settings(query=query, scope=scope)
    rows = payload["rows"]
    return templates.TemplateResponse(
        request,
        "admin_console/config_app_settings.html",
        _config_context(
            request,
            active_tab="app_settings",
            page_title="系统设置",
            page_summary="集中查看和修改系统级参数；敏感信息仅显示掩码。",
            page_notice="保存成功" if _bool(request.query_params.get("saved")) else "",
            page_error=_text(request.query_params.get("error")),
            filters={"q": query, "scope": scope},
            rows=rows,
            editable_rows=[row for row in rows if row.get("mode") == "editable"],
            masked_rows=[row for row in rows if row.get("mode") == "masked"],
            summary_cards=payload["summary_cards"],
            audit_entries=payload["audit_entries"],
        ),
    )


@router.post("/admin/config/app-settings/save", name="api.admin_config_save_app_settings", response_class=HTMLResponse)
async def admin_config_save_app_settings(request: Request):
    token_error, form = await _token_error_from_form(request)
    if token_error:
        return _redirect("/admin/config/app-settings", error=token_error)
    if not _bool(form.get("confirm")):
        return _redirect("/admin/config/app-settings", error="confirm is required before saving app settings")
    settings = {key[len("setting__") :]: value for key, value in form.items() if key.startswith("setting__")}
    try:
        AdminConfigWriteCommand().execute(settings, operator=_operator_from_request(request, form=form))
    except ValueError as exc:
        return _redirect("/admin/config/app-settings", error=str(exc))
    return _redirect("/admin/config/app-settings", saved=1)


@router.get("/admin/config/releases", name="api.admin_config_releases", response_class=HTMLResponse)
def admin_config_releases(request: Request):
    service = _config_release_service(request)
    releases = [_config_release_view(row) for row in service.list(limit=100)]
    profile_state = service.profile_state()
    active_release_id = profile_state.get("active_config_release_id")
    return templates.TemplateResponse(
        request,
        "admin_console/config_releases.html",
        _config_context(
            request,
            active_tab="releases",
            page_title="配置发布",
            page_summary="以草稿、校验、原子发布和回滚管理日常业务配置。",
            page_notice=(
                "配置发布成功"
                if _bool(request.query_params.get("published"))
                else "已创建回滚发布"
                if _bool(request.query_params.get("rolled_back"))
                else ""
            ),
            page_error=_text(request.query_params.get("error")),
            releases=releases,
            profile_state=profile_state,
            summary_cards=[
                {
                    "label": "部署档案",
                    "value": profile_state.get("profile_id") or "wecom-core",
                    "description": "所有客户实例使用同一制品，通过静态部署档案区分。",
                },
                {
                    "label": "当前发布",
                    "value": f"#{active_release_id}" if active_release_id else "尚未发布",
                    "description": "发布与 app_settings 写入处于同一数据库事务。",
                },
                {
                    "label": "启用模式",
                    "value": "观察模式" if profile_state.get("activation_mode") == "observe" else "强制模式",
                    "description": "观察模式保持现有路由和运行行为不变。",
                },
            ],
        ),
    )


@router.get("/admin/config/releases/new", name="api.admin_config_release_new", response_class=HTMLResponse)
def admin_config_release_new(request: Request):
    service = _config_release_service(request)
    return templates.TemplateResponse(
        request,
        "admin_console/config_release_new.html",
        _config_context(
            request,
            active_tab="releases",
            page_title="新建配置发布",
            page_summary="只提交本次需要变更的配置项；密钥只填写 Secret Store 引用。",
            page_error=_text(request.query_params.get("error")),
            definitions=service.definitions(),
            profile_state=service.profile_state(),
            release_row_indexes=range(CONFIG_RELEASE_ROWS),
        ),
    )


@router.post("/admin/config/releases", name="api.admin_config_release_create", response_class=HTMLResponse)
async def admin_config_release_create(request: Request):
    token_error, form = await _token_error_from_form(request)
    if token_error:
        return _redirect("/admin/config/releases/new", error=token_error)
    if not _bool(form.get("confirm")):
        return _redirect("/admin/config/releases/new", error="confirm is required before creating a config release")
    try:
        release = _config_release_service(request).create_draft(
            _config_release_changes_from_form(form),
            operator=_operator_from_request(request, form=form),
        )
    except ValueError as exc:
        return _redirect("/admin/config/releases/new", error=str(exc))
    return _redirect(f"/admin/config/releases/{release['id']}", created=1)


@router.get("/admin/config/releases/{release_id}", name="api.admin_config_release_detail", response_class=HTMLResponse)
def admin_config_release_detail(request: Request, release_id: int):
    service = _config_release_service(request)
    release = service.get(release_id)
    if not release:
        raise HTTPException(status_code=404, detail="config release not found")
    return templates.TemplateResponse(
        request,
        "admin_console/config_release_detail.html",
        _config_context(
            request,
            active_tab="releases",
            page_title=f"配置发布 #{release_id}",
            page_summary="检查变更、校验结果与发布审计，再决定是否生效或回滚。",
            page_notice=(
                "草稿已创建"
                if _bool(request.query_params.get("created"))
                else "校验完成"
                if _bool(request.query_params.get("validated"))
                else ""
            ),
            page_error=_text(request.query_params.get("error")),
            release=_config_release_view(release),
            shadow_compare=service.shadow_compare(release_id),
        ),
    )


@router.post("/admin/config/releases/{release_id}/validate", name="api.admin_config_release_validate", response_class=HTMLResponse)
async def admin_config_release_validate(request: Request, release_id: int):
    token_error, _form = await _token_error_from_form(request)
    if token_error:
        return _redirect(f"/admin/config/releases/{release_id}", error=token_error)
    try:
        _config_release_service(request).validate(release_id)
    except (KeyError, ValueError) as exc:
        return _redirect(f"/admin/config/releases/{release_id}", error=str(exc))
    return _redirect(f"/admin/config/releases/{release_id}", validated=1)


@router.post("/admin/config/releases/{release_id}/publish", name="api.admin_config_release_publish", response_class=HTMLResponse)
async def admin_config_release_publish(request: Request, release_id: int):
    token_error, form = await _token_error_from_form(request)
    if token_error:
        return _redirect(f"/admin/config/releases/{release_id}", error=token_error)
    if not _bool(form.get("confirm")):
        return _redirect(f"/admin/config/releases/{release_id}", error="confirm is required before publishing")
    try:
        _config_release_service(request).publish(
            release_id,
            expected_checksum=_text(form.get("checksum")),
            operator=_operator_from_request(request, form=form),
        )
    except (KeyError, ValueError) as exc:
        return _redirect(f"/admin/config/releases/{release_id}", error=str(exc))
    return _redirect("/admin/config/releases", published=1)


@router.post("/admin/config/releases/{release_id}/rollback", name="api.admin_config_release_rollback", response_class=HTMLResponse)
async def admin_config_release_rollback(request: Request, release_id: int):
    token_error, form = await _token_error_from_form(request)
    if token_error:
        return _redirect(f"/admin/config/releases/{release_id}", error=token_error)
    if not _bool(form.get("confirm")):
        return _redirect(f"/admin/config/releases/{release_id}", error="confirm is required before rollback")
    try:
        _config_release_service(request).rollback(
            release_id,
            operator=_operator_from_request(request, form=form),
        )
    except (KeyError, ValueError) as exc:
        return _redirect(f"/admin/config/releases/{release_id}", error=str(exc))
    return _redirect("/admin/config/releases", rolled_back=1)


@router.get("/api/admin/config/overview", name="api.admin_config_overview")
def api_admin_config_overview() -> dict[str, Any]:
    return {"ok": True, "overview": AdminConfigReadService().build_home_payload(), "source_status": "next_read_model", "fallback_used": False}


@router.get("/api/admin/config/categories", name="api.admin_config_categories")
def api_admin_config_categories() -> dict[str, Any]:
    return {
        "ok": True,
        "config": AdminConfigReadService().list_config_categories(),
        "source_status": "next_read_model",
        "fallback_used": False,
    }


@router.get("/api/admin/config/categories/{category_key}", name="api.admin_config_category")
def api_admin_config_category(category_key: str):
    try:
        detail = AdminConfigReadService().get_config_category_detail(category_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="config category not found") from exc
    return {
        "ok": True,
        "config": detail,
        "source_status": "next_read_model",
        "fallback_used": False,
    }


@router.put("/api/admin/config/categories/{category_key}/enabled", name="api.admin_config_category_enabled")
async def api_admin_config_category_enabled(category_key: str, request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "payload must be an object"}, status_code=400)
    token_error = _token_error_from_payload(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=400)
    try:
        saved = AdminConfigWriteCommand().set_category_enabled(
            category_key,
            _bool(payload.get("enabled")),
            operator=_operator_from_request(request, payload=payload),
        )
    except (KeyError, ValueError) as exc:
        return _category_error(exc)
    return {
        "ok": True,
        "item": saved,
        "config": AdminConfigReadService().get_config_category_detail(category_key)["category"],
        "source_status": "next_command",
        "fallback_used": False,
        "real_external_call_executed": False,
    }


@router.put("/api/admin/config/categories/{category_key}/settings", name="api.admin_config_category_settings")
async def api_admin_config_category_settings(category_key: str, request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "payload must be an object"}, status_code=400)
    settings = payload.get("settings") or {}
    if not isinstance(settings, dict):
        return JSONResponse({"ok": False, "error": "settings must be an object"}, status_code=400)
    token_error = _token_error_from_payload(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=400)
    try:
        changed = AdminConfigWriteCommand().save_category_settings(
            category_key,
            settings,
            operator=_operator_from_request(request, payload=payload),
        )
        detail = AdminConfigReadService().get_config_category_detail(category_key)
    except (KeyError, ValueError) as exc:
        return _category_error(exc)
    return {
        "ok": True,
        "changed": changed,
        "changed_count": len(changed),
        "config": detail,
        "source_status": "next_command",
        "fallback_used": False,
        "real_external_call_executed": False,
    }


@router.post("/api/admin/config/categories/{category_key}/check", name="api.admin_config_category_check")
async def api_admin_config_category_check(category_key: str, request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "payload must be an object"}, status_code=400)
    try:
        result = AdminConfigWriteCommand().check_category(
            category_key,
            operator=_operator_from_request(request, payload=payload),
        )
    except (KeyError, ValueError) as exc:
        return _category_error(exc)
    return {
        **result,
        "source_status": "next_command",
        "fallback_used": False,
    }


@router.get("/api/admin/config/app-settings", name="api.admin_config_app_settings_resource")
def api_admin_config_app_settings(request: Request) -> dict[str, Any]:
    return {
        "ok": True,
        "config": AdminConfigReadService().list_app_settings(
            query=_text(request.query_params.get("q")),
            scope=_text(request.query_params.get("scope")),
        ),
        "source_status": "next_read_model",
        "fallback_used": False,
    }


@router.get("/api/admin/config/capabilities", name="api.admin_config_capabilities")
def api_admin_config_capabilities() -> dict[str, Any]:
    return {
        "ok": True,
        "registry": registry_summary(),
        "source_status": "static_capability_registry",
        "fallback_used": False,
    }


@router.get("/api/admin/config/definitions", name="api.admin_config_definitions")
def api_admin_config_definitions(request: Request) -> dict[str, Any]:
    service = _config_release_service(request)
    return {
        "ok": True,
        "schema": config_definition_summary(),
        "enabled_definitions": service.definitions(),
        "source_status": "static_config_definitions",
        "fallback_used": False,
    }


@router.get("/api/admin/config/deployment-profile", name="api.admin_config_deployment_profile")
def api_admin_config_deployment_profile(request: Request) -> dict[str, Any]:
    return {
        "ok": True,
        "profile": _config_release_service(request).profile_state(),
        "source_status": "deployment_profile",
        "fallback_used": False,
    }


@router.get("/api/admin/config/releases", name="api.admin_config_releases_resource")
def api_admin_config_releases_resource(request: Request, limit: int = 50) -> dict[str, Any]:
    return {
        "ok": True,
        "releases": _config_release_service(request).list(limit=limit),
        "source_status": "config_release_read_model",
        "fallback_used": False,
    }


@router.post("/api/admin/config/releases", name="api.admin_config_release_create_resource")
async def api_admin_config_release_create_resource(request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "payload must be an object"}, status_code=400)
    token_error = _token_error_from_payload(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    if not _bool(payload.get("confirm")):
        return JSONResponse({"ok": False, "error": "confirm is required before creating a config release"}, status_code=400)
    try:
        release = _config_release_service(request).create_draft(
            payload.get("changes") or {},
            operator=_operator_from_request(request, payload=payload),
            based_on_release_id=int(payload["based_on_release_id"]) if payload.get("based_on_release_id") else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _config_release_error(exc)
    return {
        "ok": True,
        "release": release,
        "source_status": "config_release_command",
        "fallback_used": False,
        "real_external_call_executed": False,
    }


@router.get("/api/admin/config/releases/{release_id}", name="api.admin_config_release_resource")
def api_admin_config_release_resource(release_id: int, request: Request):
    release = _config_release_service(request).get(release_id)
    if not release:
        return JSONResponse({"ok": False, "error": "config release not found"}, status_code=404)
    return {
        "ok": True,
        "release": release,
        "source_status": "config_release_read_model",
        "fallback_used": False,
    }


@router.get("/api/admin/config/releases/{release_id}/shadow-compare", name="api.admin_config_release_shadow_compare")
def api_admin_config_release_shadow_compare(release_id: int, request: Request):
    try:
        comparison = _config_release_service(request).shadow_compare(release_id)
    except KeyError as exc:
        return _config_release_error(exc)
    return {
        "ok": comparison["ok"],
        "comparison": comparison,
        "source_status": "config_release_shadow_compare",
        "fallback_used": False,
    }


@router.post("/api/admin/config/releases/{release_id}/validate", name="api.admin_config_release_validate_resource")
async def api_admin_config_release_validate_resource(release_id: int, request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "payload must be an object"}, status_code=400)
    token_error = _token_error_from_payload(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    try:
        release = _config_release_service(request).validate(release_id)
    except (KeyError, ValueError) as exc:
        return _config_release_error(exc)
    return {
        "ok": True,
        "release": release,
        "source_status": "config_release_command",
        "fallback_used": False,
        "real_external_call_executed": False,
    }


@router.post("/api/admin/config/releases/{release_id}/publish", name="api.admin_config_release_publish_resource")
async def api_admin_config_release_publish_resource(release_id: int, request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "payload must be an object"}, status_code=400)
    token_error = _token_error_from_payload(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    if not _bool(payload.get("confirm")):
        return JSONResponse({"ok": False, "error": "confirm is required before publishing"}, status_code=400)
    try:
        release = _config_release_service(request).publish(
            release_id,
            expected_checksum=_text(payload.get("checksum")),
            operator=_operator_from_request(request, payload=payload),
        )
    except (KeyError, ValueError) as exc:
        return _config_release_error(exc)
    return {
        "ok": True,
        "release": release,
        "source_status": "config_release_command",
        "fallback_used": False,
        "real_external_call_executed": False,
    }


@router.post("/api/admin/config/releases/{release_id}/rollback", name="api.admin_config_release_rollback_resource")
async def api_admin_config_release_rollback_resource(release_id: int, request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "payload must be an object"}, status_code=400)
    token_error = _token_error_from_payload(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    if not _bool(payload.get("confirm")):
        return JSONResponse({"ok": False, "error": "confirm is required before rollback"}, status_code=400)
    try:
        release = _config_release_service(request).rollback(
            release_id,
            operator=_operator_from_request(request, payload=payload),
        )
    except (KeyError, ValueError) as exc:
        return _config_release_error(exc)
    return {
        "ok": True,
        "release": release,
        "source_status": "config_release_command",
        "fallback_used": False,
        "real_external_call_executed": False,
    }


@router.get("/api/admin/config/push-capabilities", name="api.admin_config_push_capabilities")
def api_admin_config_push_capabilities() -> dict[str, Any]:
    return AdminConfigReadService().get_push_capabilities()


@router.patch("/api/admin/config/push-capabilities/scheduler", name="api.admin_config_patch_push_scheduler")
async def api_admin_config_patch_push_scheduler(request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "payload must be an object"}, status_code=400)
    token_error = _token_error_from_payload(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    if "enabled" not in payload:
        return JSONResponse({"ok": False, "error": "enabled is required"}, status_code=400)
    result = AdminConfigWriteCommand().set_external_effect_scheduler_enabled(
        _bool(payload.get("enabled")),
        operator=_operator_from_request(request, payload=payload),
    )
    return {
        "ok": True,
        "scheduler": result["scheduler"],
        "route_owner": "ai_crm_next",
        "real_external_call_executed": False,
    }


@router.patch("/api/admin/config/push-capabilities/{capability_key}", name="api.admin_config_patch_push_capability")
async def api_admin_config_patch_push_capability(capability_key: str, request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "payload must be an object"}, status_code=400)
    token_error = _token_error_from_payload(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    if "enabled" not in payload:
        return JSONResponse({"ok": False, "error": "enabled is required"}, status_code=400)
    try:
        result = AdminConfigWriteCommand().set_push_capability_enabled(
            capability_key,
            _bool(payload.get("enabled")),
            operator=_operator_from_request(request, payload=payload),
        )
    except (KeyError, PermissionError, ValueError) as exc:
        return _push_capability_error(exc)
    return {
        "ok": True,
        "capability": result["capability"],
        "derived_gates": result["derived_gates"],
        "route_owner": "ai_crm_next",
        "real_external_call_executed": False,
    }


@router.get("/admin/config/mcp-tools", name="api.admin_config_mcp_tools", response_class=HTMLResponse)
def admin_config_mcp_tools():
    return _redirect("/admin/api-docs")


@router.post("/admin/config/mcp-tools/save", name="api.admin_config_save_mcp_tool", response_class=HTMLResponse)
def admin_config_save_mcp_tool():
    return _redirect("/admin/api-docs")


@router.get("/api/admin/config/mcp-tools", name="api.admin_config_mcp_tools_resource")
def api_admin_config_mcp_tools(request: Request) -> dict[str, Any]:
    return {
        "ok": True,
        "config": AdminConfigReadService().list_mcp_tool_settings(
            query=_text(request.query_params.get("q")),
            enabled_only=_bool(request.query_params.get("enabled_only")),
        ),
        "source_status": "next_read_model",
        "fallback_used": False,
    }


@router.post("/api/admin/config/mcp-tools", name="api.admin_config_save_mcp_tool_resource")
async def api_admin_config_save_mcp_tool(request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "payload must be an object"}, status_code=400)
    try:
        saved = McpToolSettingSaveCommand().execute(payload, operator=_operator_from_request(request, payload=payload))
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {
        "ok": True,
        "item": saved,
        "source_status": "next_command",
        "fallback_used": False,
        "real_external_call_executed": False,
    }


@router.get("/api/admin/config/marketing-automation/signup-conversion", name="api.admin_config_signup_conversion")
def api_admin_config_signup_conversion() -> dict[str, Any]:
    return {
        "ok": True,
        "config": AdminConfigReadService().get_signup_conversion_config(),
        "source_status": "next_read_model",
        "fallback_used": False,
    }


@router.put("/api/admin/config/marketing-automation/signup-conversion", name="api.admin_config_save_signup_conversion")
async def api_admin_config_save_signup_conversion(request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "payload must be an object"}, status_code=400)
    try:
        saved = SignupConversionConfigSaveCommand().execute(payload, operator=_operator_from_request(request, payload=payload))
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {
        "ok": True,
        "config": saved,
        "source_status": "next_command",
        "fallback_used": False,
        "real_external_call_executed": False,
    }


@router.get("/api/admin/config/routing", name="api.admin_config_routing")
def api_admin_config_routing():
    raise HTTPException(status_code=404, detail="admin config routing API is retired")


@router.post("/api/admin/config/routing/owner-role", name="api.admin_config_save_owner_role")
def api_admin_config_save_owner_role():
    raise HTTPException(status_code=404, detail="admin config owner-role API is retired")


@router.post("/api/admin/config/routing/rule", name="api.admin_config_save_routing_rule")
def api_admin_config_save_routing_rule():
    raise HTTPException(status_code=404, detail="admin config routing-rule API is retired")


@router.get("/api/admin/config/signup-tags", name="api.admin_config_signup_tags")
def api_admin_config_signup_tags():
    raise HTTPException(status_code=404, detail="admin config signup-tags API is retired")


@router.post("/api/admin/config/signup-tags", name="api.admin_config_save_signup_tag")
def api_admin_config_save_signup_tag():
    raise HTTPException(status_code=404, detail="admin config signup-tags API is retired")


@router.get("/api/admin/config/class-term-tags", name="api.admin_config_class_term_tags")
def api_admin_config_class_term_tags():
    raise HTTPException(status_code=404, detail="admin config class-term-tags API is retired")


@router.post("/api/admin/config/class-term-tags", name="api.admin_config_save_class_term_tag")
def api_admin_config_save_class_term_tag():
    raise HTTPException(status_code=404, detail="admin config class-term-tags API is retired")


@router.put("/api/admin/config/app-settings", name="api.admin_config_save_app_settings_resource")
async def api_admin_config_save_app_settings(request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "payload must be an object"}, status_code=400)
    settings = payload.get("settings") or {}
    if not isinstance(settings, dict):
        return JSONResponse({"ok": False, "error": "settings must be an object"}, status_code=400)
    if not _bool(payload.get("confirm")):
        return JSONResponse({"ok": False, "error": "confirm is required before saving app settings"}, status_code=400)
    token = _text(request.headers.get("X-Admin-Action-Token")) or _text(payload.get("admin_action_token"))
    token_error = validate_admin_action_token(token, request=request)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=400)
    try:
        changed = AdminConfigWriteCommand().execute(settings, operator=_operator_from_request(request, payload=payload))
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {
        "ok": True,
        "changed": changed,
        "changed_count": len(changed),
        "config": AdminConfigReadService().list_app_settings(query="", scope=""),
        "source_status": "next_command",
        "fallback_used": False,
        "real_external_call_executed": False,
    }


@router.get("/admin/config/login-access", name="api.admin_config_login_access", response_class=HTMLResponse)
def admin_config_login_access(request: Request):
    return _redirect(_admin_access_detail_url(request))


@router.post("/admin/config/login-access/directory/refresh", name="api.admin_config_refresh_login_access_directory")
async def admin_config_refresh_login_access_directory(request: Request):
    token_error, _form = await _token_error_from_form(request)
    if token_error:
        return _redirect(ADMIN_ACCESS_DETAIL_PATH, error=token_error)
    return _redirect(ADMIN_ACCESS_DETAIL_PATH, notice="通讯录刷新已跳过：Next 配置模块不会触发真实企微外呼")


@router.post("/admin/config/login-access/save", name="api.admin_config_save_login_access")
async def admin_config_save_login_access(request: Request):
    token_error, form = await _token_error_from_form(request)
    if token_error:
        return _redirect(ADMIN_ACCESS_DETAIL_PATH, error=token_error)
    try:
        saved = LoginAccessSaveCommand().execute(form, operator=_operator_from_request(request, form=form))
    except ValueError as exc:
        return _redirect(ADMIN_ACCESS_DETAIL_PATH, error=str(exc))
    return _redirect(ADMIN_ACCESS_DETAIL_PATH, saved=1, edit_id=saved.get("id", ""))


@router.get("/admin/config/checklist", name="api.admin_config_checklist", response_class=HTMLResponse)
def admin_config_checklist(request: Request):
    return templates.TemplateResponse(
        request,
        "admin_console/config_checklist.html",
        _config_context(
            request,
            active_tab="checklist",
            page_title="配置检查清单",
            page_summary="新客户接入时按照此清单逐项配置，必填项标红星，绿色表示已配置。",
            checklist=AdminConfigReadService().build_checklist(),
        ),
    )


@router.get("/setup/wizard", name="api.setup_wizard", response_class=HTMLResponse)
def setup_wizard(request: Request):
    context = shell_context(
        request=request,
        page_title="系统配置向导",
        page_summary="按步骤填写企业微信和系统配置信息。",
        active_endpoint="api.admin_config",
    )
    context.update({"url_for": admin_path_for, **SetupWizardStateService().build_state()})
    return templates.TemplateResponse(request, "admin_console/setup_wizard.html", context)


@router.post("/setup/wizard/save", name="api.setup_wizard_save", response_class=HTMLResponse)
async def setup_wizard_save(request: Request):
    token_error, form = await _token_error_from_form(request)
    state_service = SetupWizardStateService()
    context = shell_context(
        request=request,
        page_title="系统配置向导",
        page_summary="按步骤填写企业微信和系统配置信息。",
        active_endpoint="api.admin_config",
    )
    if token_error:
        state = state_service.build_state(
            validation_errors=[{"group": "后台安全", "field": "动作令牌", "key": "admin_action_token", "error": token_error}]
        )
        context.update({"url_for": admin_path_for, **state})
        return templates.TemplateResponse(request, "admin_console/setup_wizard.html", context)
    result = SetupWizardSaveCommand().execute(form, operator=_operator_from_request(request, form=form))
    state = state_service.build_state(validation_errors=result["validation_errors"], save_success=bool(result["ok"]))
    context.update({"url_for": admin_path_for, **state})
    return templates.TemplateResponse(request, "admin_console/setup_wizard.html", context)
