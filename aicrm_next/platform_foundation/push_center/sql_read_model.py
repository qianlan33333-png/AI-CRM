from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from aicrm_next.platform_foundation.execution_runtime.read_model import (
    queue_policy_eligible_predicate,
)
from aicrm_next.platform_foundation.execution_runtime.repository import (
    external_canary_authorization_predicate,
    external_claim_scope_predicate,
    external_effect_claim_order_sql,
)
from aicrm_next.shared.db_session import get_session_factory
from aicrm_next.shared.runtime import require_signing_secret
from aicrm_next.shared.sensitive_data import redact_sensitive_data, redact_sensitive_text

from .section_mapper import label_for_section


class InvalidPushCenterCursor(ValueError):
    pass


@dataclass(frozen=True)
class PushCenterSQLPage:
    items: list[dict[str, Any]]
    total: int
    counts: dict[str, Any]
    sections: list[dict[str, Any]]
    next_cursor: str
    has_more: bool


SQL_FILTER_KEYS = (
    "section",
    "effect_type",
    "status",
    "business_type",
    "business_id",
    "target_type",
    "target_id",
    "external_userid",
    "owner_userid",
    "trace_id",
    "idempotency_key",
    "source_module",
    "source_route",
    "created_from",
    "created_to",
)


def _external_scope_gated_sql(*, row_alias: str, control_alias: str) -> str:
    scope_allowed = external_claim_scope_predicate(
        row_alias=row_alias,
        scope_expression=f"{control_alias}.external_claim_scope",
    )
    return f"({row_alias}.status IN ('queued', 'failed_retryable') AND NOT ({scope_allowed}))"


_DETAIL_EXTERNAL_SCOPE_GATED_SQL = _external_scope_gated_sql(row_alias="e", control_alias="control")
_PAGE_EXTERNAL_SCOPE_GATED_SQL = _external_scope_gated_sql(row_alias="e", control_alias="control")


