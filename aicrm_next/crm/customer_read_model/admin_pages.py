from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_shell_contract import shell_context

from .application import GetAdminCustomerProfileQuery, GetCustomer360ProfileQuery, ListCustomersQuery
from .dto import ListCustomersRequest

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "frontend_compat" / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

ADMIN_CUSTOMERS_UNAVAILABLE_MESSAGE = "客户列表暂不可用：生产客户读源正在同步或数据库连接繁忙，请稍后刷新。"


def _admin_customer_payload_from_list_result(
    *,
    result: dict,
    keyword: str,
    owner: str,
    mobile: str,
    tag: str,
    limit: int,
    offset: int,
) -> tuple[dict, str]:
    unavailable = not result.get("ok", True) or result.get("source_status") == "production_unavailable"
    page_error = ADMIN_CUSTOMERS_UNAVAILABLE_MESSAGE if unavailable else ""
    customers = [] if unavailable else list(result.get("customers") or result.get("items") or [])
    total = 0 if unavailable else int(result.get("total") or result.get("count") or len(customers))
    return (
        {
            "filters": {"keyword": keyword, "owner": owner, "mobile": mobile, "tag": tag},
            "customers": customers,
            "pagination": {
                "total": total,
                "has_prev": offset > 0,
                "has_next": offset + limit < total,
                "prev_offset": max(offset - limit, 0),
                "next_offset": offset + limit,
            },
        },
        page_error,
    )


def _customer_profile_initial_section(tab: str) -> str:
    tab_map = {
        "tags": "customer-live-tags",
        "questionnaire": "customer-questionnaire-answers",
        "questionnaires": "customer-questionnaire-answers",
        "messages": "customer-message-records",
    }
    return tab_map.get(str(tab or "").strip().lower(), "")


def _customer_detail_payload_from_profile_result(result: dict, *, legacy_tab: str) -> dict | None:
    if not result.get("ok"):
        return None
    profile = dict(result.get("profile") or result.get("customer") or {})
    identity = dict(profile.get("identity") or {})
    unionid = str(profile.get("unionid") or identity.get("unionid") or "").strip()
    external_userid = str(profile.get("external_userid") or profile.get("user_id") or "").strip()
    if not unionid:
        return None
    profile["external_userid"] = external_userid
    profile["user_id"] = str(profile.get("user_id") or external_userid)
    profile["customer_name"] = str(profile.get("customer_name") or profile.get("remark") or unionid)
    profile["mobile"] = str(profile.get("mobile") or identity.get("mobile") or "")
    profile["owner"] = str(profile.get("owner") or profile.get("owner_display_name") or profile.get("owner_userid") or "")
    profile["owner_userid"] = str(profile.get("owner_userid") or "")
    profile["unionid"] = unionid
    return {
        "customer": profile,
        "lookup": dict(result.get("lookup") or {}),
        "initial_section": _customer_profile_initial_section(legacy_tab),
    }


def _customer_profile_urls(*, unionid: str, external_userid: str = "") -> dict[str, str]:
    query = urlencode({"unionid": unionid}) if unionid else urlencode({"external_userid": external_userid})
    return {
        "profile": f"/api/admin/customers/profile?{query}",
        "tags": f"/api/admin/customers/profile/tags?{query}",
        "questionnaire_answers": f"/api/admin/customers/profile/questionnaire-answers?{query}",
        "messages": f"/api/admin/customers/profile/messages?{query}",
    }


@router.get("/admin/customers", name="api.admin_console_customers")
def admin_customers(request: Request, keyword: str = "", owner: str = "", mobile: str = "", tag: str = "", offset: int = 0):
    limit = 50
    offset = max(int(offset or 0), 0)
    customer_query = ListCustomersRequest(
        owner_userid=owner or None,
        tag=tag or None,
        mobile=mobile or None,
        keyword=keyword or None,
        limit=limit,
        offset=offset,
    )
    result = ListCustomersQuery()(customer_query)
    customer_payload, page_error = _admin_customer_payload_from_list_result(
        result=result,
        keyword=keyword,
        owner=owner,
        mobile=mobile,
        tag=tag,
        limit=limit,
        offset=offset,
    )
    context = shell_context(
        request=request,
        page_title="客户激活 / 客户列表",
        page_summary="查看客户列表、筛选客户并打开客户档案。",
        active_endpoint="api.admin_console_customers",
    )
    context["page_error"] = page_error
    context["customer_payload"] = customer_payload
    return templates.TemplateResponse(request, "admin_console/customers.html", context)


