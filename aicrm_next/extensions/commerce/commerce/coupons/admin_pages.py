from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_shell_contract import admin_path_for, shell_context
from aicrm_next.platform.shared.safe_logging import safe_log_exception

from .application import CouponAdminApplication


router = APIRouter()
LOGGER = logging.getLogger(__name__)
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_ADMIN_SHELL_TEMPLATES_DIR = (
    Path(__file__).resolve().parents[4] / "app" / "admin_console" / "templates"
)
templates = Jinja2Templates(directory=[_TEMPLATES_DIR, _ADMIN_SHELL_TEMPLATES_DIR])

_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}


def _context(request: Request, *, page_title: str, page_summary: str) -> dict[str, Any]:
    context = shell_context(
        request=request,
        page_title=page_title,
        page_summary=page_summary,
        active_endpoint="api.admin_coupons_page",
    )
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": admin_path_for("api.admin_console_dashboard")},
        {"label": "优惠券", "href": "/admin/coupons"},
    ]
    return context


def _coupon_from(payload: dict[str, Any] | None) -> dict[str, Any]:
    value = (payload or {}).get("coupon") if isinstance(payload, dict) else None
    return dict(value or payload or {}) if isinstance(value or payload, dict) else {}


@router.get("/admin/coupons", response_class=HTMLResponse, name="api.admin_coupons_page")
def admin_coupons_page(request: Request):
    try:
        payload = CouponAdminApplication().list_coupons(limit=100, offset=0, q="", status="")
        items = list(payload.get("items") or [])
        total = int(payload.get("total") or len(items))
        page_error = ""
        status_code = 200
    except Exception as exc:
        safe_log_exception(LOGGER, "coupon admin list unavailable", exc)
        items = []
        total = 0
        page_error = "优惠券列表暂不可用，请稍后重试。"
        status_code = 503
    context = _context(
        request,
        page_title="优惠券管理",
        page_summary="创建、分享并跟踪普通商品和周期商品的无门槛优惠券。",
    )
    context.update(
        {
            "page_actions": [{"label": "新建优惠券", "href": "/admin/coupons/new", "variant": "primary"}],
            "coupons": jsonable_encoder(items),
            "coupon_total": total,
            "page_error": page_error,
        }
    )
    return templates.TemplateResponse(
        request,
        "admin_console/coupon_list.html",
        context,
        status_code=status_code,
        headers=_HEADERS,
    )


@router.get("/admin/coupons/new", response_class=HTMLResponse, name="api.admin_coupon_new_page")
def admin_coupon_new_page(request: Request):
    context = _context(
        request,
        page_title="新建优惠券",
        page_summary="配置减免金额、适用商品、领取窗口和有效期。",
    )
    context["breadcrumbs"].append({"label": "新建优惠券"})
    context.update(
        {
            "show_page_header": False,
            "coupon_form_mode": "new",
            "coupon_id": 0,
            "initial_coupon": {},
            "initial_coupon_json": "{}",
        }
    )
    return templates.TemplateResponse(
        request,
        "admin_console/coupon_form.html",
        context,
        headers=_HEADERS,
    )


@router.get("/admin/coupons/{coupon_id}/edit", response_class=HTMLResponse, name="api.admin_coupon_edit_page")
def admin_coupon_edit_page(request: Request, coupon_id: int):
    try:
        coupon = _coupon_from(CouponAdminApplication().get_coupon(coupon_id))
        page_error = ""
        status_code = 200
    except Exception as exc:
        safe_log_exception(LOGGER, "coupon admin edit unavailable", exc)
        coupon = {}
        page_error = "优惠券不存在或暂时无法读取。"
        status_code = 404
    context = _context(
        request,
        page_title="编辑优惠券",
        page_summary="维护优惠券文案、发行量和未冻结的使用规则。",
    )
    context["breadcrumbs"].append({"label": "编辑优惠券"})
    context.update(
        {
            "show_page_header": False,
            "coupon_form_mode": "edit",
            "coupon_id": int(coupon_id),
            "initial_coupon": jsonable_encoder(coupon),
            "initial_coupon_json": json.dumps(jsonable_encoder(coupon), ensure_ascii=False),
            "page_error": page_error,
        }
    )
    return templates.TemplateResponse(
        request,
        "admin_console/coupon_form.html",
        context,
        status_code=status_code,
        headers=_HEADERS,
    )


@router.get("/admin/coupons/{coupon_id}/data", response_class=HTMLResponse, name="api.admin_coupon_data_page")
def admin_coupon_data_page(request: Request, coupon_id: int):
    try:
        coupon = _coupon_from(CouponAdminApplication().get_coupon(coupon_id))
        claims_payload = CouponAdminApplication().list_claims(coupon_id, limit=100, offset=0)
        page_error = ""
        status_code = 200
    except Exception as exc:
        safe_log_exception(LOGGER, "coupon admin data unavailable", exc)
        coupon = {}
        claims_payload = {"items": [], "total": 0, "stats": {}}
        page_error = "优惠券数据暂不可用，请稍后重试。"
        status_code = 404
    context = _context(
        request,
        page_title="优惠券数据",
        page_summary="查看领取、预占、使用和过期情况。",
    )
    context["breadcrumbs"].append({"label": "优惠券数据"})
    context.update(
        {
            "coupon_id": int(coupon_id),
            "coupon": jsonable_encoder(coupon),
            "claims": jsonable_encoder(claims_payload.get("items") or []),
            "claim_total": int(claims_payload.get("total") or 0),
            "coupon_stats": jsonable_encoder(claims_payload.get("stats") or {}),
            "page_error": page_error,
        }
    )
    return templates.TemplateResponse(
        request,
        "admin_console/coupon_data.html",
        context,
        status_code=status_code,
        headers=_HEADERS,
    )