_BASE_CTE = r"""
WITH seed_group AS (
    SELECT CASE
        WHEN COALESCE(NULLIF(e.parent_execution_id, ''), NULLIF(e.execution_id, ''), '') <> ''
            THEN 'execution:' || COALESCE(NULLIF(e.parent_execution_id, ''), e.execution_id)
        ELSE 'external_effect_job:' || e.id::text
    END AS group_key
    FROM external_effect_job e
    WHERE :seed_kind = 'external_effect_job' AND e.id = :seed_id
    UNION ALL
    SELECT CASE
        WHEN b.execution_id <> '' THEN 'execution:' || b.execution_id
        ELSE 'broadcast_job:' || b.id::text
    END AS group_key
    FROM broadcast_jobs b
    WHERE :seed_kind = 'broadcast_job' AND b.id = :seed_id
    LIMIT 1
), external_fields AS (
    SELECT
        'external_effect_job'::text AS source_kind,
        e.id::bigint AS source_id,
        CASE
            WHEN COALESCE(NULLIF(e.parent_execution_id, ''), NULLIF(e.execution_id, ''), '') <> ''
                THEN 'execution:' || COALESCE(NULLIF(e.parent_execution_id, ''), e.execution_id)
            ELSE 'external_effect_job:' || e.id::text
        END AS group_key,
        CASE
            WHEN e.effect_type IN ('ai_assist.campaign.message.plan', 'ai_assist.campaign.message.loopback') THEN 'ai_assist'
            WHEN e.effect_type = 'wecom.message.private.send' AND e.business_type = 'ai_assist_campaign' THEN 'ai_assist'
            WHEN e.effect_type = 'wecom.message.private.send' THEN 'private_broadcast'
            WHEN e.effect_type IN ('group_ops.message.loopback', 'group_ops.webhook.action.loopback') THEN 'group_ops'
            WHEN e.effect_type = 'wecom.message.group.send' AND e.business_type = 'group_broadcast' THEN 'group_broadcast'
            WHEN e.effect_type = 'wecom.message.group.send' THEN 'group_ops'
            WHEN e.effect_type = 'wecom.message.broadcast.send' THEN 'group_broadcast'
            WHEN e.effect_type = 'webhook.questionnaire_submission.push' THEN 'questionnaire'
            WHEN e.effect_type = 'webhook.order_paid.push' THEN 'order'
            WHEN e.effect_type IN ('webhook.customer_automation.retry', 'webhook.customer_automation.retry_due') THEN 'customer_webhook'
            WHEN e.effect_type IN ('wecom.contact.tag.mark', 'wecom.contact.tag.unmark', 'wecom.profile.update') THEN 'tags'
            WHEN e.effect_type = 'wecom.welcome_message.send' THEN 'welcome'
            WHEN e.effect_type LIKE 'payment.%' THEN 'payment'
            WHEN e.effect_type IN (
                'feishu.webhook.notify', 'openclaw.context.push', 'media.storage.upload',
                'wecom.media.upload', 'webhook.generic.push'
            ) THEN 'integrations'
            ELSE 'other'
        END AS section,
        e.effect_type,
        e.adapter_name,
        e.operation,
        e.status AS raw_status,
        e.execution_mode,
        e.business_type,
        e.business_id,
        e.target_type,
        e.target_id,
        CASE
            WHEN e.target_type IN ('external_user', 'external_userid', 'wecom_external_user') THEN e.target_id
            ELSE COALESCE(
                NULLIF(e.payload_summary_json->>'external_userid', ''),
                NULLIF(e.payload_summary_json->>'external_user_id', ''),
                NULLIF(e.payload_json->>'external_userid', ''),
                NULLIF(e.payload_json->>'external_user_id', ''),
                ''
            )
        END AS external_userid,
        COALESCE(
            NULLIF(e.payload_summary_json->>'owner_userid', ''),
            NULLIF(e.payload_summary_json->>'sender', ''),
            NULLIF(e.payload_summary_json->>'operator_member_id', ''),
            NULLIF(e.payload_json->>'owner_userid', ''),
            NULLIF(e.actor_id, ''),
            ''
        ) AS owner_userid,
        e.source_module,
        e.source_route,
        e.source_event_id,
        e.source_command_id,
        e.trace_id,
        e.request_id,
        e.idempotency_key,
        e.actor_id,
        e.actor_type,
        e.risk_level,
        e.requires_approval,
        e.attempt_count,
        e.max_attempts,
        e.last_attempt_id,
        e.last_error_code,
        e.last_error_message,
        e.scheduled_at,
        e.next_retry_at,
        e.created_at,
        e.updated_at,
        e.executed_at,
        e.cancelled_at,
        e.payload_summary_json,
        e.available_at,
        e.execution_id,
        e.parent_execution_id,
        e.lane,
        e.row_version,
        e.hold_reason,
        e.policy_version,
        e.cancel_requested_at,
        e.provider_call_started_at,
        (__EXTERNAL_SCOPE_GATED_SQL__) AS policy_gated,
        CASE
            WHEN e.hold_reason <> '' THEN e.hold_reason
            WHEN (__EXTERNAL_SCOPE_GATED_SQL__) THEN 'external_claim_scope_policy_gated'
            WHEN e.status = 'unknown_after_dispatch' THEN 'provider_result_unknown'
            WHEN e.status = 'dispatching' THEN 'provider_call_in_flight'
            WHEN EXISTS (
                SELECT 1
                FROM queue_rate_scope_cooldown cooldown
                WHERE cooldown.rate_scope_key = e.rate_scope_key
                  AND cooldown.blocked_until > CURRENT_TIMESTAMP
            ) THEN 'provider_rate_limited'
            WHEN e.available_at > CURRENT_TIMESTAMP THEN 'not_yet_eligible'
            WHEN e.status = 'queued' THEN 'waiting_for_lane_capacity'
            WHEN e.status = 'failed_retryable' THEN 'retry_wait'
            ELSE ''
        END AS wait_reason,
        EXISTS (
            SELECT 1
            FROM queue_rate_scope_cooldown cooldown
            WHERE cooldown.rate_scope_key = e.rate_scope_key
              AND cooldown.blocked_until > CURRENT_TIMESTAMP
        ) AS rate_limited,
        (e.effect_type IN ('group_ops.message.loopback', 'group_ops.webhook.action.loopback')
            OR e.execution_mode IN ('shadow', 'plan_only', 'execute_dryrun')) AS is_shadow,
        (e.effect_type IN (
            'wecom.message.private.send', 'wecom.message.group.send',
            'wecom.message.broadcast.send', 'wecom.welcome_message.send'
        )) AS is_delivery_effect,
        2 AS primary_rank
    FROM external_effect_job e
    JOIN seed_group seed ON seed.group_key = CASE
        WHEN COALESCE(NULLIF(e.parent_execution_id, ''), NULLIF(e.execution_id, ''), '') <> ''
            THEN 'execution:' || COALESCE(NULLIF(e.parent_execution_id, ''), e.execution_id)
        ELSE 'external_effect_job:' || e.id::text
    END
    JOIN queue_runtime_control control ON control.singleton = TRUE
), external_base AS (
    SELECT f.*,
        jsonb_build_object(
            'id', f.source_id,
            'record_type', f.source_kind,
            'source_type', f.source_kind
        ) || (
            to_jsonb(f) - ARRAY[
                'source_kind', 'source_id', 'group_key', 'rate_limited',
                'is_shadow', 'is_delivery_effect', 'primary_rank'
            ]::text[]
        ) AS record_json,
        NULL::jsonb AS outbound_task_json
    FROM external_fields f
), broadcast_fields AS (
    SELECT
        'broadcast_job'::text AS source_kind,
        b.id::bigint AS source_id,
        CASE
            WHEN b.execution_id <> '' THEN 'execution:' || b.execution_id
            ELSE 'broadcast_job:' || b.id::text
        END AS group_key,
        CASE
            WHEN b.source_table = 'automation_group_ops_plans' OR b.source_id LIKE '%:webhook:%' OR b.source_id LIKE 'group_ops:%' THEN 'group_ops'
            WHEN b.channel = 'wecom_customer_group' THEN 'group_broadcast'
            WHEN b.channel = 'wecom_private' THEN 'private_broadcast'
            ELSE 'other'
        END AS section,
        CASE
            WHEN b.source_table = 'automation_group_ops_plans' OR b.source_id LIKE '%:webhook:%' OR b.source_id LIKE 'group_ops:%'
                THEN 'broadcast_job.group_ops'
            WHEN b.channel = 'wecom_customer_group' THEN 'broadcast_job.group'
            WHEN b.channel = 'wecom_private' THEN 'broadcast_job.private'
            ELSE 'broadcast_job'
        END AS effect_type,
        'broadcast_queue'::text AS adapter_name,
        'send'::text AS operation,
        b.status AS raw_status,
        'execute'::text AS execution_mode,
        CASE
            WHEN b.source_table = 'automation_group_ops_plans' OR b.source_id LIKE '%:webhook:%' OR b.source_id LIKE 'group_ops:%'
                THEN 'group_ops_plan'
            ELSE COALESCE(NULLIF(b.business_domain, ''), b.source_type)
        END AS business_type,
        CASE
            WHEN b.source_id LIKE '%:webhook:%' THEN split_part(b.source_id, ':webhook:', 1)
            ELSE COALESCE(NULLIF(b.content_payload->>'plan_id', ''), NULLIF(b.business_domain, ''), b.source_id)
        END AS business_id,
        COALESCE(NULLIF(b.target_kind, ''), 'broadcast_target') AS target_type,
        CASE
            WHEN b.source_id LIKE '%:webhook:%' THEN split_part(b.source_id, ':webhook:', 2)
            ELSE COALESCE(NULLIF(b.target_summary, ''), b.target_kind)
        END AS target_id,
        ''::text AS external_userid,
        COALESCE(b.created_by, '') AS owner_userid,
        'broadcast_jobs'::text AS source_module,
        '/api/admin/broadcast-jobs'::text AS source_route,
        b.source_id AS source_event_id,
        b.source_id AS source_command_id,
        b.trace_id,
        b.trace_id AS request_id,
        b.idempotency_key,
        b.created_by AS actor_id,
        'system'::text AS actor_type,
        'medium'::text AS risk_level,
        b.requires_approval,
        b.attempt_count,
        b.max_attempts,
        ''::text AS last_attempt_id,
        b.failure_type AS last_error_code,
        b.last_error AS last_error_message,
        b.scheduled_for AS scheduled_at,
        b.next_retry_at,
        b.created_at,
        b.updated_at,
        b.sent_at AS executed_at,
        b.cancelled_at,
        jsonb_build_object(
            'source_id', b.source_id,
            'source_table', b.source_table,
            'target_summary', b.target_summary,
            'target_count', b.target_count,
            'sent_count', b.sent_count,
            'failed_count', b.failed_count,
            'content_summary', b.content_summary,
            'outbound_task_id', b.outbound_task_id
        ) AS payload_summary_json,
        COALESCE(b.next_retry_at, b.scheduled_for, b.created_at) AS available_at,
        b.execution_id,
        ''::text AS parent_execution_id,
        CASE WHEN b.channel = 'wecom_customer_group' THEN 'wecom_bulk' ELSE 'wecom_interactive' END AS lane,
        0::bigint AS row_version,
        b.hold_reason,
        ''::text AS policy_version,
        NULL::timestamptz AS cancel_requested_at,
        NULL::timestamptz AS provider_call_started_at,
        FALSE AS policy_gated,
        CASE
            WHEN b.hold_reason <> '' THEN b.hold_reason
            WHEN b.status = 'delegated' THEN 'owned_by_external_effect'
            WHEN b.status = 'waiting_approval' THEN 'waiting_for_approval'
            WHEN b.status = 'queued' THEN 'waiting_for_external_effect_delegation'
            WHEN b.status = 'unknown_after_dispatch' THEN 'provider_result_unknown'
            ELSE ''
        END AS wait_reason,
        FALSE AS rate_limited,
        FALSE AS is_shadow,
        TRUE AS is_delivery_effect,
        CASE WHEN b.status = 'sent' THEN 0 ELSE 1 END AS primary_rank,
        b.outbound_task_id,
        b.sent_count,
        b.failed_count,
        b.target_count,
        b.target_summary,
        b.content_summary,
        b.source_table,
        b.source_type AS broadcast_source_type,
        b.source_id AS broadcast_source_id,
        b.batch_key,
        o.id AS outbound_id,
        o.task_type AS outbound_task_type,
        o.status AS outbound_task_status,
        o.wecom_task_id AS outbound_task_wecom_task_id,
        o.response_payload AS outbound_task_response_payload,
        o.trace_id AS outbound_task_trace_id,
        b.updated_at AS outbound_task_created_at
    FROM broadcast_jobs b
    JOIN seed_group seed ON seed.group_key = CASE
        WHEN b.execution_id <> '' THEN 'execution:' || b.execution_id
        ELSE 'broadcast_job:' || b.id::text
    END
    LEFT JOIN outbound_tasks o ON o.id = b.outbound_task_id
), broadcast_base AS (
    SELECT f.*,
        jsonb_build_object(
            'id', f.source_id,
            'record_type', f.source_kind,
            'source_type', f.source_kind
        ) || (
            to_jsonb(f) - ARRAY[
                'source_kind', 'source_id', 'group_key', 'rate_limited',
                'is_shadow', 'is_delivery_effect', 'primary_rank',
                'outbound_id', 'outbound_task_type', 'outbound_task_status',
                'outbound_task_wecom_task_id', 'outbound_task_response_payload',
                'outbound_task_trace_id', 'outbound_task_created_at'
            ]::text[]
        ) AS record_json,
        CASE WHEN f.outbound_id IS NULL THEN NULL ELSE jsonb_build_object(
            'id', f.outbound_id,
            'status', f.outbound_task_status,
            'task_type', f.outbound_task_type,
            'wecom_task_id', f.outbound_task_wecom_task_id,
            'trace_id', f.outbound_task_trace_id,
            'created_at', f.outbound_task_created_at,
            'response_payload', COALESCE(f.outbound_task_response_payload, '{}'::jsonb)
        ) END AS outbound_task_json
    FROM broadcast_fields f
), base AS (
    SELECT
        source_kind, source_id, group_key, section, effect_type, adapter_name, operation,
        raw_status, execution_mode, business_type, business_id, target_type, target_id,
        external_userid, owner_userid, source_module, source_route, source_event_id,
        source_command_id, trace_id, request_id, idempotency_key, actor_id, actor_type,
        risk_level, requires_approval, attempt_count, max_attempts, last_attempt_id,
        last_error_code, last_error_message, scheduled_at, next_retry_at, created_at,
        updated_at, executed_at, cancelled_at, payload_summary_json, available_at,
        execution_id, parent_execution_id, lane, row_version, hold_reason,
        policy_version, cancel_requested_at, provider_call_started_at, policy_gated,
        wait_reason, rate_limited,
        is_shadow, is_delivery_effect, primary_rank, record_json,
        outbound_task_json
    FROM external_base
    UNION ALL
    SELECT
        source_kind, source_id, group_key, section, effect_type, adapter_name, operation,
        raw_status, execution_mode, business_type, business_id, target_type, target_id,
        external_userid, owner_userid, source_module, source_route, source_event_id,
        source_command_id, trace_id, request_id, idempotency_key, actor_id, actor_type,
        risk_level, requires_approval, attempt_count, max_attempts, last_attempt_id,
        last_error_code, last_error_message, scheduled_at, next_retry_at, created_at,
        updated_at, executed_at, cancelled_at, payload_summary_json, available_at,
        execution_id, parent_execution_id, lane, row_version, hold_reason,
        policy_version, cancel_requested_at, provider_call_started_at, policy_gated,
        wait_reason, rate_limited,
        is_shadow, is_delivery_effect, primary_rank, record_json,
        outbound_task_json
    FROM broadcast_base
), attempt_groups AS (
    SELECT e.group_key,
        jsonb_agg(jsonb_build_object(
            'id', a.id,
            'attempt_id', a.attempt_id,
            'job_id', a.job_id,
            'adapter_name', a.adapter_name,
            'adapter_mode', a.adapter_mode,
            'operation', a.operation,
            'trace_id', a.trace_id,
            'request_id', a.request_id,
            'status', a.status,
            'raw_status', a.status,
            'request_summary', a.request_summary_json,
            'request_summary_json', a.request_summary_json,
            'response_summary', a.response_summary_json,
            'response_summary_json', a.response_summary_json,
            'error_code', a.error_code,
            'error_message', a.error_message,
            'started_at', a.started_at,
            'completed_at', a.completed_at
        ) ORDER BY a.id DESC) AS attempts
    FROM external_base e
    JOIN external_effect_attempt a ON a.job_id = e.source_id
    GROUP BY e.group_key
), grouped AS (
    SELECT
        b.group_key,
        (jsonb_agg(b.record_json ORDER BY b.primary_rank, b.created_at DESC, b.source_id DESC)->0) AS primary_record,
        COALESCE(jsonb_agg(b.record_json ORDER BY b.created_at DESC, b.source_id DESC)
            FILTER (WHERE b.source_kind = 'external_effect_job'), '[]'::jsonb) AS external_records,
        COALESCE(jsonb_agg(b.record_json ORDER BY b.created_at DESC, b.source_id DESC)
            FILTER (WHERE b.source_kind = 'broadcast_job'), '[]'::jsonb) AS broadcast_records,
        COALESCE(jsonb_agg(b.outbound_task_json ORDER BY b.created_at DESC, b.source_id DESC)
            FILTER (WHERE b.outbound_task_json IS NOT NULL), '[]'::jsonb) AS outbound_records,
        MAX(b.created_at) AS sort_at,
        MAX(b.available_at) AS available_at,
        BOOL_OR(b.source_kind = 'broadcast_job' AND b.raw_status = 'sent') AS broadcast_sent,
        BOOL_OR(b.is_shadow AND b.raw_status IN ('failed', 'failed_retryable', 'failed_terminal', 'blocked', 'cancelled')) AS shadow_failed,
        BOOL_OR(b.source_kind = 'external_effect_job' AND NOT b.is_shadow AND b.raw_status = 'succeeded') AS external_succeeded,
        BOOL_OR(b.raw_status = 'unknown_after_dispatch') AS has_unknown,
        BOOL_OR(b.raw_status = 'simulated') AS has_simulated,
        BOOL_OR(NOT b.is_shadow AND b.raw_status IN ('failed', 'failed_retryable', 'failed_terminal', 'blocked', 'cancelled', 'expired')) AS has_failed,
        BOOL_OR(b.raw_status IN ('claimed', 'running', 'dispatching')) AS has_running,
        BOOL_OR(b.raw_status = 'failed_retryable') AS has_retry_wait,
        BOOL_OR(b.hold_reason <> '') AS has_hold,
        BOOL_OR(b.policy_gated) AS has_policy_gated,
        BOOL_OR(b.rate_limited) AS has_rate_limit,
        BOOL_OR(b.raw_status IN ('planned', 'approved', 'waiting_approval', 'blocked')) AS has_held,
        BOOL_OR(b.raw_status IN ('queued', 'pending', 'delegated')) AS has_waiting,
        BOOL_OR(b.is_delivery_effect AND b.raw_status IN ('succeeded', 'sent')) AS delivery_accepted,
        BOOL_OR(NOT b.is_delivery_effect AND b.raw_status = 'succeeded') AS non_delivery_succeeded,
        COUNT(*) FILTER (WHERE b.source_kind = 'external_effect_job') AS external_count,
        COUNT(*) FILTER (WHERE b.source_kind = 'broadcast_job') AS broadcast_count,
        COUNT(*) FILTER (WHERE b.outbound_task_json IS NOT NULL) AS outbound_count
    FROM base b
    GROUP BY b.group_key
), projection_states AS (
    SELECT g.*,
        CASE
            WHEN g.broadcast_sent AND g.shadow_failed THEN 'sent_with_shadow_warning'
            WHEN g.broadcast_sent THEN 'sent'
            WHEN g.external_succeeded THEN 'succeeded'
            WHEN g.has_unknown THEN 'unknown_after_dispatch'
            WHEN g.has_simulated THEN 'simulated'
            WHEN g.has_failed THEN 'failed'
            WHEN g.shadow_failed THEN 'shadow_failed_not_business_failed'
            WHEN g.has_running THEN 'running'
            WHEN g.has_waiting OR g.has_held THEN 'pending'
            ELSE 'failed'
        END AS effective_status,
        CASE
            WHEN g.has_unknown THEN 'unknown'
            WHEN g.delivery_accepted THEN 'provider_accepted'
            WHEN g.non_delivery_succeeded THEN 'not_applicable'
            WHEN g.has_failed OR g.shadow_failed THEN 'failed'
            ELSE 'pending'
        END AS delivery_state,
        CASE
            WHEN g.has_hold OR g.has_policy_gated THEN 'held'
            WHEN g.has_unknown THEN 'unknown'
            WHEN g.has_running THEN 'running'
            WHEN g.has_retry_wait THEN 'retry_wait'
            WHEN g.has_rate_limit THEN 'rate_limited'
            WHEN g.available_at > CURRENT_TIMESTAMP THEN 'scheduled'
            WHEN g.has_held THEN 'held'
            WHEN g.broadcast_sent OR g.external_succeeded OR g.has_simulated OR g.has_failed THEN 'terminal'
            ELSE 'waiting'
        END AS queue_state
    FROM grouped g
), projections AS (
    SELECT
        p.group_key,
        COALESCE(
            'external_effect_job:' || NULLIF(p.external_records->0->>'id', ''),
            'broadcast_job:' || NULLIF(p.broadcast_records->0->>'id', ''),
            p.group_key
        ) AS projection_id,
        p.sort_at AS created_at,
        p.available_at,
        p.effective_status,
        p.delivery_state,
        p.queue_state,
        p.has_policy_gated AS policy_gated,
        CASE
            WHEN p.has_policy_gated THEN 'external_claim_scope_policy_gated'
            ELSE COALESCE(p.primary_record->>'wait_reason', '')
        END AS wait_reason,
        p.primary_record,
        p.external_records,
        p.broadcast_records,
        p.outbound_records,
        COALESCE(a.attempts, '[]'::jsonb) AS attempts,
        p.external_count,
        p.broadcast_count,
        p.outbound_count,
        COALESCE(jsonb_array_length(a.attempts), 0) AS attempt_count
    FROM projection_states p
    LEFT JOIN attempt_groups a ON a.group_key = p.group_key
), projection_rows AS (
    SELECT
        p.group_key,
        p.projection_id,
        p.created_at,
        p.available_at,
        p.effective_status,
        p.delivery_state,
        p.queue_state,
        p.policy_gated,
        p.wait_reason,
        COALESCE(p.primary_record->>'section', 'other') AS section,
        COALESCE(p.primary_record->>'effect_type', '') AS effect_type,
        COALESCE(p.primary_record->>'business_type', '') AS business_type,
        COALESCE(p.primary_record->>'business_id', '') AS business_id,
        COALESCE(p.primary_record->>'target_type', '') AS target_type,
        COALESCE(p.primary_record->>'target_id', '') AS target_id,
        COALESCE(p.primary_record->>'external_userid', '') AS external_userid,
        COALESCE(p.primary_record->>'owner_userid', '') AS owner_userid,
        COALESCE(p.primary_record->>'trace_id', '') AS trace_id,
        COALESCE(p.primary_record->>'idempotency_key', '') AS idempotency_key,
        COALESCE(p.primary_record->>'source_module', '') AS source_module,
        COALESCE(p.primary_record->>'source_route', '') AS source_route,
        COALESCE(p.primary_record->>'execution_id', '') AS execution_id,
        COALESCE(p.primary_record->>'lane', '') AS lane,
        p.primary_record,
        p.external_records,
        p.broadcast_records,
        p.outbound_records,
        p.attempts,
        p.external_count,
        p.broadcast_count,
        p.outbound_count,
        p.attempt_count
    FROM projections p
), filtered AS (
    SELECT * FROM projection_rows p
    WHERE (:section = '' OR p.section = :section)
      AND (:effect_type = '' OR p.effect_type ILIKE '%' || :effect_type || '%')
      AND (:status = '' OR p.effective_status = :status OR (:status = 'sent' AND p.effective_status = 'sent_with_shadow_warning'))
      AND (:business_type = '' OR p.business_type ILIKE '%' || :business_type || '%')
      AND (:business_id = '' OR p.business_id ILIKE '%' || :business_id || '%')
      AND (:target_type = '' OR p.target_type ILIKE '%' || :target_type || '%')
      AND (:target_id = '' OR p.target_id ILIKE '%' || :target_id || '%')
      AND (:external_userid = '' OR p.external_userid ILIKE '%' || :external_userid || '%')
      AND (:owner_userid = '' OR p.owner_userid ILIKE '%' || :owner_userid || '%')
      AND (:trace_id = '' OR p.trace_id ILIKE '%' || :trace_id || '%')
      AND (:idempotency_key = '' OR p.idempotency_key ILIKE '%' || :idempotency_key || '%')
      AND (:source_module = '' OR p.source_module ILIKE '%' || :source_module || '%')
      AND (:source_route = '' OR p.source_route ILIKE '%' || :source_route || '%')
      AND (:created_from = '' OR p.created_at >= CAST(:created_from AS timestamptz))
      AND (:created_to = '' OR p.created_at <= CAST(:created_to AS timestamptz))
)
""".replace("__EXTERNAL_SCOPE_GATED_SQL__", _DETAIL_EXTERNAL_SCOPE_GATED_SQL)