@router.get("/admin/customers/{unionid}", name="api.admin_console_customer_detail")
def admin_customer_detail_page(request: Request, unionid: str, tab: str = ""):
    result = GetAdminCustomerProfileQuery()(unionid=unionid)
    if not result.get("ok"):
        result = GetAdminCustomerProfileQuery()(external_userid=unionid)
    payload = _customer_detail_payload_from_profile_result(result, legacy_tab=tab)
    if not payload:
        status_code = int(result.get("status_code") or 404)
        page_error = str(result.get("page_error") or result.get("error") or "未找到客户")
        context = shell_context(
            request=request,
            page_title="客户不存在",
            page_summary="当前客户编号没有查到对应客户。",
            active_endpoint="api.admin_console_customers",
        )
        context.update(
            {
                "breadcrumbs": [
                    {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
                    {"label": "客户", "href": "/admin/customers"},
                    {"label": unionid, "href": ""},
                ],
                "actions": [{"label": "返回客户列表", "href": "/admin/customers", "variant": "secondary"}],
                "state_title": "客户不存在",
                "state_body": "请确认客户编号是否正确，或稍后重试。",
                "state_items": ["检查客户编号是否输入正确", "确认当前环境已经同步到该客户数据"],
                "table_rows": [],
                "page_error": page_error,
            }
        )
        return templates.TemplateResponse(request, "admin_console/placeholder.html", context, status_code=status_code)

    customer = payload["customer"]
    customer_name = str(customer.get("customer_name") or unionid)
    context = shell_context(
        request=request,
        page_title=customer_name,
        page_summary="查看客户基础资料、实时标签、问卷问答和聊天记录。",
        active_endpoint="api.admin_console_customers",
    )
    context.update(
        {
            "breadcrumbs": [
                {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
                {"label": "客户", "href": "/admin/customers"},
                {"label": customer_name, "href": ""},
            ],
            "customer_payload": payload,
            "page_error": str(result.get("page_error") or ""),
            "admin_action_token": "",
            "action_result": {},
            "customer_profile_urls": _customer_profile_urls(
                unionid=str(customer.get("unionid") or unionid),
                external_userid=str(customer.get("external_userid") or ""),
            ),
        }
    )
    return templates.TemplateResponse(request, "admin_console/customer_detail.html", context)


@router.get("/admin/customer-360/{unionid}", name="api.admin_customer_360_page")
def admin_customer_360_page(request: Request, unionid: str):
    result = GetCustomer360ProfileQuery()(unionid)
    if not result.get("ok"):
        status_code = int(result.get("status_code") or 404)
        page_error = str(result.get("page_error") or result.get("error") or "未找到客户 360 档案")
        context = shell_context(
            request=request,
            page_title="Customer 360 不可用",
            page_summary="当前 unionid 没有查到可展示的 Customer 360 read model。",
            active_endpoint="api.admin_console_customers",
        )
        context.update(
            {
                "breadcrumbs": [
                    {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
                    {"label": "客户", "href": "/admin/customers"},
                    {"label": "Customer 360", "href": ""},
                ],
                "actions": [{"label": "返回客户列表", "href": "/admin/customers", "variant": "secondary"}],
                "state_title": "Customer 360 不可用",
                "state_body": page_error,
                "state_items": ["确认 unionid 是否正确", "确认 Customer Read Model 已同步"],
                "table_rows": [],
                "page_error": page_error,
            }
        )
        return templates.TemplateResponse(request, "admin_console/placeholder.html", context, status_code=status_code)

    context = shell_context(
        request=request,
        page_title=f"Customer 360 · {unionid}",
        page_summary="按 unionid 查看身份、成交、问卷、消息、运营状态和风险标记。",
        active_endpoint="api.admin_console_customers",
    )
    context.update(
        {
            "breadcrumbs": [
                {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
                {"label": "客户", "href": "/admin/customers"},
                {"label": "Customer 360", "href": ""},
            ],
            "customer_360": result,
            "page_error": str(result.get("page_error") or ""),
        }
    )
    return templates.TemplateResponse(request, "admin_console/customer_360.html", context)
