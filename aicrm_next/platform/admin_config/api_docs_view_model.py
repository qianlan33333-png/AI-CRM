from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from fastapi.routing import APIRoute

_METHOD_ORDER = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}
_SKIPPED_METHODS = {"HEAD", "OPTIONS"}


@dataclass(frozen=True)
class _GroupSpec:
    id: str
    title: str
    description: str


_GROUPS = [
    _GroupSpec("system-mcp", "系统 / MCP", "健康检查、系统探针与 MCP 网关入口。"),
    _GroupSpec("auth-callback", "认证 / 回调", "后台登录、企业微信授权回调、企微事件与支付回调。"),
    _GroupSpec("customer-identity-sidebar", "客户 / 身份 / 侧边栏", "客户列表、客户档案、最近消息、身份解析和侧边栏上下文。"),
    _GroupSpec("channels", "渠道码中心", "渠道资产、渠道联系人、绑定关系、分享链接与欢迎素材。"),
    _GroupSpec("questionnaires", "问卷", "后台问卷管理、H5 问卷访问、提交结果和微信网页授权。"),
    _GroupSpec("user-ops", "用户运营 / 激活", "用户运营看板、批量发送、免打扰、发送记录和激活 webhook。"),
    _GroupSpec("automation", "自动化运营", "自动化转化配置、任务、工作流、成员、Agent 产物和执行记录。"),
    _GroupSpec("group-ops", "群运营计划", "群运营计划、群资产同步、节点编排、定时执行和 webhook。"),
    _GroupSpec("wecom-tags", "企微标签", "企微客户标签、标签组、同步和 live 标记接口。"),
    _GroupSpec("materials-send-content", "素材 / 发送内容", "图片、附件、小程序素材库，以及发送内容校验、预览和素材选择器。"),
    _GroupSpec("commerce", "交易 / 商品", "商品页、下单、订单查询、微信支付、支付宝与后台交易管理。"),
    _GroupSpec("ai-assist-compat", "AI 助手 / 兼容代理", "AI 助手契约、云编排、定时任务兼容代理与外部适配入口。"),
    _GroupSpec("push-center", "推送中心", "统一推送任务查询与执行明细。"),
    _GroupSpec("external-effects", "外部动作队列排障", "External Effect Queue 的排障查询、diagnostics、run-due 和测试 receiver API。"),
    _GroupSpec("other", "其他 API", "当前 FastAPI 注册表中尚未归入固定业务分组的公开 API。"),
]
_GROUP_BY_ID = {group.id: group for group in _GROUPS}

_PATH_LABELS = {
    "health": "健康检查",
    "mcp": "MCP 网关",
    "login": "登录",
    "logout": "退出登录",
    "callback": "回调",
    "events": "事件接收",
    "links": "身份链路",
    "customers": "客户",
    "timeline": "时间线",
    "messages": "消息",
    "sidebar": "侧边栏",
    "identity": "身份解析",
    "channels": "渠道",
    "qrcode": "二维码",
    "questionnaires": "问卷",
    "preflight": "预检",
    "submit": "提交",
    "result": "结果",
    "oauth": "OAuth",
    "user-ops": "用户运营",
    "overview": "概览",
    "batch-send": "批量发送",
    "do-not-disturb": "免打扰",
    "send-records": "发送记录",
    "automation-conversion": "自动化运营",
    "contract": "运行契约",
    "pools": "池",
    "task-groups": "任务组",
    "workflows": "工作流",
    "workflow-nodes": "工作流节点",
    "tasks": "任务",
    "agents": "Agent",
    "agent-outputs": "Agent 产物",
    "agent-runs": "Agent 运行",
    "members": "成员",
    "execution-records": "执行记录",
    "group-ops": "群运营",
    "plans": "计划",
    "owners": "负责人",
    "groups": "群资产",
    "sync": "同步",
    "run-due": "执行到期任务",
    "webhooks": "Webhook",
    "business-profile": "客户商业档案",
    "commerce-summary": "商业摘要",
    "wecom": "企微",
    "tags": "标签",
    "tag-groups": "标签组",
    "live": "Live",
    "image-library": "图片素材",
    "attachment-library": "附件素材",
    "miniprogram-library": "小程序素材",
    "send-content": "发送内容",
    "material-picker": "素材选择器",
    "wechat-pay": "微信支付",
    "alipay": "支付宝",
    "products": "商品",
    "orders": "订单",
    "payments": "支付",
    "refunds": "退款",
    "exports": "数据导出",
    "checkout": "下单",
    "notify": "支付通知",
    "ai-assist": "AI 助手",
    "cloud-orchestrator": "云编排",
    "jobs": "后台任务",
    "push-center": "推送中心",
    "external-effects": "外部动作队列",
    "troubleshooting": "排障",
    "diagnostics": "诊断",
    "test-receipts": "测试回执",
    "test-receiver": "测试接收端",
    "test-loopback": "Loopback 测试",
}