# Keep the reusable source projection inline so PostgreSQL can prune expensive
# JSON/runtime columns while building the narrow filter/count spool.  Only the
# cursor-selected keys join back to the wide projection for page rendering.
_FAST_PAGE_SQL = r"""
WITH runtime_control AS MATERIALIZED (
    SELECT external_claim_scope
    FROM queue_runtime_control
    WHERE singleton = TRUE
    LIMIT 1
), wide_source_rows AS NOT MATERIALIZED (
    SELECT
        'external_effect_job:' || e.id::text AS projection_id,
        'external_effect_job'::text AS record_type,
        e.id::bigint AS source_record_id,
        e.effect_type,
        e.adapter_name,
        e.operation,
        e.status AS raw_status,
        e.execution_mode,
        e.business_type,
        e.business_id,
        e.target_type,
        e.target_id,
        CASE
            WHEN e.target_type IN ('external_user', 'external_userid', 'wecom_external_user') THEN e.target_id
            ELSE COALESCE(NULLIF(e.payload_summary_json->>'external_userid', ''), NULLIF(e.payload_json->>'external_userid', ''), '')
        END AS external_userid,
        COALESCE(NULLIF(e.payload_summary_json->>'owner_userid', ''), NULLIF(e.payload_json->>'owner_userid', ''), NULLIF(e.actor_id, ''), '') AS owner_userid,
        e.source_module,
        e.source_route,
        e.source_event_id,
        e.source_command_id,
        e.trace_id,
        e.request_id,
        e.idempotency_key,
        e.actor_id,
        e.actor_type,
        e.risk_level,
        e.requires_approval,
        e.attempt_count,
        e.max_attempts,
        e.last_attempt_id,
        e.last_error_code,
        e.last_error_message,
        e.scheduled_at,
        e.next_retry_at,
        e.created_at,
        e.updated_at,
        e.executed_at,
        e.cancelled_at,
        e.payload_summary_json,
        e.available_at,
        e.execution_id,
        e.parent_execution_id,
        e.lane,
        CASE
            WHEN e.effect_type IN ('ai_assist.campaign.message.plan', 'ai_assist.campaign.message.loopback') THEN 'ai_assist'
            WHEN e.effect_type = 'wecom.message.private.send' AND e.business_type = 'ai_assist_campaign' THEN 'ai_assist'
            WHEN e.effect_type = 'wecom.message.private.send' THEN 'private_broadcast'
            WHEN e.effect_type IN ('group_ops.message.loopback', 'group_ops.webhook.action.loopback') THEN 'group_ops'
            WHEN e.effect_type = 'wecom.message.group.send' AND e.business_type = 'group_broadcast' THEN 'group_broadcast'
            WHEN e.effect_type = 'wecom.message.group.send' THEN 'group_ops'
            WHEN e.effect_type = 'wecom.message.broadcast.send' THEN 'group_broadcast'
            WHEN e.effect_type = 'webhook.questionnaire_submission.push' THEN 'questionnaire'
            WHEN e.effect_type = 'webhook.order_paid.push' THEN 'order'
            WHEN e.effect_type IN ('webhook.customer_automation.retry', 'webhook.customer_automation.retry_due') THEN 'customer_webhook'
            WHEN e.effect_type IN ('wecom.contact.tag.mark', 'wecom.contact.tag.unmark', 'wecom.profile.update') THEN 'tags'
            WHEN e.effect_type = 'wecom.welcome_message.send' THEN 'welcome'
            WHEN e.effect_type LIKE 'payment.%' THEN 'payment'
            WHEN e.effect_type IN ('feishu.webhook.notify', 'openclaw.context.push', 'media.storage.upload', 'wecom.media.upload', 'webhook.generic.push') THEN 'integrations'
            ELSE 'other'
        END AS section,
        CASE
            WHEN e.status IN ('planned', 'approved', 'queued') THEN 'pending'
            WHEN e.status IN ('claimed', 'running', 'dispatching') THEN 'running'
            WHEN e.status = 'succeeded' THEN 'succeeded'
            WHEN e.status = 'simulated' THEN 'simulated'
            WHEN e.status = 'unknown_after_dispatch' THEN 'unknown_after_dispatch'
            WHEN e.execution_mode IN ('shadow', 'plan_only', 'execute_dryrun')
                 AND e.status IN ('failed_retryable', 'failed_terminal', 'blocked', 'cancelled')
                THEN 'shadow_failed_not_business_failed'
            ELSE 'failed'
        END AS effective_status,
        CASE
            WHEN e.status = 'unknown_after_dispatch' THEN 'unknown'
            WHEN e.status = 'succeeded' AND e.effect_type IN (
                'wecom.message.private.send', 'wecom.message.group.send',
                'wecom.message.broadcast.send', 'wecom.welcome_message.send'
            ) THEN 'provider_accepted'
            WHEN e.status = 'succeeded' THEN 'not_applicable'
            WHEN e.status IN ('failed_retryable', 'failed_terminal', 'blocked', 'cancelled', 'expired') THEN 'failed'
            ELSE 'pending'
        END AS delivery_state,
        CASE
            WHEN e.hold_reason <> '' THEN 'held'
            WHEN (__EXTERNAL_SCOPE_GATED_SQL__) THEN 'held'
            WHEN e.status = 'unknown_after_dispatch' THEN 'unknown'
            WHEN e.status IN ('claimed', 'running', 'dispatching') THEN 'running'
            WHEN e.status = 'failed_retryable' THEN 'retry_wait'
            WHEN EXISTS (
                SELECT 1
                FROM queue_rate_scope_cooldown cooldown
                WHERE cooldown.rate_scope_key = e.rate_scope_key
                  AND cooldown.blocked_until > CURRENT_TIMESTAMP
            ) THEN 'rate_limited'
            WHEN e.available_at > CURRENT_TIMESTAMP THEN 'scheduled'
            WHEN e.status IN ('planned', 'approved', 'blocked') THEN 'held'
            WHEN e.status = 'queued' THEN 'waiting'
            ELSE 'terminal'
        END AS queue_state,
        e.row_version,
        e.hold_reason,
        e.policy_version,
        e.cancel_requested_at,
        e.provider_call_started_at,
        (__EXTERNAL_SCOPE_GATED_SQL__) AS policy_gated,
        CASE
            WHEN e.hold_reason <> '' THEN e.hold_reason
            WHEN (__EXTERNAL_SCOPE_GATED_SQL__) THEN 'external_claim_scope_policy_gated'
            WHEN e.status = 'unknown_after_dispatch' THEN 'provider_result_unknown'
            WHEN e.status = 'dispatching' THEN 'provider_call_in_flight'
            WHEN EXISTS (
                SELECT 1
                FROM queue_rate_scope_cooldown cooldown
                WHERE cooldown.rate_scope_key = e.rate_scope_key
                  AND cooldown.blocked_until > CURRENT_TIMESTAMP
            ) THEN 'provider_rate_limited'
            WHEN e.available_at > CURRENT_TIMESTAMP THEN 'not_yet_eligible'
            WHEN e.status = 'queued' THEN 'waiting_for_lane_capacity'
            WHEN e.status = 'failed_retryable' THEN 'retry_wait'
            ELSE ''
        END AS wait_reason,
        NULL::bigint AS outbound_task_id,
        NULL::bigint AS sent_count,
        NULL::bigint AS failed_count
    FROM external_effect_job e
    CROSS JOIN runtime_control control

    UNION ALL

    SELECT
        'broadcast_job:' || b.id::text AS projection_id,
        'broadcast_job'::text AS record_type,
        b.id::bigint AS source_record_id,
        CASE
            WHEN b.source_table = 'automation_group_ops_plans' OR b.source_id LIKE '%:webhook:%' OR b.source_id LIKE 'group_ops:%' THEN 'broadcast_job.group_ops'
            WHEN b.channel = 'wecom_customer_group' THEN 'broadcast_job.group'
            WHEN b.channel = 'wecom_private' THEN 'broadcast_job.private'
            ELSE 'broadcast_job'
        END AS effect_type,
        'broadcast_queue'::text AS adapter_name,
        'send'::text AS operation,
        b.status AS raw_status,
        'execute'::text AS execution_mode,
        CASE WHEN b.source_table = 'automation_group_ops_plans' OR b.source_id LIKE '%:webhook:%' THEN 'group_ops_plan' ELSE COALESCE(NULLIF(b.business_domain, ''), b.source_type) END AS business_type,
        CASE WHEN b.source_id LIKE '%:webhook:%' THEN split_part(b.source_id, ':webhook:', 1) ELSE COALESCE(NULLIF(b.content_payload->>'plan_id', ''), NULLIF(b.business_domain, ''), b.source_id) END AS business_id,
        COALESCE(NULLIF(b.target_kind, ''), 'broadcast_target') AS target_type,
        CASE WHEN b.source_id LIKE '%:webhook:%' THEN split_part(b.source_id, ':webhook:', 2) ELSE COALESCE(NULLIF(b.target_summary, ''), b.target_kind) END AS target_id,
        ''::text AS external_userid,
        COALESCE(b.created_by, '') AS owner_userid,
        'broadcast_jobs'::text AS source_module,
        '/api/admin/broadcast-jobs'::text AS source_route,
        b.source_id AS source_event_id,
        b.source_id AS source_command_id,
        b.trace_id,
        b.trace_id AS request_id,
        b.idempotency_key,
        b.created_by AS actor_id,
        'system'::text AS actor_type,
        'medium'::text AS risk_level,
        b.requires_approval,
        b.attempt_count,
        b.max_attempts,
        ''::text AS last_attempt_id,
        b.failure_type AS last_error_code,
        b.last_error AS last_error_message,
        b.scheduled_for AS scheduled_at,
        b.next_retry_at,
        b.created_at,
        b.updated_at,
        b.sent_at AS executed_at,
        b.cancelled_at,
        jsonb_build_object(
            'source_id', b.source_id, 'source_table', b.source_table,
            'target_summary', b.target_summary, 'target_count', b.target_count,
            'sent_count', b.sent_count, 'failed_count', b.failed_count,
            'content_summary', b.content_summary, 'outbound_task_id', b.outbound_task_id
        ) AS payload_summary_json,
        COALESCE(b.next_retry_at, b.scheduled_for, b.created_at) AS available_at,
        b.execution_id,
        ''::text AS parent_execution_id,
        CASE WHEN b.channel = 'wecom_customer_group' THEN 'wecom_bulk' ELSE 'wecom_interactive' END AS lane,
        CASE
            WHEN b.source_table = 'automation_group_ops_plans' OR b.source_id LIKE '%:webhook:%' OR b.source_id LIKE 'group_ops:%' THEN 'group_ops'
            WHEN b.channel = 'wecom_customer_group' THEN 'group_broadcast'
            WHEN b.channel = 'wecom_private' THEN 'private_broadcast'
            ELSE 'other'
        END AS section,
        CASE
            WHEN b.status IN ('waiting_approval', 'queued', 'delegated') THEN 'pending'
            WHEN b.status IN ('claimed', 'dispatching') THEN 'running'
            WHEN b.status = 'sent' THEN 'sent'
            WHEN b.status = 'simulated' THEN 'simulated'
            WHEN b.status = 'unknown_after_dispatch' THEN 'unknown_after_dispatch'
            ELSE 'failed'
        END AS effective_status,
        CASE WHEN b.status = 'sent' THEN 'provider_accepted' WHEN b.status = 'unknown_after_dispatch' THEN 'unknown' WHEN b.status IN ('failed', 'failed_retryable', 'failed_terminal', 'blocked', 'cancelled') THEN 'failed' ELSE 'pending' END AS delivery_state,
        CASE WHEN b.hold_reason <> '' THEN 'held' WHEN b.status = 'unknown_after_dispatch' THEN 'unknown' WHEN b.status IN ('claimed', 'dispatching') THEN 'running' WHEN b.status = 'failed_retryable' THEN 'retry_wait' WHEN COALESCE(b.next_retry_at, b.scheduled_for, b.created_at) > CURRENT_TIMESTAMP THEN 'scheduled' WHEN b.status IN ('waiting_approval', 'blocked') THEN 'held' WHEN b.status IN ('queued', 'delegated') THEN 'waiting' ELSE 'terminal' END AS queue_state,
        0::bigint AS row_version,
        b.hold_reason,
        ''::text AS policy_version,
        NULL::timestamptz AS cancel_requested_at,
        NULL::timestamptz AS provider_call_started_at,
        FALSE AS policy_gated,
        CASE
            WHEN b.hold_reason <> '' THEN b.hold_reason
            WHEN b.status = 'delegated' THEN 'owned_by_external_effect'
            WHEN b.status = 'waiting_approval' THEN 'waiting_for_approval'
            WHEN b.status = 'queued' THEN 'waiting_for_external_effect_delegation'
            WHEN b.status = 'unknown_after_dispatch' THEN 'provider_result_unknown'
            ELSE ''
        END AS wait_reason,
        b.outbound_task_id,
        b.sent_count::bigint,
        b.failed_count::bigint
    FROM broadcast_jobs b
    WHERE COALESCE(NULLIF(b.execution_owner, ''), 'legacy_frozen') <> 'external_effect'
      AND b.status <> 'delegated'
), filtered AS MATERIALIZED (
    SELECT
        CASE p.record_type
            WHEN 'external_effect_job' THEN 1
            ELSE 2
        END::smallint AS record_kind,
        p.source_record_id,
        p.created_at,
        CASE p.section
            WHEN 'ai_assist' THEN 1
            WHEN 'private_broadcast' THEN 2
            WHEN 'group_ops' THEN 3
            WHEN 'group_broadcast' THEN 4
            WHEN 'questionnaire' THEN 5
            WHEN 'order' THEN 6
            WHEN 'customer_webhook' THEN 7
            WHEN 'tags' THEN 8
            WHEN 'welcome' THEN 9
            WHEN 'payment' THEN 10
            WHEN 'integrations' THEN 11
            WHEN 'test_receiver' THEN 12
            ELSE 13
        END::smallint AS section_key,
        CASE p.effective_status
            WHEN 'pending' THEN 1
            WHEN 'running' THEN 2
            WHEN 'succeeded' THEN 3
            WHEN 'sent' THEN 4
            WHEN 'simulated' THEN 5
            WHEN 'unknown_after_dispatch' THEN 6
            WHEN 'shadow_failed_not_business_failed' THEN 7
            WHEN 'sent_with_shadow_warning' THEN 8
            ELSE 9
        END::smallint AS effective_status_key
    FROM wide_source_rows p
    WHERE (:section = '' OR p.section = :section)
      AND (:effect_type = '' OR p.effect_type ILIKE '%' || :effect_type || '%')
      AND (:status = '' OR p.effective_status = :status OR (:status = 'sent' AND p.effective_status = 'sent_with_shadow_warning'))
      AND (:business_type = '' OR p.business_type ILIKE '%' || :business_type || '%')
      AND (:business_id = '' OR p.business_id ILIKE '%' || :business_id || '%')
      AND (:target_type = '' OR p.target_type ILIKE '%' || :target_type || '%')
      AND (:target_id = '' OR p.target_id ILIKE '%' || :target_id || '%')
      AND (:external_userid = '' OR p.external_userid ILIKE '%' || :external_userid || '%')
      AND (:owner_userid = '' OR p.owner_userid ILIKE '%' || :owner_userid || '%')
      AND (:trace_id = '' OR p.trace_id ILIKE '%' || :trace_id || '%')
      AND (:idempotency_key = '' OR p.idempotency_key ILIKE '%' || :idempotency_key || '%')
      AND (:source_module = '' OR p.source_module ILIKE '%' || :source_module || '%')
      AND (:source_route = '' OR p.source_route ILIKE '%' || :source_route || '%')
      AND (:created_from = '' OR p.created_at >= CAST(:created_from AS timestamptz))
      AND (:created_to = '' OR p.created_at <= CAST(:created_to AS timestamptz))
), cursor_keys AS (
    SELECT
        CASE f.record_kind
            WHEN 1 THEN 'external_effect_job:'
            ELSE 'broadcast_job:'
        END || f.source_record_id::text AS projection_id,
        f.record_kind,
        f.source_record_id,
        f.created_at
    FROM filtered f
    WHERE (
        :cursor_created_at = ''
        OR (
            f.created_at,
            CASE f.record_kind
                WHEN 1 THEN 'external_effect_job:'
                ELSE 'broadcast_job:'
            END || f.source_record_id::text
        ) < (CAST(:cursor_created_at AS timestamptz), :cursor_projection_id)
    )
    ORDER BY
        f.created_at DESC,
        CASE f.record_kind
            WHEN 1 THEN 'external_effect_job:'
            ELSE 'broadcast_job:'
        END || f.source_record_id::text DESC
    LIMIT :page_limit OFFSET :legacy_offset
), cursor_page AS (
    SELECT source.*
    FROM cursor_keys page
    CROSS JOIN LATERAL (
        SELECT candidate.*
        FROM wide_source_rows candidate
        WHERE candidate.record_type = CASE page.record_kind
            WHEN 1 THEN 'external_effect_job'
            ELSE 'broadcast_job'
        END
          AND candidate.source_record_id = page.source_record_id
        LIMIT 1
    ) source
    ORDER BY page.created_at DESC, page.projection_id DESC
), summary_counts AS MATERIALIZED (
    SELECT effective_status_key, section_key, COUNT(*) AS count
    FROM filtered
    GROUP BY GROUPING SETS ((effective_status_key), (section_key))
)
SELECT
    COALESCE((
        SELECT jsonb_agg(to_jsonb(c) ORDER BY c.created_at DESC, c.projection_id DESC)
        FROM cursor_page c
    ), '[]'::jsonb) AS items,
    COALESCE((
        SELECT SUM(count)
        FROM summary_counts
        WHERE effective_status_key IS NOT NULL
    ), 0) AS total,
    COALESCE((
        SELECT jsonb_object_agg(
            CASE effective_status_key
                WHEN 1 THEN 'pending'
                WHEN 2 THEN 'running'
                WHEN 3 THEN 'succeeded'
                WHEN 4 THEN 'sent'
                WHEN 5 THEN 'simulated'
                WHEN 6 THEN 'unknown_after_dispatch'
                WHEN 7 THEN 'shadow_failed_not_business_failed'
                WHEN 8 THEN 'sent_with_shadow_warning'
                ELSE 'failed'
            END,
            count
        )
        FROM summary_counts
        WHERE effective_status_key IS NOT NULL
    ), '{}'::jsonb) AS by_status,
    COALESCE((
        SELECT jsonb_object_agg(
            CASE section_key
                WHEN 1 THEN 'ai_assist'
                WHEN 2 THEN 'private_broadcast'
                WHEN 3 THEN 'group_ops'
                WHEN 4 THEN 'group_broadcast'
                WHEN 5 THEN 'questionnaire'
                WHEN 6 THEN 'order'
                WHEN 7 THEN 'customer_webhook'
                WHEN 8 THEN 'tags'
                WHEN 9 THEN 'welcome'
                WHEN 10 THEN 'payment'
                WHEN 11 THEN 'integrations'
                WHEN 12 THEN 'test_receiver'
                ELSE 'other'
            END,
            count
        )
        FROM summary_counts
        WHERE section_key IS NOT NULL
    ), '{}'::jsonb) AS by_section
""".replace("__EXTERNAL_SCOPE_GATED_SQL__", _PAGE_EXTERNAL_SCOPE_GATED_SQL)


