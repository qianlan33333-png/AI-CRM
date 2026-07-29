#!/usr/bin/env python3
"""Build a privacy-safe operation-cycle snapshot for the 2026-07-13 HXC run.

The source artifacts may contain recipient-level material.  This script reads
only an allowlisted set of aggregate fields and never copies source paths,
recipient rows, message content, or identity fields into the output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "operation_cycle_snapshot.v1"
TENANT_ID = "aicrm"
TIMEZONE = ZoneInfo("Asia/Shanghai")
RUN_DATE = date(2026, 7, 13)
PRODUCTION_PLAN_KEY = "hxc-monday-abcd-20260713-1600-final-848-weekly-cover-v1"
MAX_SOURCE_BYTES = 64 * 1024 * 1024

ARTIFACT_TYPES = (
    "send_summary",
    "campaign_input",
    "create_response",
    "delivery_metrics",
)
OBSERVATION_WINDOWS = ("T+2h", "T+24h", "T+48h", "T+72h")
OBSERVATION_WINDOW_SUFFIXES = {"T+2h": "t2h", "T+24h": "t24h", "T+48h": "t48h", "T+72h": "t72h"}
OBSERVATION_METRICS = {
    "active_message_count": "主动消息人数",
    "target_behavior_count": "目标行为人数",
}

FORBIDDEN_KEY_PARTS = {
    "phone",
    "mobile",
    "unionid",
    "external_userid",
    "openid",
    "nickname",
    "display_name",
    "recipient",
    "raw_message",
    "raw_msg",
    "content_text",
    "credential",
    "access_token",
    "api_key",
    "api_secret",
    "client_secret",
    "private_key",
    "signing_key",
    "secret",
    "password",
}
PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
API_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9])(?:sk|rk|pk)-(?:proj-)?[a-z0-9_-]{12,}(?![a-z0-9])"
    r"|(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])"
    r"|(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}(?![A-Za-z0-9])"
)
WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")


def _canonical_output_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


CANONICAL_FORBIDDEN_KEY_PARTS = tuple(_canonical_output_key(value) for value in FORBIDDEN_KEY_PARTS)


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_artifact(path: Path | None, artifact_type: str) -> dict[str, Any] | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve(strict=True)
    raw = resolved.read_bytes()
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError(f"{artifact_type} exceeds the {MAX_SOURCE_BYTES}-byte source limit")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{artifact_type} must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{artifact_type} must contain a JSON object")
    return {
        "artifact_type": artifact_type,
        "payload": payload,
        "evidence_hash": hashlib.sha256(raw).hexdigest(),
    }


def _nonnegative_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return int(value)


def _first_int(*candidates: tuple[Any, str]) -> tuple[int | None, str | None]:
    for value, source in candidates:
        parsed = _nonnegative_int(value, field=source)
        if parsed is not None:
            return parsed, source
    return None, None


def _local_iso(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("timestamp fields must use ISO-8601 or YYYY-MM-DD HH:MM") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TIMEZONE)
    return parsed.isoformat(timespec="seconds")


def _artifact_reference(artifact_type: str, artifact: Mapping[str, Any] | None) -> dict[str, Any]:
    labels = {
        "send_summary": "人群决策聚合",
        "campaign_input": "计划目标聚合",
        "create_response": "计划创建结果聚合",
        "delivery_metrics": "发送与行为聚合",
    }
    is_available = artifact is not None
    return {
        "reference_key": f"hxc-20260713-{artifact_type.replace('_', '-')}",
        "reference_type": "artifact",
        "label": labels[artifact_type],
        "source_system": ("ai_crm_production_readonly" if artifact_type == "delivery_metrics" else "codex_local_artifact") if is_available else "source_missing",
        "source_id": PRODUCTION_PLAN_KEY if artifact_type == "delivery_metrics" and is_available else f"hxc_monday_20260713:{artifact_type}",
        "href": "",
        "evidence_hash": artifact["evidence_hash"] if is_available else "",
        "data_status": "observed" if is_available else "unknown",
    }


def _funnel_value(
    *,
    value: int | None,
    source_available: bool,
    available_source: str,
    missing_source: str,
    missing_limitation: str,
    observed_limitation: str = "",
    classification: str = "",
) -> dict[str, Any]:
    is_observed = source_available and value is not None
    return {
        "status": "observed" if is_observed else "unknown",
        "value": value if is_observed else None,
        "data_source": available_source if source_available else missing_source,
        "limitation": observed_limitation if is_observed else missing_limitation,
        "classification": classification if is_observed else "",
    }


def _extract_delivery(delivery_artifact: Mapping[str, Any] | None) -> dict[str, Any]:
    if delivery_artifact is None:
        return {
            "sent": None,
            "failed": None,
            "first_sent_at": None,
            "last_sent_at": None,
            "plan_review_status": "",
            "plan_run_status": "",
            "source_available": False,
            "observations": {},
        }

    payload = delivery_artifact["payload"]
    delivery = payload.get("delivery") if isinstance(payload.get("delivery"), dict) else payload
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    status_counts = delivery.get("status_counts") if isinstance(delivery.get("status_counts"), dict) else delivery
    sent, _ = _first_int(
        (status_counts.get("sent"), "delivery.status_counts.sent"),
        (status_counts.get("effective_sent"), "delivery.effective_sent"),
        (status_counts.get("sent_count"), "delivery.sent_count"),
    )
    failed, _ = _first_int(
        (status_counts.get("failed_retryable"), "delivery.status_counts.failed_retryable"),
        (status_counts.get("failed"), "delivery.status_counts.failed"),
        (status_counts.get("failed_count"), "delivery.failed_count"),
    )
    observations: dict[str, dict[str, int]] = {}
    raw_windows = payload.get("observation_windows")
    if isinstance(raw_windows, dict):
        for window in OBSERVATION_WINDOWS:
            raw_window = raw_windows.get(window)
            if not isinstance(raw_window, dict):
                continue
            values: dict[str, int] = {}
            for metric_key in OBSERVATION_METRICS:
                parsed = _nonnegative_int(raw_window.get(metric_key), field=f"observation_windows.{window}.{metric_key}")
                if parsed is not None:
                    values[metric_key] = parsed
            if values:
                observations[window] = values
    return {
        "sent": sent,
        "failed": failed,
        "first_sent_at": _local_iso(delivery.get("first_sent_at")),
        "last_sent_at": _local_iso(delivery.get("last_sent_at")),
        "plan_review_status": str(plan.get("review_status") or "").strip().lower(),
        "plan_run_status": str(plan.get("run_status") or plan.get("status") or "").strip().lower(),
        "source_available": sent is not None and failed is not None,
        "observations": observations,
    }


def _assert_funnel_order(funnel: Mapping[str, Mapping[str, Any]]) -> None:
    ordered_keys = (
        "candidate_count",
        "audited_count",
        "recommended_send_count",
        "planned_target_count",
        "effective_sent_count",
    )
    values = [funnel[key]["value"] for key in ordered_keys]
    candidate, audited, recommended, planned, sent = values
    inconsistent = (
        (candidate is not None and audited is not None and candidate < audited)
        or (audited is not None and recommended is not None and audited < recommended)
        or (recommended is not None and planned is not None and recommended != planned)
        or (planned is not None and sent is not None and planned < sent)
    )
    if inconsistent:
        raise ValueError("aggregate funnel is inconsistent: expected candidate >= audited >= recommended == planned >= sent")
    failed = funnel["failed_count"]["value"]
    if planned is not None and sent is not None and failed is not None and sent + failed != planned:
        raise ValueError("aggregate funnel is inconsistent: sent + failed must equal planned target")


def assert_snapshot_safe(payload: Any) -> None:
    """Reject identity fields, secrets, phone literals, and local paths recursively."""

    def walk(value: Any, location: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = _canonical_output_key(key)
                if any(part in normalized for part in CANONICAL_FORBIDDEN_KEY_PARTS):
                    raise ValueError(f"unsafe output key at {location}.{key}")
                walk(child, f"{location}.{key}")
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{location}[{index}]")
            return
        if isinstance(value, str):
            if PHONE_PATTERN.search(value):
                raise ValueError(f"phone-like literal found at {location}")
            if API_CREDENTIAL_PATTERN.search(value):
                raise ValueError(f"credential-like literal found at {location}")
            safe_admin_href = location.endswith(".href") and value.startswith(("/admin/", "/api/admin/"))
            if (value.startswith(("/", "~/", "file://")) and not safe_admin_href) or WINDOWS_ABSOLUTE_PATH_PATTERN.match(value):
                raise ValueError(f"absolute local path found at {location}")

    walk(payload, "snapshot")


def build_snapshot(
    *,
    send_summary_path: Path | None = None,
    campaign_input_path: Path | None = None,
    create_response_path: Path | None = None,
    delivery_metrics_path: Path | None = None,
    snapshot_revision: int = 1,
    reported_at: str | None = None,
) -> dict[str, Any]:
    if snapshot_revision < 1:
        raise ValueError("snapshot_revision must be at least 1")

    artifacts = {
        "send_summary": _load_artifact(send_summary_path, "send_summary"),
        "campaign_input": _load_artifact(campaign_input_path, "campaign_input"),
        "create_response": _load_artifact(create_response_path, "create_response"),
        "delivery_metrics": _load_artifact(delivery_metrics_path, "delivery_metrics"),
    }
    summary = artifacts["send_summary"]["payload"] if artifacts["send_summary"] else {}
    campaign = artifacts["campaign_input"]["payload"] if artifacts["campaign_input"] else {}
    create_response = artifacts["create_response"]["payload"] if artifacts["create_response"] else {}
    delivery = _extract_delivery(artifacts["delivery_metrics"])

    candidate, _ = _first_int((campaign.get("candidate_count"), "campaign_input.candidate_count"))
    audited, _ = _first_int((summary.get("recipient_count"), "send_summary.recipient_count"))
    decision_counts = summary.get("decision_counts") if isinstance(summary.get("decision_counts"), dict) else {}
    recommended, _ = _first_int(
        (summary.get("sendable_count"), "send_summary.sendable_count"),
        (decision_counts.get("建议发送"), "send_summary.decision_counts.send"),
    )
    planned, _ = _first_int(
        (create_response.get("recipient_count"), "create_response.recipient_count"),
        (campaign.get("recipient_count"), "campaign_input.recipient_count"),
    )
    values = {
        "candidate_count": candidate,
        "audited_count": audited,
        "recommended_send_count": recommended,
        "planned_target_count": planned,
        "effective_sent_count": delivery["sent"],
        "failed_count": delivery["failed"],
    }
    funnel = {
        "candidate_count": _funnel_value(
            value=values["candidate_count"],
            source_available=candidate is not None,
            available_source="campaign_input.aggregate",
            missing_source="source_missing",
            missing_limitation="原始候选聚合文件未随快照提供；该漏斗值保持未知。",
        ),
        "audited_count": _funnel_value(
            value=values["audited_count"],
            source_available=audited is not None,
            available_source="send_summary.aggregate",
            missing_source="source_missing",
            missing_limitation="人群审计聚合文件未随快照提供；该漏斗值保持未知。",
        ),
        "recommended_send_count": _funnel_value(
            value=values["recommended_send_count"],
            source_available=recommended is not None,
            available_source="send_summary.aggregate",
            missing_source="source_missing",
            missing_limitation="发送建议聚合文件未随快照提供；该漏斗值保持未知。",
        ),
        "planned_target_count": _funnel_value(
            value=values["planned_target_count"],
            source_available=planned is not None,
            available_source="create_response.aggregate" if artifacts["create_response"] else "campaign_input.aggregate",
            missing_source="source_missing",
            missing_limitation="计划目标聚合文件未随快照提供；该漏斗值保持未知。",
        ),
        "effective_sent_count": _funnel_value(
            value=values["effective_sent_count"],
            source_available=delivery["source_available"],
            available_source="broadcast_jobs.production_readonly_aggregate",
            missing_source="source_missing",
            missing_limitation="晚间发送事实文件未提供；有效发送人数保持未知。",
        ),
        "failed_count": _funnel_value(
            value=values["failed_count"],
            source_available=delivery["source_available"],
            available_source="broadcast_jobs.production_readonly_aggregate",
            missing_source="source_missing",
            missing_limitation="晚间发送事实文件未提供；失败人数保持未知。",
            observed_limitation="该失败聚合的生产状态为 failed_retryable。",
            classification="failed_retryable",
        ),
    }
    _assert_funnel_order(funnel)

    intended_send_at = _local_iso(campaign.get("scheduled_for")) or f"{RUN_DATE.isoformat()}T16:00:00+08:00"
    plan_scheduled_for = _local_iso(create_response.get("scheduled_for")) or intended_send_at
    decision_completed_at = _local_iso(summary.get("generated_at"))
    snapshot_reported_at = _local_iso(reported_at) if reported_at else datetime.combine(RUN_DATE + timedelta(days=1), time.min, TIMEZONE).isoformat()

    delivery_quality = "production_readonly_aggregate" if delivery["source_available"] else "source_missing"
    delivery_source = "broadcast_jobs.production_readonly_aggregate" if delivery["source_available"] else "source_missing"
    delivery_rate_available = (
        delivery["source_available"]
        and values["effective_sent_count"] is not None
        and values["planned_target_count"] not in {None, 0}
    )
    metrics: list[dict[str, Any]] = [
        {
            "metric_key": "effective_delivery_rate",
            "label": "有效发送率",
            "numerator": values["effective_sent_count"] if delivery_rate_available else None,
            "denominator": values["planned_target_count"] if delivery_rate_available else None,
            "value": round(values["effective_sent_count"] / values["planned_target_count"], 6) if delivery_rate_available else None,
            "unit": "ratio",
            "observation_window": "delivery_final",
            "data_source": delivery_source,
            "data_quality": delivery_quality,
            "limitations": ["这是发送完成度，不代表激活提升。"]
            + ([] if delivery_rate_available else ["当前缺少可复核的发送聚合，数值保持未知。"]),
            "is_causal": False,
            "value_status": "observed" if delivery_rate_available else "unknown",
        }
    ]
    for window in OBSERVATION_WINDOWS:
        window_values = delivery["observations"].get(window, {})
        if window_values:
            for metric_key, value in window_values.items():
                metrics.append(
                    {
                        "metric_key": f"{metric_key}_{OBSERVATION_WINDOW_SUFFIXES[window]}",
                        "label": OBSERVATION_METRICS[metric_key],
                        "numerator": value,
                        "denominator": values["effective_sent_count"],
                        "value": value,
                        "unit": "people",
                        "observation_window": window,
                        "data_source": "delivery_metrics.aggregate",
                        "data_quality": "aggregated_snapshot",
                        "limitations": ["观察信号仅用于运营复盘，不构成因果提升证据。"],
                        "is_causal": False,
                        "value_status": "observed",
                    }
                )
        else:
            metrics.append(
                {
                    "metric_key": f"target_behavior_count_{OBSERVATION_WINDOW_SUFFIXES[window]}",
                    "label": "目标行为人数",
                    "numerator": None,
                    "denominator": None,
                    "value": None,
                    "unit": "people",
                    "observation_window": window,
                    "data_source": "source_missing",
                    "data_quality": "source_missing",
                    "limitations": ["当前没有可复核的分窗口行为聚合，不能推断激活效果。"],
                    "is_causal": False,
                    "value_status": "unknown",
                }
            )

    create_run_status = str(delivery["plan_run_status"] or create_response.get("run_status") or "unknown").strip().lower()
    plan_status = create_run_status if create_run_status != "unknown" else ""
    source_review_status = str(delivery["plan_review_status"] or create_response.get("review_status") or "").strip().lower()
    review_status = {
        "pending_review": "pending",
        "pending": "pending",
        "approved": "approved",
        "rejected": "rejected",
        "cancelled": "cancelled",
    }.get(source_review_status, "not_created")
    data_conflicts: list[str] = []
    if (values["effective_sent_count"] or 0) > 0 and plan_status == "draft":
        data_conflicts.append("生产计划 review_status 已 approved，但 run_status/status 仍为 draft；发送事实显示已有有效发送，展示以发送事实为准并保留冲突。")
    available_artifact_count = sum(artifact is not None for artifact in artifacts.values())
    artifact_status = (
        "source_missing"
        if available_artifact_count == 0
        else "complete"
        if available_artifact_count == len(artifacts) and len(delivery["observations"]) == len(OBSERVATION_WINDOWS)
        else "partial"
    )
    delivery_status = "completed" if values["effective_sent_count"] == values["planned_target_count"] and values["failed_count"] == 0 else "partial"

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "report_id": "hxc_monday_20260713_backfill_v1",
        "snapshot_revision": snapshot_revision,
        "tenant_id": TENANT_ID,
        "reported_at": snapshot_reported_at,
        "external_effects": "none",
        "strategy": {
            "strategy_key": "hxc_monday_full_activation",
            "title": "每周一全量用户激活",
            "description": "按聚合证据完成分层判断、人审、发送观察与复盘的周期运营策略。",
            "cadence": "weekly_monday",
            "timezone": "Asia/Shanghai",
            "status": "active",
            "version": 2,
            "version_label": "ABCD 分层 v2（2026-07-13）",
            "objective": "让每位可触达用户获得与当前状态匹配的下一步服务，并保留可复核结果。",
            "definition": {
                "audience": "全量可运营用户（按规则排除后）",
                "decision_model": "ABCD 分层与逐人去留判断",
                "reporting_mode": "aggregate_only",
                "template_key": "hxc_monday_abcd_obsidian_v2_20260713",
            },
            "version_effective_from": f"{RUN_DATE.isoformat()}T00:00:00+08:00",
        },
        "run": {
            "run_key": "hxc_monday_full_activation_20260713",
            "label": "2026-07-13 周一全量激活",
            "objective": "完成本周全量用户分层、审核、发送与观察闭环。",
            "plan_version": "",
            "plan_status": plan_status,
            "plan_source": (
                "cloud_broadcast_plans.production_readonly_aggregate"
                if delivery["plan_run_status"] or delivery["plan_review_status"]
                else "create_response.aggregate"
                if artifacts["create_response"]
                else "source_missing"
            ),
            "started_at": f"{RUN_DATE.isoformat()}T09:00:00+08:00",
            "completed_at": None,
            "intended_send_at": intended_send_at,
            "plan_scheduled_for": plan_scheduled_for,
            "first_sent_at": delivery["first_sent_at"],
            "last_sent_at": delivery["last_sent_at"],
        },
        "execution_stage": "postmortem",
        "review_status": review_status,
        "delivery_status": delivery_status,
        "data_status": "attribution_gap",
        "optimization_status": "pending_confirmation",
        "artifact_status": artifact_status,
        "attempts": [
            {
                "attempt_key": "hxc-20260713-preflight-01",
                "parent_attempt_key": None,
                "status": "blocked",
                "started_at": f"{RUN_DATE.isoformat()}T09:00:00+08:00",
                "ended_at": f"{RUN_DATE.isoformat()}T09:00:00+08:00",
                "blocked_reason": "prerequisites_missing",
                "summary": {"note": "09:00 前置检查被阻断；原始阻断证据未随安全快照提供。"},
            },
            {
                "attempt_key": "hxc-20260713-recovery-02",
                "parent_attempt_key": "hxc-20260713-preflight-01",
                "status": "completed",
                "started_at": decision_completed_at,
                "ended_at": None,
                "blocked_reason": "",
                "summary": {
                    "note": "恢复后完成分层、计划与发送；发送事实来自生产只读聚合。"
                    if delivery["source_available"]
                    else "恢复后完成分层和计划；发送事实当前未知。"
                },
            },
        ],
        "stages": [
            {
                "stage_key": "hxc-20260713-preflight-01",
                "attempt_key": "hxc-20260713-preflight-01",
                "stage": "preflight",
                "status": "blocked",
                "started_at": f"{RUN_DATE.isoformat()}T09:00:00+08:00",
                "ended_at": f"{RUN_DATE.isoformat()}T09:00:00+08:00",
                "blocked_reason": "prerequisites_missing",
                "summary": {"note": "前置条件未满足；具体原始证据标记为 source_missing。"},
            },
            {
                "stage_key": "hxc-20260713-decisioning-02",
                "attempt_key": "hxc-20260713-recovery-02",
                "stage": "decisioning",
                "status": "blocked" if review_status == "pending" and delivery["source_available"] else "completed",
                "started_at": None,
                "ended_at": decision_completed_at,
                "blocked_reason": "",
                "summary": {"note": "完成 895 人审计并形成 848 人建议发送版本。"},
            },
            {
                "stage_key": "hxc-20260713-review-02",
                "attempt_key": "hxc-20260713-recovery-02",
                "stage": "review",
                "status": "completed",
                "started_at": None,
                "ended_at": None,
                "blocked_reason": "state_conflict" if review_status == "pending" and delivery["source_available"] else "",
                "summary": {
                    "note": "创建快照仍为 pending_review / draft，但生产发送事实已发生；人审完成状态无法单独核验。"
                    if review_status == "pending" and delivery["source_available"]
                    else "生产计划聚合确认 review_status=approved；独立审批事件时间未随聚合提供。"
                    if review_status == "approved" and delivery["plan_review_status"] == "approved"
                    else "计划版本进入执行；人审时间证据未随快照提供。"
                },
            },
            {
                "stage_key": "hxc-20260713-delivery-02",
                "attempt_key": "hxc-20260713-recovery-02",
                "stage": "delivery",
                "status": "completed" if delivery["source_available"] else "blocked",
                "started_at": delivery["first_sent_at"],
                "ended_at": delivery["last_sent_at"],
                "blocked_reason": "source_missing" if not delivery["source_available"] else "",
                "summary": {
                    "note": f"生产只读聚合为 {values['effective_sent_count']} 个有效发送、{values['failed_count']} 个可重试失败。"
                    if delivery["source_available"]
                    else "发送聚合文件未提供；发送结果保持未知。"
                },
            },
            {
                "stage_key": "hxc-20260713-observing-02",
                "attempt_key": "hxc-20260713-recovery-02",
                "stage": "observing",
                "status": "blocked" if not delivery["observations"] else "completed",
                "started_at": None,
                "ended_at": None,
                "blocked_reason": "instrumentation_missing" if not delivery["observations"] else "",
                "summary": {
                    "note": "缺少可复核的 T+2h、24h、48h、72h 行为聚合。" if not delivery["observations"] else "分窗口观察数据已聚合。"
                },
            },
            {
                "stage_key": "hxc-20260713-postmortem-02",
                "attempt_key": "hxc-20260713-recovery-02",
                "stage": "postmortem",
                "status": "completed",
                "started_at": None,
                "ended_at": snapshot_reported_at,
                "blocked_reason": "",
                "summary": {"note": "完成发送事实复盘；行为效果保持未知且不作因果包装。"},
            },
        ],
        "funnel": funnel,
        "metrics": metrics,
        "retrospective": {
            "conclusion": (
                "本轮已验证从分层决策到发送事实的链路；缺少可复核行为归因，不能得出激活提升结论。"
                if delivery["source_available"]
                else "本轮已验证分层决策与计划聚合；发送和行为结果缺少可复核来源，不能得出激活提升结论。"
            ),
            "observations": ["候选、审计、建议发送、计划目标和发送结果使用不同分母，页面必须分别展示。"]
            + (
                [f"{values['effective_sent_count']} 个有效发送与 {values['failed_count']} 个可重试失败来自生产只读聚合，不等于用户被激活。"]
                if delivery["source_available"]
                else []
            ),
            "limitations": (
                [] if delivery["source_available"] else ["晚间发送聚合文件未提供，发送人数保持 unknown。"]
            )
            + ["分窗口行为指标为 unknown；所有结果均为 observational，is_causal=false。"],
            "data_conflicts": data_conflicts,
            "generated_at": snapshot_reported_at,
        },
        "next_iteration": {
            "summary": "先补齐归因与计划状态一致性，再扩大效果判断。",
            "hypothesis": "若发送回执、卡片追踪与计划投影使用同一关联键，下一轮可形成可复核的分窗口漏斗。",
            "actions": [
                "补齐发送回执与 T+2h、24h、48h、72h 行为聚合。",
                "修复计划 draft 投影与实际发送事实不一致的问题。",
                "下一轮继续区分观察信号与因果提升。",
            ],
            "status": "pending_confirmation",
            "confirmation_note": "优化项尚未被负责人确认，也未应用到下一策略版本。",
            "applied_strategy_version": None,
        },
        "references": [_artifact_reference(artifact_type, artifacts[artifact_type]) for artifact_type in ARTIFACT_TYPES],
    }
    assert_snapshot_safe(snapshot)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a privacy-safe HXC Monday operation-cycle snapshot.")
    parser.add_argument("--send-summary", type=Path)
    parser.add_argument("--campaign-input", type=Path)
    parser.add_argument("--create-response", type=Path)
    parser.add_argument("--delivery-metrics", type=Path)
    parser.add_argument("--snapshot-revision", type=int, default=1)
    parser.add_argument("--reported-at", default="")
    parser.add_argument("--output", type=Path, help="Optional output JSON path. The path is never embedded in the snapshot or CLI receipt.")
    args = parser.parse_args()
    try:
        snapshot = build_snapshot(
            send_summary_path=args.send_summary,
            campaign_input_path=args.campaign_input,
            create_response_path=args.create_response,
            delivery_metrics_path=args.delivery_metrics,
            snapshot_revision=args.snapshot_revision,
            reported_at=args.reported_at or None,
        )
    except (OSError, ValueError):
        print(json.dumps({"ok": False, "error": "snapshot_build_failed"}), file=sys.stderr)
        return 2

    encoded = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        receipt = {
            "ok": True,
            "report_id": snapshot["report_id"],
            "snapshot_revision": snapshot["snapshot_revision"],
            "snapshot_hash": hashlib.sha256(_canonical_bytes(snapshot)).hexdigest(),
        }
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