_AUTH_LABEL_MD = {
    "session": "Session Cookie 登录态",
    "public": "公开访问",
    "bearer": "Bearer Token",
    "signature": "签名 / Webhook Token",
}


def _normalize_path(path: str) -> str:
    return re.sub(r"\{([^}:]+):[^}]+\}", r"{\1}", path)


def _path_for_route(route: APIRoute, prefix: str = "") -> str:
    path = _join_paths(prefix, str(getattr(route, "path_format", None) or route.path))
    return _normalize_path(path)


def _join_paths(prefix: str, path: str) -> str:
    if not prefix:
        return path
    if not path:
        return prefix
    return f"{prefix.rstrip('/')}/{path.lstrip('/')}"


def _should_document(path: str) -> bool:
    if path.startswith("/static"):
        return False
    if path.startswith("/admin/"):
        return False
    if path == "/admin":
        return False
    return (
        path == "/health"
        or path == "/mcp"
        or path.startswith("/api/")
        or path.startswith("/wecom/")
        or path in {"/login", "/logout"}
        or path.startswith("/auth/wecom/")
        or path.startswith("/p/")
        or path.startswith("/pay/")
        or path.startswith("/s/")
    )


def _group_id_for(path: str) -> str:
    if path in {"/health", "/api/system/health", "/mcp"} or path.startswith("/api/frontend-compat/"):
        return "system-mcp"
    if (
        path in {"/login", "/logout"}
        or path.startswith("/auth/wecom/")
        or path.startswith("/wecom/")
        or path.startswith("/api/wecom/events")
        or path.endswith("/notify")
        or path.endswith("/return")
    ):
        return "auth-callback"
    if (
        path.startswith("/api/customers")
        or path.startswith("/api/messages/")
        or path.startswith("/api/sidebar/")
        or path.startswith("/api/identity/")
        or path.startswith("/api/admin/identity/")
        or path.startswith("/api/admin/customers/")
        or path.startswith("/api/admin/customers/profile")
    ):
        return "customer-identity-sidebar"
    if path.startswith("/api/admin/webhooks"):
        return "auth-callback"
    if path.startswith("/api/admin/exports"):
        return "system-mcp"
    if path.startswith("/api/admin/channels") or path.startswith("/api/admin/channel-welcome-materials"):
        return "channels"
    if path.startswith("/api/admin/questionnaires") or path.startswith("/api/h5/questionnaires") or path.startswith("/api/h5/wechat/oauth"):
        return "questionnaires"
    if path.startswith("/api/admin/user-ops") or path.startswith("/api/customer-automation/activation-webhook"):
        return "user-ops"
    if path.startswith("/api/admin/automation-conversion/group-ops") or path.startswith("/api/automation/group-ops"):
        return "group-ops"
    if path.startswith("/api/admin/wecom/tags") or path.startswith("/api/admin/wecom/tag-groups"):
        return "wecom-tags"
    if (
        path.startswith("/api/admin/image-library")
        or path.startswith("/api/admin/attachment-library")
        or path.startswith("/api/admin/miniprogram-library")
        or path.startswith("/api/admin/send-content")
        or path.startswith("/api/admin/material-picker")
    ):
        return "materials-send-content"
    if (
        path.startswith("/api/admin/wechat-pay")
        or path.startswith("/api/admin/service-period-products")
        or path.startswith("/api/admin/alipay")
        or path.startswith("/api/admin/orders")
        or path.startswith("/api/admin/payments")
        or path.startswith("/api/admin/refunds")
        or path.startswith("/api/products")
        or path.startswith("/api/h5/service-period-products")
        or path.startswith("/p/")
        or path.startswith("/pay/")
        or path.startswith("/s/")
        or path.startswith("/api/checkout/")
        or path.startswith("/api/orders/")
        or path.startswith("/api/wechat-pay/")
        or path.startswith("/api/alipay/")
    ):
        return "commerce"
    if (
        path.startswith("/api/admin/ai-assist")
        or path.startswith("/api/admin/ai-audience")
        or path.startswith("/api/ai/audience")
        or path.startswith("/api/admin/cloud-orchestrator")
    ):
        return "ai-assist-compat"
    if path.startswith("/api/admin/push-center"):
        return "push-center"
    if path.startswith("/api/admin/external-effects") or path.startswith("/api/external-effects"):
        return "external-effects"
    if path.startswith("/api/admin/automation-conversion"):
        return "automation"
    return "other"