_PAGE_SQL = _FAST_PAGE_SQL


_DETAIL_SQL = _BASE_CTE + r"""
SELECT jsonb_build_object(
    'projection_id', p.projection_id,
    'created_at', p.created_at,
    'available_at', p.available_at,
    'effective_status', p.effective_status,
    'delivery_state', p.delivery_state,
    'queue_state', p.queue_state,
    'policy_gated', p.policy_gated,
    'wait_reason', p.wait_reason,
    'section', p.section,
    'effect_type', p.effect_type,
    'business_type', p.business_type,
    'business_id', p.business_id,
    'target_type', p.target_type,
    'target_id', p.target_id,
    'external_userid', p.external_userid,
    'owner_userid', p.owner_userid,
    'trace_id', p.trace_id,
    'idempotency_key', p.idempotency_key,
    'source_module', p.source_module,
    'source_route', p.source_route,
    'execution_id', p.execution_id,
    'lane', p.lane,
    'primary_record', p.primary_record,
    'external_records', p.external_records,
    'broadcast_records', p.broadcast_records,
    'outbound_records', p.outbound_records,
    'attempts', p.attempts,
    'external_count', p.external_count,
    'broadcast_count', p.broadcast_count,
    'outbound_count', p.outbound_count,
    'attempt_count', p.attempt_count
) AS item
FROM projection_rows p
WHERE p.group_key = (
    SELECT seed.group_key
    FROM base seed
    WHERE seed.source_kind = :seed_kind AND seed.source_id = :seed_id
    LIMIT 1
)
LIMIT 1
"""


