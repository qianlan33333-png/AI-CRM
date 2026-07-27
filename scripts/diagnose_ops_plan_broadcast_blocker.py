#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from script_runtime import ensure_repo_root_on_path, print_json


ROOT = ensure_repo_root_on_path()
DEFAULT_PLAN_ID = "external_daily_lesson_20260617_1230_huangyoucan_v1_b11"
OPS_PLAN_APPROVED_EVENT_TYPE = "ops_plan.approved"
PLANNER_CONSUMER = "broadcast_task_planner_consumer"
EXPECTED_CONSUMERS = (
    "audit_projection_consumer",
    "automation_schedule_refresh_consumer",
    PLANNER_CONSUMER,
    "ops_plan_ai_assist_notify_consumer",
)

RUN_DUE_PREVIEW_ROUTE = "/api/admin/internal-events/run-due/preview"
RUN_DUE_EXECUTE_ROUTE = "/api/admin/internal-events/run-due"
SINGLE_CONSUMER_RUN_ROUTE = "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/run"
SINGLE_CONSUMER_RETRY_ROUTE = "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/retry"
SINGLE_CONSUMER_SKIP_ROUTE = "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/skip"

CLASSIFICATIONS = {
    "legacy_event_non_applicable",
    "run_due_ready_for_operator_preview",
    "run_due_blocked_by_token",
    "run_due_blocked_by_auto_execute_config",
    "run_due_blocked_by_allowlist",
    "run_due_not_eligible",
    "consumer_already_succeeded",
    "consumer_failed_retryable",
    "consumer_failed_terminal",
    "consumer_non_applicable",
    "runtime_repair_required",
    "unknown_requires_manual_review",
    "planner_created_broadcast_job",
    "planner_reused_broadcast_job",
    "planner_succeeded_downstream_pending",
    "planner_skipped_non_applicable",
    "planner_skipped_missing_required_input",
    "planner_failed_retryable",
    "planner_failed_terminal",
    "planner_runtime_repair_required",
}

NEXT_NATIVE_TARGET_STATUSES = {
    "legacy_event_non_applicable",
    "next_native_plan_ready_for_evidence",
    "next_native_plan_missing_recipients",
    "next_native_plan_missing_messages",
    "planner_created_broadcast_job",
    "planner_reused_broadcast_job",
    "planner_succeeded_downstream_pending",
    "BLOCKED_NEXT_NATIVE_TARGET_MISSING",
}

SENSITIVE_KEY_PARTS = (
    "authorization",
    "token",
    "secret",
    "corpsecret",
    "access_token",
    "external_userid",
    "external_user_id",
    "phone",
    "mobile",
    "openid",
    "unionid",
    "target_list",
    "member",
    "customer_identifier",
)

SAFE_SENSITIVE_METADATA_KEYS = {
    "required_token_or_gate",
    "token_configured",
    "token_gate_status",
}