def _slug(value: str) -> str:
    normalized = value.lower().replace("_", "-")
    normalized = re.sub(r"\{([^}]+)\}", r"\1", normalized)
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return normalized or "endpoint"


def _type_name(value: Any) -> str:
    if value is None:
        return "string"
    name = getattr(value, "__name__", None)
    if name:
        return name
    text = str(value).replace("typing.", "")
    text = text.replace("<class '", "").replace("'>", "")
    return text.rsplit(".", 1)[-1] or "string"


def _param_description(param: Any, location: str) -> str:
    field_info = getattr(param, "field_info", None)
    description = str(getattr(field_info, "description", "") or "").strip()
    if description:
        return description
    return f"{location} 参数"


def _required(param: Any) -> bool:
    required = getattr(param, "required", None)
    if isinstance(required, bool):
        return required
    is_required = getattr(param, "is_required", None)
    if callable(is_required):
        try:
            return bool(is_required())
        except TypeError:
            return False
    return False


def _route_params(route: APIRoute) -> list[dict[str, Any]]:
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return []

    params: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def append(items: list[Any], location: str) -> None:
        for param in items:
            name = str(getattr(param, "name", "") or "").strip()
            if not name or (location, name) in seen:
                continue
            seen.add((location, name))
            params.append(
                {
                    "name": name,
                    "type": _type_name(getattr(param, "type_", None)),
                    "required": _required(param),
                    "location": location,
                    "description": _param_description(param, location),
                }
            )

    append(list(getattr(dependant, "path_params", []) or []), "path")
    append(list(getattr(dependant, "query_params", []) or []), "query")
    append(list(getattr(dependant, "body_params", []) or []), "body")
    append(list(getattr(dependant, "header_params", []) or []), "header")
    return params


def _sample_path(path: str) -> str:
    samples = {
        "external_userid": "wm_sample_external_userid",
        "record_id": "record_sample",
        "questionnaire_id": "1",
        "submission_id": "1",
        "channel_id": "1",
        "task_id": "1",
        "workflow_id": "1",
        "node_id": "1",
        "template_id": "1",
        "member_id": "1",
        "output_id": "output_sample",
        "run_id": "run_sample",
        "plan_id": "1",
        "chat_id": "chat_sample",
        "tag_id": "tag_sample",
        "group_id": "group_sample",
        "image_id": "1",
        "attachment_id": "1",
        "item_id": "1",
        "product_id": "1",
        "order_id": "1",
        "order_no": "ORDER_SAMPLE",
        "page_slug": "sample-product",
        "slug": "sample-questionnaire",
        "webhook_key": "webhook_sample",
        "segment_key": "segment_sample",
        "variant_key": "thumb",
        "path": "sample",
    }

    def repl(match: re.Match[str]) -> str:
        return samples.get(match.group(1), f"{match.group(1)}_sample")

    return re.sub(r"\{([^}]+)\}", repl, path)


def _request_example(method: str, path: str, params: list[dict[str, Any]]) -> str:
    sample = _sample_path(path)
    query_params = [param for param in params if param["location"] == "query" and param["required"]][:3]
    if method == "GET" and query_params:
        query = "&".join(f"{param['name']}={param['name']}_sample" for param in query_params)
        sample = f"{sample}?{query}"
    if method == "GET":
        return f"GET {sample}"
    body_params = [param for param in params if param["location"] == "body"]
    body = {"example": True}
    if body_params:
        body = {param["name"]: f"{param['name']}_payload" for param in body_params[:3]}
    return f"{method} {sample}\nContent-Type: application/json\n\n{json.dumps(body, ensure_ascii=False, indent=2)}"


def _response_example(path: str) -> str:
    if path.startswith("/p/"):
        return "HTTP 200 text/html 商品页"
    if path.startswith("/s/"):
        return "HTTP 200 text/html 周期商品页"
    if path in {"/login", "/logout"} or path.startswith("/auth/wecom/"):
        return "HTTP 302 → 目标页面"
    return json.dumps({"ok": True, "route_owner": "ai_crm_next"}, ensure_ascii=False)