_QUEUE_CONTEXT_ELIGIBLE_SQL = queue_policy_eligible_predicate(
    row_alias="rows",
    control_alias="control",
    policy_alias="policy",
)
_QUEUE_CONTEXT_CLAIM_ORDER_SQL = external_effect_claim_order_sql(
    row_alias="rows",
    fairness_alias="rows",
)
_QUEUE_CONTEXT_CANARY_AUTHORIZED_SQL = external_canary_authorization_predicate(
    row_alias="e"
)


_QUEUE_CONTEXT_SQL = r"""
WITH target AS (
    SELECT e.id, e.lane, e.available_at
    FROM external_effect_job e
    WHERE :seed_kind = 'external_effect_job' AND e.id = :seed_id
    UNION ALL
    SELECT e.id, e.lane, e.available_at
    FROM broadcast_jobs b
    JOIN external_effect_job e ON e.id = b.external_effect_job_id
    WHERE :seed_kind = 'broadcast_job' AND b.id = :seed_id
    LIMIT 1
), candidate_rows AS (
    SELECT
        e.id,
        e.lane,
        e.priority,
        e.available_at,
        e.hold_reason,
        e.attempt_count,
        e.max_attempts,
        e.worker_generation,
        e.policy_version,
        e.status IN ('queued', 'failed_retryable') AS ready_state,
        e.status = 'dispatching' AS in_flight,
        e.status = 'unknown_after_dispatch' AS unknown_state,
        e.status IN ('failed_terminal', 'blocked') AS dlq_state,
        COALESCE(e.payload_json->>'execution_scope', '') AS execution_scope,
        (__CANARY_AUTHORIZED_SQL__) AS canary_authorized,
        fairness.last_claimed_at,
        (e.lease_expires_at IS NULL OR e.lease_expires_at <= CURRENT_TIMESTAMP) AS lease_available,
        EXISTS (
            SELECT 1
            FROM queue_rate_scope_cooldown cooldown
            WHERE cooldown.rate_scope_key = e.rate_scope_key
              AND cooldown.blocked_until > CURRENT_TIMESTAMP
        ) AS rate_limited,
        EXISTS (
            SELECT 1
            FROM external_effect_job active
            WHERE active.lane = e.lane
              AND active.ordering_key = e.ordering_key
              AND active.ordering_key <> ''
              AND active.status = 'dispatching'
              AND active.lease_expires_at > CURRENT_TIMESTAMP
        ) AS ordering_blocked,
        'external_effect'::text AS queue_kind
    FROM external_effect_job e
    LEFT JOIN queue_fairness_cursor fairness
      ON fairness.lane = e.lane
     AND fairness.fairness_key = e.fairness_key
    WHERE e.lane = (SELECT lane FROM target LIMIT 1)
      AND e.status IN ('queued', 'failed_retryable')
), ranked_eligible AS (
    SELECT
        rows.id,
        rows.lane,
        ROW_NUMBER() OVER (
            PARTITION BY rows.lane
            ORDER BY __CLAIM_ORDER_SQL__
        ) AS claim_rank
    FROM candidate_rows rows
    CROSS JOIN queue_runtime_control control
    JOIN queue_lane_policy policy ON policy.lane = rows.lane
    WHERE control.singleton = TRUE
      AND rows.lease_available
      AND (__ELIGIBLE_SQL__)
)
SELECT
    target.lane,
    target.available_at AS eligible_at,
    policy.max_in_flight AS lane_capacity,
    COALESCE(ranked.claim_rank - 1, 0)::BIGINT AS lane_ahead_count,
    (ranked.claim_rank IS NOT NULL) AS queue_position_eligible,
    (
        SELECT COUNT(*)::BIGINT
        FROM external_effect_job active
        WHERE active.lane = target.lane
          AND active.status = 'dispatching'
          AND active.lease_expires_at > CURRENT_TIMESTAMP
    ) AS lane_in_flight
FROM target
JOIN queue_lane_policy policy ON policy.lane = target.lane
LEFT JOIN ranked_eligible ranked ON ranked.id = target.id
LIMIT 1
""".replace("__ELIGIBLE_SQL__", _QUEUE_CONTEXT_ELIGIBLE_SQL).replace(
    "__CLAIM_ORDER_SQL__",
    _QUEUE_CONTEXT_CLAIM_ORDER_SQL,
).replace(
    "__CANARY_AUTHORIZED_SQL__",
    _QUEUE_CONTEXT_CANARY_AUTHORIZED_SQL,
)


