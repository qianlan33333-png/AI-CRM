from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aicrm_next.platform.shared.queue_provenance import (
    POST_CUTOVER_IDENTITY_RECOVERY_PREDICATE_VERSION,
    PRE_PROVIDER_IDENTITY_ADOPTION_PREDICATE_VERSION,
)


def _count(row: Mapping[str, Any], key: str) -> int:
    return int(row.get(key) or 0)


def classified_terminal_evidence(row: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    """Build aggregate-only evidence for known, non-retryable business outcomes."""

    counts = {
        key: _count(row, key)
        for key in (
            "canary_failed_retryable_count",
            "canary_failed_terminal_count",
            "canary_blocked_count",
            "callback_welcome_failed_terminal_count",
            "pre_cutover_acknowledged_welcome_count",
            "acknowledged_production_welcome_41050_count",
            "acknowledged_private_message_84061_count",
            "classified_group_message_40058_count",
            "acknowledged_private_message_contact_absence_20260728_count",
            "acknowledged_refund_not_enough_count",
            "refund_not_enough_business_rejection_count",
            "wecom_content_validation_business_rejection_count",
            "wecom_welcome_window_closed_business_rejection_count",
            "wecom_profile_backfill_business_rejection_count",
            "expected_contact_absence_count",
            "private_message_contact_absence_count",
            "pre_cutover_deferred_identity_count",
            "post_cutover_recoverable_identity_count",
        )
    }
    known_terminal_count = sum(
        counts[key]
        for key in (
            "canary_failed_terminal_count",
            "callback_welcome_failed_terminal_count",
            "pre_cutover_acknowledged_welcome_count",
            "acknowledged_production_welcome_41050_count",
            "acknowledged_private_message_84061_count",
            "classified_group_message_40058_count",
            "acknowledged_private_message_contact_absence_20260728_count",
            "acknowledged_refund_not_enough_count",
            "refund_not_enough_business_rejection_count",
            "wecom_content_validation_business_rejection_count",
            "wecom_welcome_window_closed_business_rejection_count",
            "wecom_profile_backfill_business_rejection_count",
            "expected_contact_absence_count",
            "private_message_contact_absence_count",
        )
    )
    common_audit = {
        "excluded_from_business_health": True,
        "provider_success_claimed": False,
        "replay_prohibited": True,
        "strict_provenance_required": True,
    }
    evidence = {
        "id_validation_canary": {
            "failed_retryable_count": counts["canary_failed_retryable_count"],
            "failed_terminal_count": counts["canary_failed_terminal_count"],
            "blocked_count": counts["canary_blocked_count"],
            "callback_welcome_failed_terminal_count": counts["callback_welcome_failed_terminal_count"],
            "excluded_from_business_health": True,
            "strict_provenance_required": True,
        },
        "pre_cutover_welcome_terminal_acknowledgement": {
            "acknowledged_count": counts["pre_cutover_acknowledged_welcome_count"],
            "operator_acknowledgement_required": True,
            **common_audit,
        },
        "production_welcome_41050_acknowledgement": {
            "acknowledged_count": counts["acknowledged_production_welcome_41050_count"],
            "operator_acknowledgement_required": True,
            **common_audit,
        },
        "production_private_message_84061_acknowledgement": {
            "acknowledged_count": counts["acknowledged_private_message_84061_count"],
            "operator_acknowledgement_required": True,
            **common_audit,
        },
        "production_group_message_40058_no_replay_classification": {
            "classified_count": counts["classified_group_message_40058_count"],
            "operator_acknowledgement_required": True,
            **common_audit,
        },
        "production_private_message_contact_absence_20260728_acknowledgement": {
            "acknowledged_count": counts[
                "acknowledged_private_message_contact_absence_20260728_count"
            ],
            "operator_acknowledgement_required": True,
            **common_audit,
        },
        "production_wechat_refund_not_enough_acknowledgement": {
            "acknowledged_count": counts["acknowledged_refund_not_enough_count"],
            "operator_acknowledgement_required": True,
            **common_audit,
        },
        "wechat_refund_not_enough_business_outcome": {
            "completed_count": counts["refund_not_enough_business_rejection_count"],
            "process_outcome": "completed",
            "business_outcome": "rejected",
            "business_reason_code": "insufficient_refund_balance",
            "excluded_from_system_health_failures": True,
            "refund_executed": False,
            "provider_success_claimed": False,
            "replay_prohibited": True,
            "strict_provenance_required": True,
        },
        "wecom_content_validation_business_outcome": {
            "completed_count": counts["wecom_content_validation_business_rejection_count"],
            "process_outcome": "completed",
            "business_outcome": "rejected",
            "business_reason_code": "miniprogram_title_exceeds_64_bytes",
            "excluded_from_system_health_failures": True,
            "provider_boundary_crossed": True,
            "provider_success_claimed": False,
            "replay_prohibited_until_content_fixed": True,
            "strict_provenance_required": True,
        },
        "wecom_welcome_window_closed_business_outcome": {
            "completed_count": counts["wecom_welcome_window_closed_business_rejection_count"],
            "process_outcome": "completed",
            "business_outcome": "not_sent",
            "business_reason_code": "welcome_window_already_closed",
            "excluded_from_system_health_failures": True,
            "provider_boundary_crossed": True,
            "provider_success_claimed": False,
            "replay_prohibited": True,
            "strict_provenance_required": True,
        },
        "wecom_profile_backfill_business_outcome": {
            "completed_count": counts["wecom_profile_backfill_business_rejection_count"],
            "process_outcome": "completed",
            "business_outcome": "rejected",
            "business_reason_code": "external_contact_unavailable_or_invalid",
            "excluded_from_system_health_failures": True,
            "provider_success_claimed": False,
            "replay_prohibited": True,
            "strict_provenance_required": True,
        },
        "external_contact_relationship_absent": {
            "count": counts["expected_contact_absence_count"],
            "excluded_from_business_health": True,
            "provider_boundary_crossed": True,
            "provider_success_claimed": False,
            "replay_prohibited": True,
            "strict_provenance_required": True,
        },
        "private_message_contact_relationship_absent": {
            "count": counts["private_message_contact_absence_count"],
            "process_outcome": "completed",
            "business_outcome": "rejected",
            "business_reason_code": "external_contact_relationship_absent",
            "excluded_from_system_health_failures": True,
            "provider_boundary_crossed": True,
            "provider_success_claimed": False,
            "replay_prohibited": True,
            "strict_provenance_required": True,
        },
        "pre_cutover_deferred_identity_adoption": {
            "eligible_count": counts["pre_cutover_deferred_identity_count"],
            "excluded_from_business_health": True,
            "provider_boundary_crossed": False,
            "pending_generation_1_adoption": True,
            "predicate_version": PRE_PROVIDER_IDENTITY_ADOPTION_PREDICATE_VERSION,
            "strict_provenance_required": True,
        },
        "post_cutover_identity_recovery": {
            "eligible_count": counts["post_cutover_recoverable_identity_count"],
            "excluded_from_business_health": True,
            "provider_boundary_crossed": False,
            "predicate_version": POST_CUTOVER_IDENTITY_RECOVERY_PREDICATE_VERSION,
            "strict_provenance_required": True,
        },
    }
    return evidence, known_terminal_count