def _curl(method: str, path: str, auth: str, params: list[dict[str, Any]]) -> str:
    sample_path = _sample_path(path)
    parts = [f"curl -X {method} 'https://<YOUR_DOMAIN>{sample_path}'"]
    if auth == "session":
        parts.append("  -H 'Cookie: session=<SESSION_COOKIE>'")
    elif auth == "bearer":
        parts.append("  -H 'Authorization: Bearer <TOKEN>'")
    elif auth == "signature":
        parts.append("  -H 'X-AICRM-Signature: <SIGNATURE>'")
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        body = {"example": True}
        body_params = [param for param in params if param["location"] == "body"]
        if body_params:
            body = {param["name"]: f"{param['name']}_payload" for param in body_params[:3]}
        parts.append("  -H 'Content-Type: application/json'")
        parts.append(f"  -d '{json.dumps(body, ensure_ascii=False)}'")
    return " \\\n".join(parts)


def _resource_label(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part and not part.startswith("{")]
    for part in reversed(parts):
        label = _PATH_LABELS.get(part)
        if label:
            return label
    return "接口"


def _summary_for(method: str, path: str, route: APIRoute) -> str:
    if route.summary:
        return str(route.summary)
    label = _resource_label(path)
    endpoint_name = str(getattr(route.endpoint, "__name__", "") or "").replace("_", " ")
    if method == "GET":
        action = "获取"
    elif method == "POST":
        action = "创建或执行"
    elif method in {"PUT", "PATCH"}:
        action = "更新"
    elif method == "DELETE":
        action = "删除"
    else:
        action = method
    if endpoint_name and "fixture" not in endpoint_name.lower() and endpoint_name not in {"legacy production compat routes"}:
        return f"{action}{label}（{endpoint_name}）"
    return f"{action}{label}"


def _auth_for(method: str, path: str, route: APIRoute) -> str:
    if path in {"/health", "/api/system/health", "/login"} or path.startswith("/auth/wecom/"):
        return "public"
    if path == "/logout":
        return "session"
    if path == "/mcp":
        return "bearer"
    if path.startswith("/api/admin/webhooks"):
        return "session"
    if (
        path.startswith("/api/h5/questionnaires")
        or path.startswith("/api/h5/wechat/oauth")
        or path.startswith("/api/products")
        or path.startswith("/api/h5/service-period-products")
        or path.startswith("/p/")
        or path.startswith("/pay/")
        or path.startswith("/s/")
        or path.startswith("/api/checkout/")
        or path.startswith("/api/orders/")
    ):
        return "public"
    if "callback" in path or "notify" in path or "webhook" in path or path.startswith("/api/wecom/events"):
        return "signature"
    return "session"


def _endpoint_from_route(route: APIRoute, method: str, path: str) -> dict[str, Any]:
    params = _route_params(route)
    auth = _auth_for(method, path, route)
    group = _GROUP_BY_ID[_group_id_for(path)]
    summary = _summary_for(method, path, route)
    description = (route.description or "").strip()
    if not description:
        description = f"{group.title}接口：{summary}。数据来自当前 AI-CRM Next FastAPI 路由注册表。"
    return {
        "id": f"{method.lower()}-{_slug(path)}",
        "method": method,
        "path": path,
        "summary": summary,
        "description": description,
        "auth": auth,
        "params": params,
        "request_example": _request_example(method, path, params),
        "response_example": _response_example(path),
        "curl_example": _curl(method, path, auth, params),
    }


def _iter_api_routes(routes: Iterable[Any], prefix: str = "") -> Iterable[tuple[APIRoute, str]]:
    for route in routes:
        context = getattr(route, "include_context", None)
        included_router = getattr(route, "original_router", None) or getattr(context, "included_router", None)
        if included_router is not None and hasattr(included_router, "routes"):
            yield from _iter_api_routes(getattr(included_router, "routes", ()), _join_paths(prefix, getattr(context, "prefix", "")))
            continue
        if isinstance(route, APIRoute):
            yield route, prefix


def _iter_route_endpoints(routes: Iterable[Any]) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for route, prefix in _iter_api_routes(routes):
        path = _path_for_route(route, prefix)
        if not _should_document(path):
            continue
        for method in sorted(route.methods or [], key=lambda item: _METHOD_ORDER.get(item, 99)):
            if method in _SKIPPED_METHODS:
                continue
            key = (method, path)
            if key in seen:
                continue
            seen.add(key)
            endpoints.append(_endpoint_from_route(route, method, path))
    return endpoints