def _cursor_secret() -> bytes:
    return require_signing_secret(
        "AICRM_PUSH_CENTER_CURSOR_SECRET",
        local_fallback="aicrm-push-center-local-cursor",
        fallback_env_keys=("SECRET_KEY",),
    )


def _filters_fingerprint(filters: dict[str, Any]) -> str:
    payload = {key: str(value or "").strip() for key, value in sorted(filters.items()) if str(value or "").strip()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]


def encode_push_center_cursor(*, created_at: str, projection_id: str, filters: dict[str, Any]) -> str:
    payload = {
        "v": 1,
        "created_at": str(created_at or ""),
        "projection_id": str(projection_id or ""),
        "filters": _filters_fingerprint(filters),
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = base64.urlsafe_b64encode(hmac.new(_cursor_secret(), body.encode("ascii"), hashlib.sha256).digest()).decode("ascii").rstrip("=")
    return f"{body}.{signature}"


def decode_push_center_cursor(value: str, *, filters: dict[str, Any]) -> tuple[str, str]:
    cursor = str(value or "").strip()
    if not cursor:
        return "", ""
    try:
        body, supplied_signature = cursor.rsplit(".", 1)
        expected_signature = base64.urlsafe_b64encode(
            hmac.new(_cursor_secret(), body.encode("ascii"), hashlib.sha256).digest()
        ).decode("ascii").rstrip("=")
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise InvalidPushCenterCursor("invalid cursor signature")
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except InvalidPushCenterCursor:
        raise
    except Exception as exc:
        raise InvalidPushCenterCursor("invalid cursor") from exc
    if (
        payload.get("v") != 1
        or not str(payload.get("created_at") or "")
        or not str(payload.get("projection_id") or "")
        or payload.get("filters") != _filters_fingerprint(filters)
    ):
        raise InvalidPushCenterCursor("cursor does not match this filter")
    return str(payload["created_at"]), str(payload["projection_id"])


def _json_value(value: Any, *, default: Any) -> Any:
    if value is None:
        return default
    if hasattr(value, "obj"):
        value = value.obj
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _status_label(status: str) -> str:
    return {
        "pending": "待执行",
        "running": "执行中",
        "succeeded": "执行成功",
        "sent": "已发送",
        "simulated": "模拟执行",
        "unknown_after_dispatch": "结果待核对",
        "failed": "发送失败",
        "sent_with_shadow_warning": "已发送 · 影子链路异常",
        "shadow_failed_not_business_failed": "影子链路失败，未发现主发送记录",
    }.get(status, status or "-")


def _queue_wait_seconds(*, created_at: Any, available_at: Any, queue_state: str) -> int:
    if queue_state not in {"waiting", "retry_wait", "rate_limited", "held"}:
        return 0
    values: list[datetime] = []
    for value in (created_at, available_at):
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
            except ValueError:
                continue
        values.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
    if not values:
        return 0
    return max(0, int((datetime.now(timezone.utc) - min(values).astimezone(timezone.utc)).total_seconds()))


def _safe_linked_record(value: Any) -> dict[str, Any]:
    record = dict(value or {}) if isinstance(value, dict) else {}
    for key in (
        "payload_summary",
        "payload_summary_json",
        "request_summary",
        "request_summary_json",
        "response_summary",
        "response_summary_json",
        "response_payload",
    ):
        if isinstance(record.get(key), dict):
            record[key] = redact_sensitive_data(record[key])
    for key in ("last_error_message", "error_message"):
        if key in record:
            record[key] = redact_sensitive_text(record.get(key))
    return record


def _public_item(raw: dict[str, Any]) -> dict[str, Any]:
    primary_payload = _json_value(raw.get("primary_record"), default={}) or {}
    if not primary_payload and raw.get("source_record_id") is not None:
        # The list query intentionally returns one skinny source row per item so
        # it can use keyset pagination without materialising every linked
        # record.  Normalise that row to the same public shape as the grouped
        # detail query.
        primary_payload = {
            **raw,
            "id": raw.get("source_record_id"),
            "record_type": raw.get("record_type"),
            "source_type": raw.get("record_type"),
        }
    primary = _safe_linked_record(primary_payload)
    external_records = [_safe_linked_record(item) for item in list(_json_value(raw.get("external_records"), default=[]) or [])]
    broadcast_records = [_safe_linked_record(item) for item in list(_json_value(raw.get("broadcast_records"), default=[]) or [])]
    attempts = [_safe_linked_record(item) for item in list(_json_value(raw.get("attempts"), default=[]) or [])]
    outbound_records = [_safe_linked_record(item) for item in list(_json_value(raw.get("outbound_records"), default=[]) or [])]
    if not external_records and primary.get("record_type") == "external_effect_job":
        external_records = [primary]
    if not broadcast_records and primary.get("record_type") == "broadcast_job":
        broadcast_records = [primary]
    if not outbound_records and primary.get("outbound_task_id"):
        outbound_records = [{"id": primary.get("outbound_task_id")}]
    status = str(raw.get("effective_status") or "")
    projection_id = str(raw.get("projection_id") or "")
    item = {
        **primary,
        "id": projection_id,
        "projection_id": projection_id,
        "source_record_id": primary.get("id"),
        "display_id": (
            f"#{external_records[0].get('id')}" if external_records
            else f"B#{broadcast_records[0].get('id')}" if broadcast_records
            else "-"
        ),
        "section": raw.get("section") or primary.get("section") or "other",
        "section_label": label_for_section(str(raw.get("section") or primary.get("section") or "other")),
        "effective_status": status,
        "effective_status_label": _status_label(status),
        "status": status,
        "status_label": _status_label(status),
        "queue_state": str(raw.get("queue_state") or "waiting"),
        "delivery_state": str(raw.get("delivery_state") or "pending"),
        "execution_id": str(raw.get("execution_id") or primary.get("execution_id") or ""),
        "parent_execution_id": str(raw.get("parent_execution_id") or primary.get("parent_execution_id") or ""),
        "lane": str(raw.get("lane") or primary.get("lane") or ""),
        "available_at": raw.get("available_at") or primary.get("available_at"),
        "eligible_at": raw.get("available_at") or primary.get("available_at"),
        "created_at": raw.get("created_at") or primary.get("created_at"),
        "row_version": int(primary.get("row_version") or 0),
        "hold_reason": str(primary.get("hold_reason") or ""),
        "policy_version": str(primary.get("policy_version") or ""),
        "policy_gated": bool(raw.get("policy_gated") or primary.get("policy_gated")),
        "wait_reason": str(raw.get("wait_reason") or primary.get("wait_reason") or primary.get("hold_reason") or ""),
        "cancel_requested_at": primary.get("cancel_requested_at"),
        "provider_call_started_at": primary.get("provider_call_started_at"),
        "raw_statuses": {
            "external_effect_jobs": [record.get("raw_status") for record in external_records],
            "broadcast_jobs": [record.get("raw_status") for record in broadcast_records],
        },
        "linked_record_counts": {
            "external_effect_jobs": int(raw.get("external_count") or len(external_records)),
            "external_effect_attempts": int(raw.get("attempt_count") or len(attempts)),
            "broadcast_jobs": int(raw.get("broadcast_count") or len(broadcast_records)),
            "outbound_tasks": int(raw.get("outbound_count") or len(outbound_records)),
        },
        "linked_records": {
            "external_effect_jobs": external_records,
            "external_effect_attempts": attempts,
            "broadcast_jobs": broadcast_records,
            "outbound_tasks": outbound_records,
        },
    }
    item["queue_wait_seconds"] = _queue_wait_seconds(
        created_at=item.get("created_at"),
        available_at=item.get("available_at"),
        queue_state=item["queue_state"],
    )
    item["root_execution_id"] = item["parent_execution_id"] or item["execution_id"]
    if not item["wait_reason"]:
        item["wait_reason"] = {
            "waiting": "waiting_for_lane_capacity",
            "retry_wait": "retry_wait",
            "rate_limited": "provider_rate_limited",
            "scheduled": "not_yet_eligible",
            "running": "provider_call_in_flight",
            "unknown": "provider_result_unknown",
        }.get(item["queue_state"], "")
    item["payload_summary"] = primary.get("payload_summary_json") or {}
    item["payload_summary_json"] = primary.get("payload_summary_json") or {}
    return item


class SQLPushCenterReadModel:
    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def query(
        self,
        filters: dict[str, Any],
        *,
        limit: int,
        cursor: str = "",
        legacy_offset: int = 0,
    ) -> PushCenterSQLPage:
        normalized = {key: str(filters.get(key) or "").strip() for key in SQL_FILTER_KEYS}
        cursor_created_at, cursor_projection_id = decode_push_center_cursor(cursor, filters=normalized)
        params = {
            **normalized,
            "cursor_created_at": cursor_created_at,
            "cursor_projection_id": cursor_projection_id,
            "page_limit": max(1, min(int(limit or 50), 200)) + 1,
            "legacy_offset": 0 if cursor else max(0, int(legacy_offset or 0)),
        }
        with self._session_factory() as session:
            row = session.execute(text(_PAGE_SQL), params).mappings().fetchone()
        payload = dict(row or {})
        raw_items = list(_json_value(payload.get("items"), default=[]) or [])
        page_size = max(1, min(int(limit or 50), 200))
        has_more = len(raw_items) > page_size
        raw_items = raw_items[:page_size]
        items = [_public_item(dict(item or {})) for item in raw_items]
        next_cursor = ""
        if has_more and items:
            next_cursor = encode_push_center_cursor(
                created_at=str(items[-1].get("created_at") or ""),
                projection_id=str(items[-1].get("projection_id") or ""),
                filters=normalized,
            )
        by_status = dict(_json_value(payload.get("by_status"), default={}) or {})
        by_section = dict(_json_value(payload.get("by_section"), default={}) or {})
        total = int(payload.get("total") or 0)
        counts = {
            "total": total,
            "by_effective_status": {str(key): int(value or 0) for key, value in by_status.items()},
            "by_status": {str(key): int(value or 0) for key, value in by_status.items()},
            "by_section": {str(key): int(value or 0) for key, value in by_section.items()},
            "pending": int(by_status.get("pending") or 0),
            "running": int(by_status.get("running") or 0),
            "succeeded": int(by_status.get("succeeded") or 0),
            "sent": int(by_status.get("sent") or 0) + int(by_status.get("sent_with_shadow_warning") or 0),
            "failed": int(by_status.get("failed") or 0),
            "shadow_warning": int(by_status.get("sent_with_shadow_warning") or 0) + int(by_status.get("shadow_failed_not_business_failed") or 0),
        }
        from .section_mapper import all_sections

        sections = [
            {
                **section,
                "count": int(by_section.get(section["key"]) or 0),
                "label": label_for_section(section["key"]),
            }
            for section in all_sections()
        ]
        return PushCenterSQLPage(
            items=items,
            total=total,
            counts=counts,
            sections=sections,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    def get(self, projection_id: str) -> dict[str, Any] | None:
        value = str(projection_id or "").strip()
        kind, separator, raw_id = value.partition(":")
        if not separator:
            kind, raw_id = "external_effect_job", value
        if kind not in {"external_effect_job", "broadcast_job"}:
            return None
        try:
            seed_id = int(raw_id)
        except (TypeError, ValueError):
            return None
        params = {
            "seed_kind": kind,
            "seed_id": seed_id,
            **{key: "" for key in (
                "section", "effect_type", "status", "business_type", "business_id", "target_type",
                "target_id", "external_userid", "owner_userid", "trace_id", "idempotency_key",
                "source_module", "source_route", "created_from", "created_to",
            )},
        }
        with self._session_factory() as session:
            row = session.execute(text(_DETAIL_SQL), params).mappings().fetchone()
            queue_context_row = session.execute(text(_QUEUE_CONTEXT_SQL), params).mappings().fetchone()
        if not row:
            return None
        item = _json_value(row.get("item"), default={})
        if not item:
            return None
        public_item = _public_item(dict(item or {}))
        queue_context = dict(queue_context_row or {})
        public_item.update(
            {
                "lane_ahead_count": int(queue_context.get("lane_ahead_count") or 0),
                "lane_capacity": int(queue_context.get("lane_capacity") or 0),
                "lane_in_flight": int(queue_context.get("lane_in_flight") or 0),
                "eligible_at": queue_context.get("eligible_at") or public_item.get("eligible_at"),
                "queue_position_eligible": bool(queue_context.get("queue_position_eligible")),
                "queue_position_scope": (
                    "lane_eligible_snapshot"
                    if queue_context.get("queue_position_eligible")
                    else "not_currently_eligible"
                ),
            }
        )
        return public_item