def run(
    *,
    plan_id: str = DEFAULT_PLAN_ID,
    input_json: Path | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    if input_json:
        evidence = json.loads(input_json.read_text(encoding="utf-8"))
        evidence.setdefault("source", {"type": "input_json", "path": str(input_json)})
    else:
        evidence = _load_readonly_db_evidence(plan_id=plan_id, database_url=database_url)
    return classify_evidence(evidence)


def classify_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    evidence = _redact_payload(evidence)
    plan_id = str(evidence.get("plan_id") or DEFAULT_PLAN_ID)
    event = dict(evidence.get("approval_event") or evidence.get("internal_event") or {})
    event_type = str(event.get("event_type") or "")
    runs = _consumer_run_records(evidence)
    planner_run = _planner_run(runs)
    config = _run_due_config(evidence=evidence, planner_run=planner_run)
    classification = _classify(event=event, event_type=event_type, planner_run=planner_run, config=config)
    planner_result = _planner_result(planner_run)
    downstream = dict(evidence.get("downstream") or {})
    plan_context = _plan_context(evidence=evidence, event=event)
    target_selection = _next_native_target_selection(evidence=evidence, event=event, plan_context=plan_context)
    broadcast_job_id = _planner_field(planner_run, "broadcast_job_id")
    external_effect_job_id = _planner_field(planner_run, "external_effect_job_id")
    push_center_job_id = _planner_field(planner_run, "push_center_job_id")
    idempotency_key = _planner_field(planner_run, "idempotency_key")
    duplicate_handling = _planner_field(planner_run, "duplicate_handling")
    downstream_status = _planner_field(planner_run, "downstream_status")

    assert classification in CLASSIFICATIONS
    output = {
        "ok": True,
        "readonly": True,
        "real_external_call_executed": False,
        "production_write_executed": False,
        "deploy_or_env_modified": False,
        "plan_id": plan_id,
        "scenario": "ops_plan_to_broadcast",
        "source": evidence.get("source") or {"type": "fixture_or_diagnostic"},
        "classification": classification,
        "planner_consumer_pending_classification": classification,
        "approval_event_id": _redact_id(str(event.get("approval_event_id") or event.get("event_id") or "")) or "not_found",
        "internal_event_id": _redact_id(str(event.get("internal_event_id") or event.get("event_id") or "")) or "not_found",
        "event_type": event_type or "not_found",
        "event_plan_type": _event_plan_type(event) or "not_collected",
        "event_source": _event_source(event) or "not_collected",
        "expected_consumer_names": list(EXPECTED_CONSUMERS),
        "actual_consumer_run_records": runs,
        "broadcast_task_planner_consumer": planner_run or {"consumer_name": PLANNER_CONSUMER, "status": "not_found"},
        "planner_result": planner_result,
        "broadcast_job_id": broadcast_job_id or "not_found",
        "external_effect_job_id": external_effect_job_id or "not_found",
        "push_center_job_id": push_center_job_id or "not_found",
        "idempotency_key": idempotency_key or "not_found",
        "duplicate_handling": duplicate_handling or "not_found",
        "downstream_status": downstream_status or "not_found",
        "downstream": downstream,
        "plan_context": plan_context,
        "legacy_event_reclassification": {
            "classification": "legacy_event_non_applicable" if classification == "legacy_event_non_applicable" else "not_applicable",
            "is_legacy_event": _is_legacy_event(event),
            "can_judge_next_native_planner": classification != "legacy_event_non_applicable",
            "reason": "legacy_campaign lacks Next-native recipient/message projection rows" if classification == "legacy_event_non_applicable" else "",
        },
        "next_native_evidence_target": target_selection["target"],
        "next_native_evidence_target_status": target_selection["status"],
        "next_native_target_blocking_reason": target_selection["blocking_reason"],
        "can_recollect_ops_plan_e2e_now": target_selection["can_recollect"],
        "required_operator_action": target_selection["required_operator_action"],
        "status": str((planner_run or {}).get("status") or "not_found"),
        "attempt_count": int((planner_run or {}).get("attempt_count") or 0),
        "last_error": _last_error(planner_run),
        "next_run_at": str((planner_run or {}).get("next_run_at") or (planner_run or {}).get("next_retry_at") or ""),
        "run_due_eligible": _run_due_eligible(planner_run),
        "preview_route_available": True,
        "preview_route": RUN_DUE_PREVIEW_ROUTE,
        "run_route_available": True,
        "run_route": RUN_DUE_EXECUTE_ROUTE,
        "retry_route_available": classification in {"consumer_failed_retryable", "planner_failed_retryable"},
        "retry_route": SINGLE_CONSUMER_RETRY_ROUTE,
        "skip_route_available": classification
        in {"run_due_ready_for_operator_preview", "run_due_blocked_by_auto_execute_config", "run_due_blocked_by_allowlist", "run_due_blocked_by_token"},
        "skip_route": SINGLE_CONSUMER_SKIP_ROUTE,
        "single_consumer_run_route_available": True,
        "single_consumer_run_route": SINGLE_CONSUMER_RUN_ROUTE,
        "required_token_or_gate": "OAuth automation-worker access token for run-due preview/run; OAuth context or admin action confirmation for single-consumer actions",
        "token_configured": config["token_configured"],
        "token_gate_status": config["token_gate_status"],
        "auto_execute_enabled": config["auto_execute_enabled"],
        "allowlist_required": config["allowlist_required"],
        "allowlist_status": config["allowlist_status"],
        "allowlist_missing": config["allowlist_missing"],
        "operator_action_required": classification
        not in {
            "consumer_already_succeeded",
            "consumer_non_applicable",
            "planner_created_broadcast_job",
            "planner_reused_broadcast_job",
        },
        "can_execute_in_operator_window": classification in {"run_due_ready_for_operator_preview", "consumer_failed_retryable", "planner_failed_retryable"},
        "real_external_call_risk": "none_from_internal_event_worker; planner may create broadcast_job and later external_effect_job only after execute",
        "production_write_risk": _production_write_risk(classification),
        "recommended_execution_mode": _recommended_execution_mode(classification),
        "downstream_job_expected": classification
        in {
            "run_due_ready_for_operator_preview",
            "consumer_failed_retryable",
            "planner_created_broadcast_job",
            "planner_reused_broadcast_job",
            "planner_succeeded_downstream_pending",
        },
        "push_center_visibility_expected": classification
        in {
            "run_due_ready_for_operator_preview",
            "consumer_failed_retryable",
            "consumer_already_succeeded",
            "planner_created_broadcast_job",
            "planner_reused_broadcast_job",
            "planner_succeeded_downstream_pending",
        },
        "blocking_reason": _blocking_reason(classification),
        "sensitive_data_redaction_ok": True,
        "can_claim_ops_plan_broadcast_90_plus": False,
        "business_explanation": _business_explanation(classification),
    }
    return _redact_payload(output)


def _classify(
    *,
    event: dict[str, Any],
    event_type: str,
    planner_run: dict[str, Any] | None,
    config: dict[str, Any],
) -> str:
    if event_type in {"", "not_collected", "not_found"} and not planner_run:
        return "unknown_requires_manual_review"
    if event_type and event_type != OPS_PLAN_APPROVED_EVENT_TYPE:
        return "consumer_non_applicable"
    if _is_legacy_event(event):
        return "legacy_event_non_applicable"
    if not planner_run:
        return "runtime_repair_required"

    status = str(planner_run.get("status") or "").lower()
    if status == "succeeded":
        planner_result = _planner_result(planner_run)
        if planner_result in {"planner_created_broadcast_job", "planner_reused_broadcast_job"}:
            return planner_result
        if _planner_field(planner_run, "broadcast_job_id"):
            return "planner_succeeded_downstream_pending"
        return "planner_runtime_repair_required"
    if status == "skipped":
        planner_result = _planner_result(planner_run)
        if planner_result == "planner_skipped_non_applicable":
            return "planner_skipped_non_applicable"
        if planner_result == "planner_skipped_missing_required_input":
            return "planner_skipped_missing_required_input"
        reason = str((planner_run.get("result_summary_json") or {}).get("reason") or "").lower()
        if reason in {"consumer_non_applicable", "unsupported_plan_type"}:
            return "planner_skipped_non_applicable"
        if reason.startswith("missing_"):
            return "planner_skipped_missing_required_input"
        return "consumer_already_succeeded"
    if status in {"failed_retryable", "failed", "error"}:
        return "planner_failed_retryable"
    if status in {"failed_terminal", "blocked"}:
        return "planner_failed_terminal"
    if status != "pending":
        return "unknown_requires_manual_review"
    if config["token_configured"] is False:
        return "run_due_blocked_by_token"
    if config["auto_execute_enabled"] is False:
        return "run_due_blocked_by_auto_execute_config"
    if config["allowlist_missing"] is True:
        return "run_due_blocked_by_allowlist"
    if not _run_due_eligible(planner_run):
        return "run_due_not_eligible"
    return "run_due_ready_for_operator_preview"


def _event_metadata(event: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key in ("payload_summary_json", "payload_summary", "payload_json", "payload"):
        item = event.get(key)
        if isinstance(item, dict):
            merged.update(item)
    for key in ("plan_type", "source"):
        if key in event:
            merged[key] = event.get(key)
    return merged


def _event_plan_type(event: dict[str, Any]) -> str:
    metadata = _event_metadata(event)
    return str(metadata.get("plan_type") or metadata.get("source_type") or "").strip()


def _event_source(event: dict[str, Any]) -> str:
    metadata = _event_metadata(event)
    return str(metadata.get("source") or metadata.get("source_type") or "").strip()


def _is_legacy_event(event: dict[str, Any]) -> bool:
    plan_type = _event_plan_type(event).lower()
    source = _event_source(event).lower()
    return "legacy_campaign" in {plan_type, source}


def _plan_context(*, evidence: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    context = dict(evidence.get("plan_context") or {})
    if not context:
        context = dict(evidence.get("current_plan_context") or {})
    if not context:
        return {}
    if "plan_type" not in context:
        context["plan_type"] = _event_plan_type(event) or context.get("source_type") or "not_collected"
    return _redact_payload(context)


def _next_native_target_selection(
    *,
    evidence: dict[str, Any],
    event: dict[str, Any],
    plan_context: dict[str, Any],
) -> dict[str, Any]:
    target = dict(evidence.get("next_native_evidence_target") or evidence.get("next_native_target") or evidence.get("next_native_target_candidate") or {})
    if not target and plan_context and _is_next_native_plan(plan_context):
        target = dict(plan_context)
    if not target and _is_legacy_event(event):
        status = "BLOCKED_NEXT_NATIVE_TARGET_MISSING"
        return _target_selection_payload(
            target={},
            status=status,
            blocking_reason="legacy_event_non_applicable_next_native_target_required",
            required_operator_action="create_or_approve_next_native_test_plan",
        )
    if not target:
        status = "BLOCKED_NEXT_NATIVE_TARGET_MISSING"
        return _target_selection_payload(
            target={},
            status=status,
            blocking_reason="next_native_cloud_plan_target_not_found",
            required_operator_action="create_or_approve_next_native_test_plan",
        )

    plan_type = str(target.get("plan_type") or target.get("source_type") or "").strip() or "cloud_plan"
    target["plan_type"] = plan_type
    if not _is_next_native_plan(target):
        return _target_selection_payload(
            target=target,
            status="legacy_event_non_applicable",
            blocking_reason="target_is_not_next_native_cloud_plan",
            required_operator_action="create_or_approve_next_native_test_plan",
        )

    recipient_count = _int_value(target.get("recipient_projection_count") or target.get("recipient_count") or target.get("recipients_count") or 0)
    message_count = _int_value(target.get("message_projection_count") or target.get("message_count") or target.get("messages_count") or 0)
    target["recipient_projection_count"] = recipient_count
    target["message_projection_count"] = message_count
    target.setdefault("approval_event_exists", bool(target.get("approval_event_id") or target.get("internal_event_id")))
    target.setdefault("planner_consumer_executable", True)

    if recipient_count <= 0:
        return _target_selection_payload(
            target=target,
            status="next_native_plan_missing_recipients",
            blocking_reason="next_native_recipient_projection_missing",
            required_operator_action="create_or_approve_next_native_test_plan_with_recipients",
        )
    if message_count <= 0:
        return _target_selection_payload(
            target=target,
            status="next_native_plan_missing_messages",
            blocking_reason="next_native_message_projection_missing",
            required_operator_action="complete_next_native_send_content_projection",
        )
    return _target_selection_payload(
        target=target,
        status="next_native_plan_ready_for_evidence",
        blocking_reason="",
        required_operator_action="run_single_consumer_preview_for_selected_next_native_plan",
    )


def _target_selection_payload(
    *,
    target: dict[str, Any],
    status: str,
    blocking_reason: str,
    required_operator_action: str,
) -> dict[str, Any]:
    assert status in NEXT_NATIVE_TARGET_STATUSES
    return {
        "target": _redact_payload(target) if target else {"status": "not_found"},
        "status": status,
        "blocking_reason": blocking_reason,
        "required_operator_action": required_operator_action,
        "can_recollect": status == "next_native_plan_ready_for_evidence",
    }


def _is_next_native_plan(value: dict[str, Any]) -> bool:
    plan_type = str(value.get("plan_type") or value.get("source_type") or "cloud_plan").strip()
    return plan_type in {"cloud_plan", "ops_plan", "next_native", "next_native_cloud_plan"}


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _consumer_run_records(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    event = evidence.get("approval_event") or evidence.get("internal_event") or {}
    rows = evidence.get("consumer_runs") or event.get("consumer_runs") or []
    records: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        records.append(
            {
                "consumer_name": str(row.get("consumer_name") or row.get("consumer") or ""),
                "status": str(row.get("status") or ""),
                "attempt_count": int(row.get("attempt_count") or 0),
                "last_error_code": str(row.get("last_error_code") or row.get("error_code") or ""),
                "last_error_message": str(row.get("last_error_message") or row.get("error_message") or ""),
                "next_run_at": str(row.get("next_run_at") or row.get("next_retry_at") or ""),
                "result_summary_json": _redact_payload(row.get("result_summary_json") or {}),
            }
        )
    return records


def _planner_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in runs:
        if row.get("consumer_name") == PLANNER_CONSUMER:
            return row
    return None


def _run_due_config(*, evidence: dict[str, Any], planner_run: dict[str, Any] | None) -> dict[str, Any]:
    config = dict(evidence.get("ops_plan_broadcast_run_due_config") or evidence.get("internal_event_config") or {})
    if "token_configured" in config:
        token_configured: bool | str = bool(config.get("token_configured"))
    elif "internal_token_configured" in config:
        token_configured = bool(config.get("internal_token_configured"))
    else:
        token_configured = "not_collected"

    if "auto_execute_enabled" in config:
        auto_execute: bool | str = bool(config.get("auto_execute_enabled"))
    else:
        auto_execute = "not_collected"

    allowed_event_types = _csv_config(config.get("allowed_event_types"))
    allowed_consumers = _csv_config(config.get("allowed_consumers"))
    allowed_event_consumers = _csv_config(config.get("allowed_event_consumers"))
    requested_consumer = str((planner_run or {}).get("consumer_name") or PLANNER_CONSUMER)
    event_allowed = not allowed_event_types or OPS_PLAN_APPROVED_EVENT_TYPE in allowed_event_types
    consumer_allowed = not allowed_consumers or requested_consumer in allowed_consumers
    pair_allowed = not allowed_event_consumers or f"{OPS_PLAN_APPROVED_EVENT_TYPE}:{requested_consumer}" in allowed_event_consumers
    allowlist_required = bool(config.get("allowlist_required", True))
    allowlist_missing = (
        bool(config.get("allowlist_missing"))
        if "allowlist_missing" in config
        else bool(allowlist_required and not (event_allowed and consumer_allowed and pair_allowed))
    )
    if not allowlist_required:
        allowlist_status = "not_required"
    elif allowlist_missing:
        allowlist_status = "missing_or_incomplete"
    elif allowed_event_types or allowed_consumers or allowed_event_consumers:
        allowlist_status = "configured_for_requested_scope"
    else:
        allowlist_status = "not_collected"
        allowlist_missing = bool(config.get("allowlist_missing", False))

    if token_configured is False:
        token_gate_status = "missing_internal_token_config"
    elif token_configured is True:
        token_gate_status = "configured_redacted"
    else:
        token_gate_status = "not_collected"

    return {
        "token_configured": token_configured,
        "token_gate_status": token_gate_status,
        "auto_execute_enabled": auto_execute,
        "allowlist_required": allowlist_required,
        "allowlist_missing": allowlist_missing,
        "allowlist_status": allowlist_status,
    }


def _csv_config(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    return [item.strip() for item in str(value or "").replace("\n", ",").split(",") if item.strip()]


def _run_due_eligible(planner_run: dict[str, Any] | None) -> bool:
    if not planner_run:
        return False
    if str(planner_run.get("status") or "").lower() not in {"pending", "failed_retryable", "failed", "error"}:
        return False
    return not str(planner_run.get("next_run_at") or planner_run.get("next_retry_at") or "")


def _last_error(planner_run: dict[str, Any] | None) -> dict[str, str]:
    if not planner_run:
        return {"code": "", "message": ""}
    return {
        "code": str(planner_run.get("last_error_code") or planner_run.get("error_code") or ""),
        "message": str(planner_run.get("last_error_message") or planner_run.get("error_message") or ""),
    }


def _planner_result(planner_run: dict[str, Any] | None) -> str:
    if not planner_run:
        return ""
    summary = planner_run.get("result_summary_json") if isinstance(planner_run.get("result_summary_json"), dict) else {}
    return str(summary.get("planner_result") or "")


def _planner_field(planner_run: dict[str, Any] | None, key: str) -> Any:
    if not planner_run:
        return ""
    summary = planner_run.get("result_summary_json") if isinstance(planner_run.get("result_summary_json"), dict) else {}
    return summary.get(key) or ""


def _production_write_risk(classification: str) -> str:
    if classification in {"run_due_ready_for_operator_preview", "consumer_failed_retryable", "planner_failed_retryable"}:
        return "preview_none; execute_writes_consumer_attempts_and_may_create_broadcast_or_external_effect_job"
    if classification in {"planner_created_broadcast_job", "planner_reused_broadcast_job", "planner_succeeded_downstream_pending"}:
        return "readonly_diagnostic_none; prior consumer execution already wrote or reused broadcast_job"
    if classification == "legacy_event_non_applicable":
        return "none_for_readonly_diagnostic; do_not_execute_legacy_event_for_next_native_evidence"
    return "none_for_readonly_diagnostic"


def _recommended_execution_mode(classification: str) -> str:
    if classification == "run_due_ready_for_operator_preview":
        return "operator_preview_first_then_single_consumer_execute_after_approval"
    if classification in {"consumer_failed_retryable", "planner_failed_retryable"}:
        return "operator_retry_preview_then_single_consumer_retry_or_run_after_approval"
    if classification in {"run_due_blocked_by_token", "run_due_blocked_by_auto_execute_config", "run_due_blocked_by_allowlist"}:
        return "fix_gate_or_collect_operator_approval_before_any_execute"
    if classification == "consumer_already_succeeded":
        return "no_execute_recollect_ops_plan_broadcast_evidence"
    if classification in {"planner_created_broadcast_job", "planner_reused_broadcast_job", "planner_succeeded_downstream_pending"}:
        return "do_not_rerun_planner_recollect_downstream_broadcast_and_push_center_evidence"
    if classification == "legacy_event_non_applicable":
        return "do_not_rerun_legacy_event_select_next_native_cloud_plan_target"
    if classification in {"planner_skipped_missing_required_input", "planner_skipped_non_applicable"}:
        return "do_not_execute_repair_or_reclassify_plan_input_before_retry"
    return "manual_review_readonly_only"


def _blocking_reason(classification: str) -> str:
    return {
        "run_due_ready_for_operator_preview": "",
        "run_due_blocked_by_token": "missing_internal_token_or_admin_action_gate",
        "run_due_blocked_by_auto_execute_config": "auto_execute_disabled_for_generic_run_due_execute",
        "run_due_blocked_by_allowlist": "event_or_consumer_allowlist_missing",
        "run_due_not_eligible": "consumer_run_not_due_or_retry_window_not_reached",
        "consumer_already_succeeded": "",
        "legacy_event_non_applicable": "legacy_campaign_event_not_applicable_to_next_native_planner_evidence",
        "consumer_failed_retryable": "retryable_consumer_failure",
        "consumer_failed_terminal": "terminal_consumer_failure",
        "consumer_non_applicable": "event_type_not_ops_plan_approved",
        "runtime_repair_required": "planner_consumer_run_missing_or_handler_not_registered",
        "planner_created_broadcast_job": "",
        "planner_reused_broadcast_job": "",
        "planner_succeeded_downstream_pending": "broadcast_job_created_but_downstream_not_complete",
        "planner_skipped_non_applicable": "planner_consumer_non_applicable",
        "planner_skipped_missing_required_input": "planner_missing_required_input",
        "planner_failed_retryable": "retryable_planner_failure",
        "planner_failed_terminal": "terminal_planner_failure",
        "planner_runtime_repair_required": "planner_succeeded_without_auditable_job_output",
        "unknown_requires_manual_review": "classifier_could_not_determine_safe_next_action",
    }.get(classification, "unknown_requires_manual_review")


def _business_explanation(classification: str) -> str:
    if classification == "run_due_ready_for_operator_preview":
        return "Planner consumer is pending and due; operator can preview before any approved execute."
    if classification == "run_due_blocked_by_token":
        return "Planner consumer is pending, but internal/admin action token gate is not configured for operator preview/run."
    if classification == "run_due_blocked_by_auto_execute_config":
        return "Planner consumer is pending; generic run-due execute is disabled, so only approved single-consumer operator flow should be considered."
    if classification == "run_due_blocked_by_allowlist":
        return "Planner consumer is pending, but event/consumer allowlist does not include the requested ops_plan.approved planner scope."
    if classification == "consumer_already_succeeded":
        return "Planner consumer already finished; recollect downstream job and Push Center evidence."
    if classification == "legacy_event_non_applicable":
        return "The target approval event is a legacy_campaign event without Next-native recipient/message projections; select a current cloud_plan approval event for E2E evidence."
    if classification in {"consumer_failed_retryable", "planner_failed_retryable"}:
        return "Planner consumer has a retryable failure; operator should preview retry before execution."
    if classification in {"consumer_failed_terminal", "planner_failed_terminal"}:
        return "Planner consumer failed terminally and needs runtime repair or manual review."
    if classification == "planner_created_broadcast_job":
        return "Planner consumer created a broadcast_job; recollect downstream worker and Push Center evidence."
    if classification == "planner_reused_broadcast_job":
        return "Planner consumer reused the existing broadcast_job idempotently; recollect downstream worker and Push Center evidence."
    if classification == "planner_succeeded_downstream_pending":
        return "Planner consumer produced a broadcast job, but downstream external effect or Push Center completion still needs evidence."
    if classification == "planner_skipped_non_applicable":
        return "Planner consumer skipped because the event is not applicable to the Next ops plan broadcast planner."
    if classification == "planner_skipped_missing_required_input":
        return "Planner consumer skipped with an explicit missing-input reason; repair plan data before retry."
    if classification == "planner_runtime_repair_required":
        return "Planner consumer finished without auditable broadcast job output; runtime repair is required."
    if classification == "runtime_repair_required":
        return "Planner consumer run record was not found, indicating registration/runtime repair may be required."
    return "Readonly diagnostic could not safely classify the planner consumer blocker."


def _load_readonly_db_evidence(*, plan_id: str, database_url: str | None) -> dict[str, Any]:
    database_url = database_url or os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return {
            "plan_id": str(plan_id),
            "source": {"type": "database", "status": "unavailable", "reason": "DATABASE_URL not configured"},
            "approval_event": {"exists": False, "event_type": "not_collected"},
            "consumer_runs": [],
            "ops_plan_broadcast_run_due_config": _collect_run_due_gate_config(),
        }

    try:
        import psycopg
        from psycopg.rows import dict_row
    except Exception as exc:  # pragma: no cover - depends on local optional package
        return {
            "plan_id": str(plan_id),
            "source": {"type": "database", "status": "unavailable", "reason": f"psycopg import failed: {exc}"},
            "approval_event": {"exists": False, "event_type": "not_collected"},
            "consumer_runs": [],
            "ops_plan_broadcast_run_due_config": _collect_run_due_gate_config(),
        }

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        conn.execute("BEGIN READ ONLY")
        event = (
            conn.execute(
                """
            select id, event_id, event_type, aggregate_type, aggregate_id, subject_type, subject_id,
                   source_module, source_route, trace_id, payload_summary_json, payload_json
              from internal_event
             where event_type = %s
               and (aggregate_id = %s or subject_id = %s or trace_id = %s)
             order by created_at desc
             limit 1
            """,
                (OPS_PLAN_APPROVED_EVENT_TYPE, str(plan_id), str(plan_id), str(plan_id)),
            ).fetchone()
            or {}
        )
        runs = []
        if event:
            runs = conn.execute(
                """
                select id, consumer_name, status, attempt_count, last_error_code, last_error_message, next_retry_at, result_summary_json
                  from internal_event_consumer_run
                 where event_id = %s
                 order by consumer_name
                """,
                (event["event_id"],),
            ).fetchall()
        broadcast_jobs = _safe_related_rows(conn, "broadcast_jobs", plan_id)
        external_effect_jobs = _safe_related_rows(conn, "external_effect_job", plan_id)
        plan_context = _safe_plan_context(conn, plan_id)
        next_native_target = _safe_next_native_target(conn)
        conn.execute("ROLLBACK")

    return _redact_payload(
        {
            "plan_id": str(plan_id),
            "source": {"type": "database", "status": "readonly_collected"},
            "approval_event": {
                "exists": bool(event),
                "event_id": _redact_id(str(event.get("event_id") or "")),
                "event_type": event.get("event_type") or "not_found",
                "aggregate_type": event.get("aggregate_type") or "not_found",
                "aggregate_id": str(event.get("aggregate_id") or "not_found"),
                "subject_type": event.get("subject_type") or "not_found",
                "subject_id": str(event.get("subject_id") or "not_found"),
                "source_module": event.get("source_module") or "not_found",
                "source_route": event.get("source_route") or "not_found",
                "payload_summary_json": event.get("payload_summary_json") or {},
                "payload_json": event.get("payload_json") or {},
            },
            "consumer_runs": runs,
            "downstream": {
                "broadcast_job_count": len(broadcast_jobs),
                "broadcast_job_ids": [row.get("id") for row in broadcast_jobs],
                "external_effect_job_count": len(external_effect_jobs),
                "external_effect_job_ids": [row.get("id") for row in external_effect_jobs],
            },
            "plan_context": plan_context,
            "next_native_evidence_target": next_native_target,
            "ops_plan_broadcast_run_due_config": _collect_run_due_gate_config(),
        }
    )


def _safe_related_rows(conn: Any, table: str, plan_id: str) -> list[dict[str, Any]]:
    try:
        if table == "broadcast_jobs":
            rows = conn.execute(
                """
                select id, status, source_type, source_id, trace_id, idempotency_key
                  from broadcast_jobs
                 where cast(coalesce(source_id, '') as text) = %s
                    or cast(coalesce(trace_id, '') as text) = %s
                    or cast(coalesce(idempotency_key, '') as text) like %s
                 order by id desc
                 limit 20
                """,
                (str(plan_id), str(plan_id), f"%{str(plan_id)}%"),
            ).fetchall()
            return [dict(row) for row in rows]
        rows = conn.execute(
            """
            select id, raw_status, source_command_id, trace_id, source_event_id
              from external_effect_job
             where cast(coalesce(source_command_id, '') as text) = %s
                or cast(coalesce(trace_id, '') as text) = %s
                or cast(coalesce(source_event_id, '') as text) = %s
             order by id desc
             limit 20
            """,
            (str(plan_id), str(plan_id), str(plan_id)),
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []


def _safe_plan_context(conn: Any, plan_id: str) -> dict[str, Any]:
    try:
        plan = conn.execute(
            """
            select plan_id, coalesce(source_type, 'cloud_plan') as plan_type,
                   coalesce(review_status, status, '') as review_status
              from cloud_broadcast_plans
             where plan_id = %s
             limit 1
            """,
            (str(plan_id),),
        ).fetchone()
        if not plan:
            return {
                "plan_id": str(plan_id),
                "plan_type": "not_found",
                "recipient_projection_count": 0,
                "message_projection_count": 0,
                "projection_source_found": False,
            }
        counts = _safe_plan_projection_counts(conn, str(plan_id))
        return {
            "plan_id": str(plan.get("plan_id") or plan_id),
            "plan_type": str(plan.get("plan_type") or "cloud_plan"),
            "review_status": str(plan.get("review_status") or ""),
            **counts,
            "projection_source_found": True,
        }
    except Exception:
        return {
            "plan_id": str(plan_id),
            "plan_type": "not_collected",
            "recipient_projection_count": 0,
            "message_projection_count": 0,
            "projection_source_found": False,
        }


def _safe_plan_projection_counts(conn: Any, plan_id: str) -> dict[str, int]:
    recipient_count = 0
    message_count = 0
    try:
        recipient_row = conn.execute(
            """
            select count(*)::int as count
              from cloud_broadcast_plan_recipients
             where plan_id = %s
               and coalesce(approval_status, 'pending') <> 'rejected'
               and coalesce(send_status, 'pending') not in ('cancelled', 'sent')
               and coalesce(external_userid, '') <> ''
            """,
            (str(plan_id),),
        ).fetchone()
        recipient_count = int((recipient_row or {}).get("count") or 0)
    except Exception:
        recipient_count = 0
    try:
        message_row = conn.execute(
            """
            select count(*)::int as count
              from cloud_broadcast_plan_recipient_messages
             where plan_id = %s
               and coalesce(status, 'pending') <> 'cancelled'
            """,
            (str(plan_id),),
        ).fetchone()
        message_count = int((message_row or {}).get("count") or 0)
    except Exception:
        message_count = 0
    return {
        "recipient_projection_count": recipient_count,
        "message_projection_count": message_count,
    }


def _safe_next_native_target(conn: Any) -> dict[str, Any]:
    try:
        rows = conn.execute(
            """
            select p.plan_id, coalesce(p.source_type, 'cloud_plan') as plan_type,
                   coalesce(p.review_status, p.status, '') as review_status,
                   exists(
                       select 1
                         from internal_event ie
                        where ie.event_type = %s
                          and (ie.aggregate_id = p.plan_id or ie.subject_id = p.plan_id or ie.trace_id = p.plan_id)
                   ) as approval_event_exists,
                   (
                       select count(*)::int
                         from cloud_broadcast_plan_recipients r
                        where r.plan_id = p.plan_id
                          and coalesce(r.approval_status, 'pending') <> 'rejected'
                          and coalesce(r.send_status, 'pending') not in ('cancelled', 'sent')
                          and coalesce(r.external_userid, '') <> ''
                   ) as recipient_projection_count,
                   (
                       select count(*)::int
                         from cloud_broadcast_plan_recipient_messages m
                        where m.plan_id = p.plan_id
                          and coalesce(m.status, 'pending') <> 'cancelled'
                   ) as message_projection_count
              from cloud_broadcast_plans p
             where coalesce(p.source_type, 'cloud_plan') = 'cloud_plan'
               and coalesce(p.review_status, p.status, '') in ('approved', 'reviewing', 'committed')
             order by p.updated_at desc nulls last, p.created_at desc nulls last
             limit 20
            """,
            (OPS_PLAN_APPROVED_EVENT_TYPE,),
        ).fetchall()
    except Exception:
        return {}
    fallback: dict[str, Any] = {}
    for row in rows:
        candidate = {
            "plan_id": str(row.get("plan_id") or ""),
            "plan_type": str(row.get("plan_type") or "cloud_plan"),
            "review_status": str(row.get("review_status") or ""),
            "approval_event_exists": bool(row.get("approval_event_exists")),
            "recipient_projection_count": int(row.get("recipient_projection_count") or 0),
            "message_projection_count": int(row.get("message_projection_count") or 0),
            "planner_consumer_executable": True,
        }
        if not fallback:
            fallback = candidate
        if candidate["approval_event_exists"] and candidate["recipient_projection_count"] > 0 and candidate["message_projection_count"] > 0:
            return candidate
    return fallback


def _collect_run_due_gate_config() -> dict[str, Any]:
    try:
        from aicrm_next.platform.platform_foundation.internal_events.config import (
            allowed_consumers,
            allowed_event_consumers,
            allowed_event_types,
            auto_execute_enabled,
        )
    except Exception:
        return {
            "token_configured": bool(os.getenv("AICRM_AUTH_AUTOMATION_WORKER_CLIENT_ID", "").strip()),
            "auto_execute_enabled": "not_collected",
            "allowed_event_types": [],
            "allowed_consumers": [],
            "allowed_event_consumers": [],
            "allowlist_required": True,
        }
    return {
        "token_configured": bool(os.getenv("AICRM_AUTH_AUTOMATION_WORKER_CLIENT_ID", "").strip()),
        "auto_execute_enabled": auto_execute_enabled(),
        "allowed_event_types": allowed_event_types(),
        "allowed_consumers": allowed_consumers(),
        "allowed_event_consumers": allowed_event_consumers(),
        "allowlist_required": True,
    }


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                if lowered in SAFE_SENSITIVE_METADATA_KEYS:
                    redacted[key] = _redact_payload(item)
                elif isinstance(item, bool):
                    redacted[key] = item
                elif lowered in {"token_configured"}:
                    redacted[key] = bool(item)
                else:
                    redacted[key] = "[redacted]"
            else:
                redacted[key] = _redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str) and value.startswith("iev_"):
        return _redact_id(value)
    return value


def _redact_id(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Readonly triage for Ops Plan -> Broadcast E2E planner consumer blockers.")
    parser.add_argument("--plan-id", default=DEFAULT_PLAN_ID, help=f"Ops plan id to inspect. Default: {DEFAULT_PLAN_ID}")
    parser.add_argument("--input-json", type=Path, help="Classify a redacted evidence JSON fixture instead of DB.")
    parser.add_argument("--database-url", help="Optional read-only database URL. Defaults to DATABASE_URL.")
    parser.add_argument("--indent", type=int, default=2, help="JSON indentation. Use 0 for compact output.")
    args = parser.parse_args(argv)

    payload = run(plan_id=args.plan_id, input_json=args.input_json, database_url=args.database_url)
    print_json(payload, indent=None if args.indent == 0 else args.indent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