def _build_groups(endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {group.id: [] for group in _GROUPS}
    for endpoint in endpoints:
        grouped[_group_id_for(endpoint["path"])].append(endpoint)

    groups: list[dict[str, Any]] = []
    for spec in _GROUPS:
        items = sorted(grouped[spec.id], key=lambda ep: (ep["path"], _METHOD_ORDER.get(ep["method"], 99)))
        if not items:
            continue
        groups.append(
            {
                "id": spec.id,
                "title": spec.title,
                "description": spec.description,
                "endpoints": items,
                "subsections": [],
            }
        )
    return groups


def _flat_endpoints(group: dict[str, Any]) -> list[dict[str, Any]]:
    endpoints = list(group.get("endpoints") or [])
    for sub in group.get("subsections") or []:
        endpoints.extend(sub.get("endpoints") or [])
    return endpoints


def _build_quick_reference(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for group in groups:
        for ep in _flat_endpoints(group):
            refs.append(
                {
                    "method": ep["method"],
                    "path": ep["path"],
                    "summary": ep["summary"],
                    "auth": ep["auth"],
                    "group_title": group["title"],
                    "anchor": ep["id"],
                }
            )
    return refs


def _params_to_markdown(params: list[dict[str, Any]]) -> str:
    lines = ["| 参数 | 类型 | 位置 | 必填 | 说明 |", "|---|---|---|---|---|"]
    for param in params:
        req = "是" if param.get("required") else "否"
        desc = str(param.get("description") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{param['name']}` | {param.get('type', '')} | {param.get('location', '')} | {req} | {desc} |")
    return "\n".join(lines)


def _endpoint_to_markdown(ep: dict[str, Any]) -> str:
    lines = [f"### `{ep['method']} {ep['path']}` — {ep['summary']}", ""]
    if ep.get("description"):
        lines.extend([ep["description"], ""])
    lines.extend([f"- **认证**: {_AUTH_LABEL_MD.get(ep.get('auth'), ep.get('auth') or '—')}", ""])
    if ep.get("params"):
        lines.extend([_params_to_markdown(ep["params"]), ""])
    for label, fence, key in [
        ("请求示例", "", "request_example"),
        ("响应示例", "json", "response_example"),
        ("curl", "bash", "curl_example"),
    ]:
        if ep.get(key):
            lines.extend([f"**{label}**", "", f"```{fence}", ep[key], "```", ""])
    return "\n".join(lines).rstrip() + "\n"


def _group_to_markdown(group: dict[str, Any]) -> str:
    parts = [f"## {group['title']}", ""]
    if group.get("description"):
        parts.extend([group["description"], ""])
    for ep in _flat_endpoints(group):
        parts.extend([_endpoint_to_markdown(ep), ""])
    return "\n".join(parts).rstrip() + "\n"


def _full_doc_markdown(groups: list[dict[str, Any]]) -> str:
    parts = [
        "# AI-CRM Next API 文档",
        "",
        "本文档由当前 FastAPI 路由注册表生成，供开发者和 AI Agent 接入使用。",
        "",
    ]
    for group in groups:
        parts.extend([_group_to_markdown(group), ""])
    return "\n".join(parts).rstrip() + "\n"


def _build_markdown_data(groups: list[dict[str, Any]]) -> dict[str, Any]:
    data = {"endpoints": {}, "groups": {}, "full": _full_doc_markdown(groups)}
    for group in groups:
        data["groups"][group["id"]] = _group_to_markdown(group)
        for ep in _flat_endpoints(group):
            data["endpoints"][ep["id"]] = _endpoint_to_markdown(ep)
    return data


def _size_label(value: str) -> str:
    size = len(value.encode("utf-8"))
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{max(1, round(size / 1024))} KB"


def build_api_docs_view_model(*, routes: Iterable[Any]) -> dict[str, Any]:
    """Build docs from the routes composed for the active application profile."""
    groups = _build_groups(_iter_route_endpoints(routes))
    quick_reference = _build_quick_reference(groups)
    markdown_data = _build_markdown_data(groups)
    return {
        "endpoint_groups": groups,
        "quick_reference": quick_reference,
        "markdown_data": markdown_data,
        "endpoint_count": len(quick_reference),
        "markdown_size_label": _size_label(markdown_data["full"]),
        "source_status": "fastapi_route_map",
    }


__all__ = ["build_api_docs_view_model"]
