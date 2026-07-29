from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_shell_contract import admin_path_for, shell_context

from .application import get_run, get_strategy, list_strategies
from .markdown_renderer import render_markdown
from .feature_flags import operation_context_v1_enabled
from .strategy_context import get_strategy_context


router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "app" / "admin_console" / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

_WEEKLY_PROGRESS_STEPS = (
    ("planning", "任务准备"),
    ("delivery", "发送执行"),
    ("review", "结果复盘"),
    ("optimization", "优化确认"),
)


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _detail_href(strategy_key: object, run_key: object | None = None) -> str:
    params = {"strategy_key": str(strategy_key or "")}
    if run_key is None:
        return admin_path_for("api.admin_operation_cycle_strategy_page", **params)
    return admin_path_for("api.admin_operation_cycle_run_page", run_key=str(run_key or ""), **params)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _business_timezone(name: object) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "Asia/Shanghai"))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Shanghai")


def _weekly_progress(item: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    business_tz = _business_timezone(item.get("timezone"))
    local_now = now.astimezone(business_tz)
    week_start = local_now.date() - timedelta(days=local_now.weekday())
    week_end = week_start + timedelta(days=6)
    latest_run_at = _parse_datetime(item.get("latest_run_at"))
    has_current_week_run = bool(latest_run_at and week_start <= latest_run_at.astimezone(business_tz).date() <= week_end)
    states = {key: "pending" for key, _label in _WEEKLY_PROGRESS_STEPS}

    if has_current_week_run:
        execution_stage = str(item.get("execution_stage") or "scheduled").strip().lower()
        review_status = str(item.get("review_status") or "not_created").strip().lower()
        delivery_status = str(item.get("delivery_status") or "not_started").strip().lower()
        optimization_status = str(item.get("optimization_status") or "none").strip().lower()

        planning_done = (
            review_status == "approved"
            or execution_stage in {"delivery", "observing", "postmortem", "closed"}
            or delivery_status in {"dispatching", "partial", "completed", "failed", "cancelled"}
        )
        if not planning_done:
            states["planning"] = "active"
        else:
            states["planning"] = "completed"
            delivery_done = delivery_status in {"partial", "completed"}
            if not delivery_done:
                states["delivery"] = "active"
            else:
                states["delivery"] = "completed"
                review_done = execution_stage in {"postmortem", "closed"}
                if not review_done:
                    states["review"] = "active"
                else:
                    states["review"] = "completed"
                    optimization_done = execution_stage == "closed" or optimization_status in {
                        "accepted",
                        "rejected",
                        "applied",
                    }
                    if optimization_done:
                        states["optimization"] = "completed"
                    else:
                        states["optimization"] = "active"

    return {
        "has_current_week_run": has_current_week_run,
        "steps": [{"key": key, "label": label, "state": states[key]} for key, label in _WEEKLY_PROGRESS_STEPS],
    }


def _strategy_summaries(payload: dict[str, Any], *, now: datetime | None = None) -> list[dict[str, Any]]:
    effective_now = now or _utc_now()
    return [
        {
            **item,
            "detail_href": _detail_href(item.get("strategy_key")),
            "weekly_progress": _weekly_progress(item, now=effective_now),
        }
        for item in _plain(payload.get("items") or [])
        if isinstance(item, dict)
    ]


def _run_summaries(payload: dict[str, Any], strategy_key: str) -> list[dict[str, Any]]:
    return [{**item, "detail_href": _detail_href(strategy_key, item.get("run_key"))} for item in _plain(payload.get("items") or []) if isinstance(item, dict)]


def _run_view(run: dict[str, Any]) -> dict[str, Any]:
    funnel = run.get("funnel") or {}
    planned = funnel.get("planned_target_count") or {}
    sent = funnel.get("effective_sent_count") or {}
    failed = funnel.get("failed_count") or {}
    failure_classification = str(failed.get("classification") or "").strip().lower()
    retryable_failure = failure_classification == "failed_retryable"
    completion_rate = None
    if planned.get("status") == "observed" and sent.get("status") == "observed" and planned.get("value"):
        completion_rate = round(float(sent["value"]) * 100 / float(planned["value"]), 2)
    return {
        **run,
        "plan": {
            "version_label": run.get("plan_version") or "",
            "status": run.get("plan_status") or "",
            "source": run.get("plan_source") or "",
            "plan_source": run.get("plan_source") or "",
            "review_status": run.get("review_status") or "",
            "target_count": planned.get("value") if planned.get("status") in {"observed", "partial_lower_bound"} else None,
            "target_count_state": planned.get("status") or "unknown",
            "conflict_notice": "计划元数据与实际发送事实存在冲突" if run.get("fact_conflict") else "",
        },
        "delivery": {
            "status": run.get("delivery_status") or "",
            "source": sent.get("data_source") or failed.get("data_source") or "",
            "source_label": sent.get("data_source") or failed.get("data_source") or "",
            "effective_sent_count": sent.get("value"),
            "effective_sent_state": sent.get("status") or "unknown",
            "failed_count": failed.get("value"),
            "failed_state": failed.get("status") or "unknown",
            "failure_classification": failure_classification,
            "retryable_failed_count": failed.get("value") if retryable_failure else None,
            "retryable_failed_state": failed.get("status") if retryable_failure else "unknown",
            "completion_rate": completion_rate,
            "completion_rate_state": "observed" if completion_rate is not None else "unknown",
            "failure_summary": failed.get("limitation") or "",
        },
    }


def _observation_windows(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for raw_metric in metrics:
        metric = {
            **raw_metric,
            "value_state": raw_metric.get("value_state") or raw_metric.get("value_status") or "unknown",
            "limitation": raw_metric.get("limitation") or "；".join(raw_metric.get("limitations") or []),
        }
        window = str(metric.get("observation_window") or "未指定窗口")
        group = grouped.setdefault(
            window,
            {
                "label": window,
                "window_label": window,
                "status": metric.get("value_state") or "unknown",
                "value_state": metric.get("value_state") or "unknown",
                "data_quality": metric.get("data_quality") or "unknown",
                "window_start": metric.get("window_start"),
                "window_end": metric.get("window_end"),
                "limitation": metric.get("limitation") or metric.get("limitations") or "",
                "metrics": [],
            },
        )
        group["metrics"].append(metric)
        if group["status"] != "observed" and metric.get("value_state") == "observed":
            group["status"] = "observed"
            group["value_state"] = "observed"
    return list(grouped.values())


def _attempt_view(item: dict[str, Any]) -> dict[str, Any]:
    summary = item.get("summary")
    if isinstance(summary, dict):
        summary = summary.get("summary") or summary.get("note") or summary.get("detail") or ""
    return {**item, "summary": summary or "", "finished_at": item.get("finished_at") or item.get("ended_at")}


def _stage_view(item: dict[str, Any]) -> dict[str, Any]:
    summary = item.get("summary")
    if isinstance(summary, dict):
        summary = summary.get("summary") or summary.get("note") or summary.get("detail") or ""
    return {**item, "label": item.get("label") or item.get("stage"), "summary": summary or ""}


def _retrospective_view(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "findings": payload.get("findings") or payload.get("observations") or []}


def _next_iteration_view(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "changes": payload.get("changes") or payload.get("actions") or [],
        "rationale": payload.get("rationale") or payload.get("hypothesis") or "",
        "applied_version": payload.get("applied_version") or payload.get("applied_strategy_version"),
    }


@router.get("/admin/operation-cycles", name="api.admin_operation_cycles_page", response_class=HTMLResponse)
def admin_operation_cycles_page(request: Request):
    payload = _plain(list_strategies(limit=100, offset=0))
    context = shell_context(
        request=request,
        page_title="运营闭环",
        page_summary="查看每项运营任务的本周进度。任务由 Agent 创建。",
        active_endpoint="api.admin_operation_cycles_page",
    )
    context.update(
        {
            "breadcrumbs": [
                {"label": "客户管理后台", "href": admin_path_for("api.admin_console_dashboard")},
                {"label": "运营闭环", "href": ""},
            ],
            "strategy_summaries": _strategy_summaries(payload),
        }
    )
    return templates.TemplateResponse(request, "admin_shell/operation_cycles_list.html", context)


@router.get(
    "/admin/operation-cycles/{strategy_key}/runs/{run_key}",
    name="api.admin_operation_cycle_run_page",
    response_class=HTMLResponse,
)
def admin_operation_cycle_run_page(request: Request, strategy_key: str, run_key: str):
    payload = _plain(get_run(run_key))
    if not payload:
        return _not_found(request, "未找到这次运行")
    run = _run_view(payload.get("run") or {})
    if str(run.get("strategy_key") or strategy_key) != strategy_key:
        return _not_found(request, "这次运行不属于当前策略")
    strategy_payload = _plain(get_strategy(strategy_key)) or {}
    strategy = strategy_payload.get("strategy") or {"strategy_key": strategy_key, "title": strategy_key}
    metrics = [item for item in payload.get("metrics") or [] if isinstance(item, dict)]
    context = shell_context(
        request=request,
        page_title=str(run.get("label") or run_key),
        page_summary="逐步还原任务目标、尝试、人审、发送事实、分窗口结果、复盘与优化采纳。",
        active_endpoint="api.admin_operation_cycles_page",
    )
    context.update(
        {
            "breadcrumbs": [
                {"label": "客户管理后台", "href": admin_path_for("api.admin_console_dashboard")},
                {"label": "运营闭环", "href": admin_path_for("api.admin_operation_cycles_page")},
                {"label": str(strategy.get("title") or strategy_key), "href": _detail_href(strategy_key)},
                {"label": str(run.get("label") or run_key), "href": ""},
            ],
            "strategy": {**strategy, "detail_href": _detail_href(strategy_key)},
            "run": {**run, "detail_href": _detail_href(strategy_key, run_key)},
            "attempts": [_attempt_view(item) for item in payload.get("attempts") or []],
            "stages": [_stage_view(item) for item in payload.get("stages") or []],
            "funnel": run.get("funnel") or {},
            "observation_windows": _observation_windows(metrics),
            "retrospective": _retrospective_view(payload.get("retrospective") or {}),
            "next_iteration": _next_iteration_view(payload.get("next_iteration") or {}),
            "references": payload.get("references") or [],
            "snapshot": payload.get("snapshot") or {},
        }
    )
    return templates.TemplateResponse(request, "admin_shell/operation_cycles_run.html", context)


@router.get(
    "/admin/operation-cycles/{strategy_key}",
    name="api.admin_operation_cycle_strategy_page",
    response_class=HTMLResponse,
)
def admin_operation_cycle_strategy_page(request: Request, strategy_key: str):
    payload = _plain(get_strategy(strategy_key))
    if not payload:
        return _not_found(request, "未找到这个运营策略")
    strategy = payload.get("strategy") or {}
    section = str(request.query_params.get("section") or "broadcast_details").strip()
    allowed_sections = {
        "broadcast_details": "本周发送数据",
        "retrospective_details": "本周复盘明细",
        "execution_strategy": "下周执行策略",
        "history": "历史发送记录",
        "formal_strategy": "正式策略",
        "optimization_records": "优化记录",
    }
    if section not in allowed_sections:
        section = "broadcast_details"
    detail_href = _detail_href(strategy_key)
    detail_nav = [
        {
            "key": key,
            "label": label,
            "href": detail_href if key == "broadcast_details" else f"{detail_href}?section={key}",
        }
        for key, label in allowed_sections.items()
    ]
    documents = payload.get("documents") or {}
    active_document = documents.get(section) if section != "history" else {}
    if not isinstance(active_document, dict):
        active_document = {}
    formal_context: dict[str, Any] = {}
    optimization_context: dict[str, Any] = {}
    if operation_context_v1_enabled() and section in {"formal_strategy", "optimization_records"}:
        mode = "execution" if section == "formal_strategy" else "optimization"
        formal_payload = get_strategy_context(strategy_key, mode=mode, limit=3) or {}
        if section == "formal_strategy":
            formal_context = _plain(formal_payload)
        else:
            optimization_context = _plain(formal_payload)
    formal_documents = []
    document_pack = ((formal_context.get("execution") or {}).get("document_pack") or {})
    for key, label in (
        ("execution_guide", "执行指南"),
        ("copy_guide", "话术指南"),
        ("measurement_guide", "度量指南"),
    ):
        document = document_pack.get(key) if isinstance(document_pack, dict) else {}
        if isinstance(document, dict):
            formal_documents.append(
                {
                    "key": key,
                    "label": label,
                    **document,
                    "rendered": render_markdown(str(document.get("markdown") or "")),
                }
            )
    context = shell_context(
        request=request,
        page_title=str(strategy.get("title") or strategy_key),
        page_summary="查看本次循环进度、Agent 文档与关联的 AI 助手记录。",
        active_endpoint="api.admin_operation_cycles_page",
    )
    context.update(
        {
            "breadcrumbs": [
                {"label": "客户管理后台", "href": admin_path_for("api.admin_console_dashboard")},
                {"label": "运营闭环", "href": admin_path_for("api.admin_operation_cycles_page")},
                {"label": str(strategy.get("title") or strategy_key), "href": ""},
            ],
            "strategy": {**strategy, "detail_href": _detail_href(strategy_key)},
            "weekly_progress": _weekly_progress(strategy, now=_utc_now()),
            "detail_nav": detail_nav,
            "active_section": section,
            "active_label": allowed_sections[section],
            "active_document": active_document,
            "rendered_document": render_markdown(str(active_document.get("markdown") or "")),
            "assistant_plans": [item for item in payload.get("assistant_plans") or [] if isinstance(item, dict)],
            "operation_context_enabled": operation_context_v1_enabled(),
            "formal_context": formal_context,
            "formal_documents": formal_documents,
            "optimization_context": optimization_context,
        }
    )
    return templates.TemplateResponse(request, "admin_shell/operation_cycles_strategy.html", context)


def _not_found(request: Request, message: str):
    context = shell_context(
        request=request,
        page_title="运营闭环",
        page_summary=message,
        active_endpoint="api.admin_operation_cycles_page",
    )
    context.update({"strategy_summaries": [], "page_error": message})
    return templates.TemplateResponse(
        request,
        "admin_shell/operation_cycles_list.html",
        context,
        status_code=404,
    )
