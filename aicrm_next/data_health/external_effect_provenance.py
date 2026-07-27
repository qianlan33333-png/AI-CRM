from __future__ import annotations

from aicrm_next.shared.queue_provenance import (
    external_contact_relationship_absent_terminal_sql,
    post_cutover_identity_recovery_predicate_sql,
    pre_provider_identity_adoption_predicate_sql,
)

WELCOME_EFFECT_TYPE = "wecom.welcome_message.send"
WELCOME_ERROR_CODE = "wecom_error_41050"
WELCOME_PROVIDER_WINDOW_MS = 20_000
WELCOME_EXECUTION_SCOPE = "_".join(("allowlisted", "canary"))
PRE_CUTOVER_ACKNOWLEDGEMENT_TYPE = "pre_cutover_welcome_41050_no_replay"
PRE_CUTOVER_AUTHORIZATION_BASE_SHA = "7369fa6c7858165097f25dff26f324d109cf7b80"
PRE_CUTOVER_AUTHORIZATION_CONFIRMATION_SHA256 = (
    "23255deb8941ea4a7307fff1c7f45c53721447e3969ad8b7ea58c7306553166e"
)
PRODUCTION_AUTHORIZATION_BASE_SHA = "8ab2f80ec8a6808a357a5911ace38128599a3d3d"
PRIVATE_MESSAGE_ACKNOWLEDGEMENT_TYPE = "production_private_message_84061_no_replay"
PRIVATE_MESSAGE_AUTHORIZATION_CONFIRMATION_SHA256 = (
    "023361c05331fd999eb7bdfe4117b312f6bb6393bb1efe34226ac52277f23b57"
)
REFUND_ACKNOWLEDGEMENT_TYPE = "production_wechat_refund_not_enough_no_replay"
REFUND_AUTHORIZATION_CONFIRMATION_SHA256 = (
    "adc4f08542ae86f432c00e6dbe318442b0dd588887ff550d6ee48bd20f2f819b"
)
PRODUCTION_WELCOME_ACKNOWLEDGEMENT_TYPE = "production_welcome_41050_job_2157_no_replay"
PRODUCTION_WELCOME_AUTHORIZATION_BASE_SHA = "1ccf3a056f21d3d023fcf77ac809d0569cc09820"
PRODUCTION_WELCOME_AUTHORIZATION_CONFIRMATION_SHA256 = (
    "e5a4f0fb09cb5d7e64069064139db7cd31ca4d152e5eaa3352f9cf97e2d510d7"
)


def direct_canary_job_sql(alias: str) -> str:
    """Return the strict provenance predicate for dedicated ID-validation effects."""

    return f"""
        COALESCE({alias}.business_type, '') = 'id_validation_canary'
        AND COALESCE({alias}.business_id, '') <> ''
        AND COALESCE({alias}.source_module, '') = 'scripts.ops.plan_wecom_canary'
        AND COALESCE({alias}.source_route, '') = 'scripts/ops/plan_wecom_canary.py'
        AND COALESCE({alias}.trace_id, '') LIKE 'id-validation-canary:%'
        AND COALESCE({alias}.request_id, '') = COALESCE({alias}.business_id, '')
        AND COALESCE({alias}.idempotency_key, '') LIKE 'id-validation-canary:%'
        AND COALESCE({alias}.fairness_key, '') = 'id_validation_canary'
        AND COALESCE({alias}.actor_id, '') LIKE 'github:%'
        AND COALESCE({alias}.actor_type, '') = 'operator'
        AND COALESCE({alias}.risk_level, '') = 'high'
        AND COALESCE({alias}.execution_mode, '') = 'execute'
        AND {alias}.max_attempts = 1
    """


def callback_welcome_failure_sql(alias: str) -> str:
    """Return a fail-closed predicate for a mirrored one-time welcome canary.

    Callback jobs retain their real channel-entry provenance. They are excluded
    from business health only when the append-only validation ledger proves the
    exact job crossed the provider boundary once, failed with WeCom 41050, and
    belongs to a guarded allowlisted callback observation.
    """

    return f"""
        COALESCE({alias}.effect_type, '') = '{WELCOME_EFFECT_TYPE}'
        AND COALESCE({alias}.status, '') = 'failed_terminal'
        AND COALESCE({alias}.last_error_code, '') = '{WELCOME_ERROR_CODE}'
        AND {alias}.side_effect_executed IS TRUE
        AND {alias}.provider_result_received IS TRUE
        AND {alias}.attempt_count = 1
        AND COALESCE({alias}.payload_json ->> 'execution_scope', '') = '{WELCOME_EXECUTION_SCOPE}'
        AND EXISTS (
            SELECT 1
            FROM queue_runtime_validation_evidence callback_evidence
            WHERE callback_evidence.evidence_type = 'wecom_welcome'
              AND callback_evidence.job_id = {alias}.id
              AND callback_evidence.execution_id = COALESCE({alias}.execution_id, '')
              AND callback_evidence.status = 'failed'
              AND callback_evidence.active_generation = {alias}.worker_generation
              AND callback_evidence.policy_version = COALESCE({alias}.policy_version, '')
              AND COALESCE(callback_evidence.actor, '') LIKE 'github:%'
              AND COALESCE(callback_evidence.reason, '') <> ''
              AND COALESCE(callback_evidence.evidence_json ->> 'job_status', '') = 'failed_terminal'
              AND COALESCE(callback_evidence.evidence_json ->> 'job_error_code', '') = '{WELCOME_ERROR_CODE}'
              AND COALESCE(callback_evidence.evidence_json ->> 'execution_scope', '') = '{WELCOME_EXECUTION_SCOPE}'
              AND COALESCE(callback_evidence.evidence_json ->> 'provider_attempt_status', '') = 'failed_terminal'
              AND COALESCE(callback_evidence.evidence_json ->> 'provider_attempt_error_code', '') = '{WELCOME_ERROR_CODE}'
              AND COALESCE(callback_evidence.evidence_json ->> 'provider_adapter_mode', '') = 'execute'
              AND COALESCE(callback_evidence.evidence_json ->> 'provider_error_classification', '') = 'terminal'
              AND COALESCE(callback_evidence.evidence_json ->> 'provider_errcode', '') = '41050'
              AND COALESCE(callback_evidence.evidence_json ->> 'attempt_count', '') = '1'
              AND COALESCE(callback_evidence.evidence_json ->> 'provider_attempt_count', '') = '1'
              AND COALESCE(callback_evidence.evidence_json ->> 'callback_duplicate_count', '') = '0'
              AND COALESCE(callback_evidence.evidence_json ->> 'provider_boundary_started', '') = 'true'
              AND COALESCE(callback_evidence.evidence_json ->> 'provider_result_received', '') = 'true'
              AND COALESCE(callback_evidence.evidence_json ->> 'side_effect_executed', '') = 'true'
              AND COALESCE(callback_evidence.evidence_json ->> 'duplicate_provider_call_proof', '') = 'true'
              AND COALESCE(callback_evidence.evidence_json ->> 'worker_generation_matches', '') = 'true'
              AND COALESCE(callback_evidence.evidence_json ->> 'evidence_type_matches', '') = 'true'
              AND COALESCE(callback_evidence.evidence_json ->> 'job_policy_version_matches', '') = 'true'
              AND COALESCE(callback_evidence.evidence_json ->> 'policy_proof_valid', '') = 'true'
              AND COALESCE(callback_evidence.evidence_json ->> 'provider_policy_gate_passed', '') = 'true'
              AND COALESCE(callback_evidence.evidence_json ->> 'test_receipt_proof_valid', '') = 'true'
              AND COALESCE(callback_evidence.evidence_json ->> 'provider_blocked', '') = 'false'
              AND COALESCE(callback_evidence.evidence_json ->> 'target_values_redacted', '') = 'true'
              AND COALESCE(callback_evidence.evidence_json ->> 'error_messages_redacted', '') = 'true'
              AND COALESCE(callback_evidence.evidence_json ->> 'source_webhook_inbox_id', '') ~ '^[1-9][0-9]*$'
              AND CASE
                    WHEN COALESCE(
                        callback_evidence.evidence_json ->> 'callback_to_provider_boundary_ms',
                        ''
                    ) ~ '^[0-9]+$'
                    THEN (callback_evidence.evidence_json ->> 'callback_to_provider_boundary_ms')::NUMERIC
                    ELSE {WELCOME_PROVIDER_WINDOW_MS}
                  END < {WELCOME_PROVIDER_WINDOW_MS}
        )
    """


def acknowledged_pre_cutover_welcome_failure_sql(alias: str) -> str:
    """Return the one exact operator-authorized no-replay history predicate.

    This does not claim provider success. It recognizes only the generation-0
    welcome terminal that has one provider-crossed attempt, a settled graph,
    and the immutable append-only authorization record.
    """

    return f"""
        COALESCE({alias}.effect_type, '') = '{WELCOME_EFFECT_TYPE}'
        AND COALESCE({alias}.adapter_name, '') = 'wecom_welcome_message'
        AND COALESCE({alias}.operation, '') = 'send'
        AND COALESCE({alias}.business_type, '') = 'channel_welcome_effect_graph'
        AND COALESCE({alias}.source_module, '') = 'channel_entry.application'
        AND COALESCE({alias}.source_route, '') = 'channel_entry.process_channel_entry'
        AND COALESCE({alias}.status, '') = 'failed_terminal'
        AND COALESCE({alias}.last_error_code, '') = '{WELCOME_ERROR_CODE}'
        AND COALESCE({alias}.execution_mode, '') = 'execute'
        AND {alias}.attempt_count = 1
        AND {alias}.max_attempts = 5
        AND {alias}.side_effect_executed IS TRUE
        AND {alias}.provider_result_received IS TRUE
        AND {alias}.provider_call_started_at IS NOT NULL
        AND {alias}.reconciliation_required IS FALSE
        AND {alias}.worker_generation = 0
        AND COALESCE({alias}.policy_version, '') = 'queue-v2-test-loopback'
        AND {alias}.created_at <= TIMESTAMPTZ '2026-07-22T03:07:19Z'
        AND EXISTS (
            SELECT 1
            FROM channel_welcome_effect_graph acknowledged_graph
            WHERE acknowledged_graph.final_effect_job_id = {alias}.id
              AND acknowledged_graph.execution_id = COALESCE({alias}.business_id, '')
              AND acknowledged_graph.status = 'terminal'
        )
        AND 1 = (
            SELECT COUNT(*)
            FROM external_effect_attempt all_acknowledged_attempts
            WHERE all_acknowledged_attempts.job_id = {alias}.id
        )
        AND EXISTS (
            SELECT 1
            FROM external_effect_attempt acknowledged_attempt
            WHERE acknowledged_attempt.job_id = {alias}.id
              AND acknowledged_attempt.attempt_id = COALESCE({alias}.last_attempt_id, '')
              AND acknowledged_attempt.adapter_name = 'wecom_welcome_message'
              AND acknowledged_attempt.adapter_mode = 'execute'
              AND acknowledged_attempt.operation = 'send'
              AND acknowledged_attempt.status = 'failed_terminal'
              AND acknowledged_attempt.error_code = '{WELCOME_ERROR_CODE}'
              AND acknowledged_attempt.provider_call_started_at IS NOT NULL
              AND acknowledged_attempt.worker_generation = 0
              AND acknowledged_attempt.completed_at IS NOT NULL
        )
        AND NOT EXISTS (
            SELECT 1
            FROM external_effect_job acknowledged_success
            WHERE acknowledged_success.id <> {alias}.id
              AND acknowledged_success.status = 'succeeded'
              AND acknowledged_success.effect_type = {alias}.effect_type
              AND COALESCE({alias}.business_id, '') <> ''
              AND acknowledged_success.business_type = {alias}.business_type
              AND acknowledged_success.business_id = {alias}.business_id
        )
        AND NOT EXISTS (
            SELECT 1
            FROM external_effect_job acknowledged_later_success
            WHERE acknowledged_later_success.id <> {alias}.id
              AND acknowledged_later_success.status = 'succeeded'
              AND acknowledged_later_success.effect_type = {alias}.effect_type
              AND acknowledged_later_success.target_type = {alias}.target_type
              AND acknowledged_later_success.target_id = {alias}.target_id
              AND acknowledged_later_success.updated_at > {alias}.updated_at
        )
        AND EXISTS (
            SELECT 1
            FROM queue_terminal_acknowledgement acknowledgement
            WHERE acknowledgement.acknowledgement_type = '{PRE_CUTOVER_ACKNOWLEDGEMENT_TYPE}'
              AND acknowledgement.job_id = {alias}.id
              AND acknowledgement.job_execution_id = COALESCE({alias}.execution_id, '')
              AND acknowledgement.attempt_id = COALESCE({alias}.last_attempt_id, '')
              AND EXISTS (
                  SELECT 1
                  FROM channel_welcome_effect_graph acknowledgement_graph_link
                  WHERE acknowledgement_graph_link.id = acknowledgement.graph_id
                    AND acknowledgement_graph_link.final_effect_job_id = {alias}.id
                    AND acknowledgement_graph_link.execution_id = COALESCE({alias}.business_id, '')
              )
              AND acknowledgement.status = 'acknowledged_history'
              AND acknowledgement.job_status = 'failed_terminal'
              AND acknowledgement.error_code = '{WELCOME_ERROR_CODE}'
              AND acknowledgement.authorization_base_sha = '{PRE_CUTOVER_AUTHORIZATION_BASE_SHA}'
              AND acknowledgement.authorization_confirmation_sha256 =
                  '{PRE_CUTOVER_AUTHORIZATION_CONFIRMATION_SHA256}'
              AND acknowledgement.release_sha ~ '^[0-9a-f]{{40}}$'
              AND acknowledgement.job_fingerprint_sha256 ~ '^[0-9a-f]{{64}}$'
              AND acknowledgement.replay_prohibited IS TRUE
              AND acknowledgement.provider_success_claimed IS FALSE
              AND LENGTH(BTRIM(acknowledgement.actor)) > 0
              AND LENGTH(BTRIM(acknowledgement.reason)) > 0
        )
    """


def acknowledged_private_message_84061_failure_sql(alias: str) -> str:
    """Return the one operator-authorized private-message no-replay history."""

    return f"""
        COALESCE({alias}.effect_type, '') = 'wecom.message.private.send'
        AND COALESCE({alias}.adapter_name, '') = 'wecom_private_message'
        AND COALESCE({alias}.operation, '') = 'send_private_message'
        AND COALESCE({alias}.business_type, '') = 'broadcast_job'
        AND COALESCE({alias}.source_module, '') = 'background_jobs.broadcast_effect_delegate'
        AND COALESCE({alias}.source_route, '') = 'broadcast_effect_delegate'
        AND COALESCE({alias}.status, '') = 'failed_terminal'
        AND COALESCE({alias}.last_error_code, '') = 'external_call_failed_known'
        AND COALESCE({alias}.execution_mode, '') = 'execute'
        AND {alias}.attempt_count = 1
        AND {alias}.max_attempts = 5
        AND {alias}.side_effect_executed IS TRUE
        AND {alias}.provider_result_received IS FALSE
        AND {alias}.worker_generation = 1
        AND COALESCE({alias}.policy_version, '') = 'queue-v2-production-all-g1'
        AND {alias}.updated_at >= TIMESTAMPTZ '2026-07-22T19:42:00+08:00'
        AND {alias}.updated_at < TIMESTAMPTZ '2026-07-22T19:43:00+08:00'
        AND (
            LOWER({alias}.last_error_message) LIKE '%not external contact%'
            OR LOWER({alias}.last_error_message) LIKE '%not a friend%'
            OR LOWER({alias}.last_error_message) LIKE '%external contact%not found%'
        )
        AND 1 = (
            SELECT COUNT(*) FROM external_effect_attempt private_attempt_count
            WHERE private_attempt_count.job_id = {alias}.id
        )
        AND EXISTS (
            SELECT 1 FROM external_effect_attempt private_attempt
            WHERE private_attempt.job_id = {alias}.id
              AND private_attempt.attempt_id = COALESCE({alias}.last_attempt_id, '')
              AND private_attempt.adapter_name = 'wecom_private_message'
              AND private_attempt.adapter_mode = 'execute'
              AND private_attempt.operation = 'send_private_message'
              AND private_attempt.status = 'failed_terminal'
              AND private_attempt.completed_at IS NOT NULL
        )
        AND NOT EXISTS (
            SELECT 1 FROM external_effect_job private_success
            WHERE private_success.id <> {alias}.id
              AND private_success.status = 'succeeded'
              AND private_success.effect_type = {alias}.effect_type
              AND COALESCE({alias}.business_id, '') <> ''
              AND private_success.business_type = {alias}.business_type
              AND private_success.business_id = {alias}.business_id
        )
        AND NOT EXISTS (
            SELECT 1 FROM external_effect_job private_later_success
            WHERE private_later_success.id <> {alias}.id
              AND private_later_success.status = 'succeeded'
              AND private_later_success.effect_type = {alias}.effect_type
              AND private_later_success.target_type = {alias}.target_type
              AND private_later_success.target_id = {alias}.target_id
              AND private_later_success.updated_at > {alias}.updated_at
        )
        AND EXISTS (
            SELECT 1 FROM queue_terminal_acknowledgement acknowledgement
            WHERE acknowledgement.acknowledgement_type = '{PRIVATE_MESSAGE_ACKNOWLEDGEMENT_TYPE}'
              AND acknowledgement.job_id = {alias}.id
              AND acknowledgement.job_execution_id = COALESCE({alias}.execution_id, '')
              AND acknowledgement.attempt_id = COALESCE({alias}.last_attempt_id, '')
              AND acknowledgement.graph_id IS NULL
              AND acknowledgement.status = 'acknowledged_history'
              AND acknowledgement.job_status = 'failed_terminal'
              AND acknowledgement.error_code = 'external_call_failed_known'
              AND acknowledgement.authorization_base_sha = '{PRODUCTION_AUTHORIZATION_BASE_SHA}'
              AND acknowledgement.authorization_confirmation_sha256 =
                  '{PRIVATE_MESSAGE_AUTHORIZATION_CONFIRMATION_SHA256}'
              AND acknowledgement.release_sha ~ '^[0-9a-f]{{40}}$'
              AND acknowledgement.job_fingerprint_sha256 ~ '^[0-9a-f]{{64}}$'
              AND acknowledgement.replay_prohibited IS TRUE
              AND acknowledgement.provider_success_claimed IS FALSE
              AND LENGTH(BTRIM(acknowledgement.actor)) > 0
              AND LENGTH(BTRIM(acknowledgement.reason)) > 0
        )
    """


def acknowledged_refund_not_enough_failure_sql(alias: str) -> str:
    """Return the three operator-authorized refund NOT_ENOUGH histories."""

    return f"""
        COALESCE({alias}.effect_type, '') = 'payment.wechat.refund.request'
        AND COALESCE({alias}.adapter_name, '') = 'wechat_payment'
        AND COALESCE({alias}.operation, '') = 'refund_request'
        AND COALESCE({alias}.business_type, '') = 'commerce_order'
        AND COALESCE({alias}.source_module, '') = 'commerce.admin_transactions'
        AND COALESCE({alias}.source_route, '') =
            'commerce.admin_transactions.create_wechat_refund_request'
        AND COALESCE({alias}.status, '') = 'failed_terminal'
        AND COALESCE({alias}.last_error_code, '') = 'http_403'
        AND COALESCE({alias}.execution_mode, '') = 'execute'
        AND {alias}.attempt_count = 1
        AND {alias}.max_attempts = 5
        AND {alias}.side_effect_executed IS TRUE
        AND {alias}.provider_result_received IS TRUE
        AND {alias}.provider_call_started_at IS NOT NULL
        AND {alias}.worker_generation = 1
        AND COALESCE({alias}.policy_version, '') = 'queue-v2-production-all-g1'
        AND {alias}.updated_at >= TIMESTAMPTZ '2026-07-23T11:41:00+08:00'
        AND {alias}.updated_at < TIMESTAMPTZ '2026-07-23T11:51:00+08:00'
        AND EXISTS (
            SELECT 1 FROM wechat_pay_refunds acknowledged_refund
            WHERE acknowledged_refund.out_refund_no = {alias}.target_id
              AND UPPER(COALESCE(jsonb_extract_path_text(
                    acknowledged_refund.response_payload_json,
                    'provider_payload', 'code'
                  ), '')) = 'NOT_ENOUGH'
        )
        AND 1 = (
            SELECT COUNT(*) FROM external_effect_attempt refund_attempt_count
            WHERE refund_attempt_count.job_id = {alias}.id
        )
        AND EXISTS (
            SELECT 1 FROM external_effect_attempt refund_attempt
            WHERE refund_attempt.job_id = {alias}.id
              AND refund_attempt.attempt_id = COALESCE({alias}.last_attempt_id, '')
              AND refund_attempt.adapter_name = 'wechat_payment'
              AND refund_attempt.adapter_mode = 'execute'
              AND refund_attempt.operation = 'refund_request'
              AND refund_attempt.status = 'failed_terminal'
              AND refund_attempt.provider_call_started_at IS NOT NULL
              AND refund_attempt.completed_at IS NOT NULL
        )
        AND NOT EXISTS (
            SELECT 1 FROM external_effect_job refund_success
            WHERE refund_success.id <> {alias}.id
              AND refund_success.status = 'succeeded'
              AND refund_success.effect_type = {alias}.effect_type
              AND COALESCE({alias}.business_id, '') <> ''
              AND refund_success.business_type = {alias}.business_type
              AND refund_success.business_id = {alias}.business_id
        )
        AND NOT EXISTS (
            SELECT 1 FROM external_effect_job refund_later_success
            WHERE refund_later_success.id <> {alias}.id
              AND refund_later_success.status = 'succeeded'
              AND refund_later_success.effect_type = {alias}.effect_type
              AND refund_later_success.target_type = {alias}.target_type
              AND refund_later_success.target_id = {alias}.target_id
              AND refund_later_success.updated_at > {alias}.updated_at
        )
        AND EXISTS (
            SELECT 1 FROM queue_terminal_acknowledgement acknowledgement
            WHERE acknowledgement.acknowledgement_type = '{REFUND_ACKNOWLEDGEMENT_TYPE}'
              AND acknowledgement.job_id = {alias}.id
              AND acknowledgement.job_execution_id = COALESCE({alias}.execution_id, '')
              AND acknowledgement.attempt_id = COALESCE({alias}.last_attempt_id, '')
              AND acknowledgement.graph_id IS NULL
              AND acknowledgement.status = 'acknowledged_history'
              AND acknowledgement.job_status = 'failed_terminal'
              AND acknowledgement.error_code = 'http_403'
              AND acknowledgement.authorization_base_sha = '{PRODUCTION_AUTHORIZATION_BASE_SHA}'
              AND acknowledgement.authorization_confirmation_sha256 =
                  '{REFUND_AUTHORIZATION_CONFIRMATION_SHA256}'
              AND acknowledgement.release_sha ~ '^[0-9a-f]{{40}}$'
              AND acknowledgement.job_fingerprint_sha256 ~ '^[0-9a-f]{{64}}$'
              AND acknowledgement.replay_prohibited IS TRUE
              AND acknowledgement.provider_success_claimed IS FALSE
              AND LENGTH(BTRIM(acknowledgement.actor)) > 0
              AND LENGTH(BTRIM(acknowledgement.reason)) > 0
        )
    """


def acknowledged_production_welcome_41050_failure_sql(alias: str) -> str:
    """Return the exact no-replay acknowledgement for production job 2157.

    The predicate intentionally pins every durable identifier and timestamp
    that proves the one provider-crossed welcome attempt. A later success,
    extra attempt, changed graph, or altered acknowledgement makes the row
    visible to business health again.
    """

    return f"""
        {alias}.id = 2157
        AND COALESCE({alias}.effect_type, '') = '{WELCOME_EFFECT_TYPE}'
        AND COALESCE({alias}.adapter_name, '') = 'wecom_welcome_message'
        AND COALESCE({alias}.operation, '') = 'send'
        AND COALESCE({alias}.business_type, '') = 'channel_welcome_effect_graph'
        AND COALESCE({alias}.source_module, '') = 'channel_entry.application'
        AND COALESCE({alias}.source_route, '') = 'channel_entry.process_channel_entry'
        AND COALESCE({alias}.status, '') = 'failed_terminal'
        AND COALESCE({alias}.last_error_code, '') = '{WELCOME_ERROR_CODE}'
        AND COALESCE({alias}.execution_mode, '') = 'execute'
        AND COALESCE({alias}.lane, '') = 'wecom_interactive'
        AND COALESCE({alias}.execution_id, '') =
            'exe_channel_welcome_send_4795cc3d70964dc2b8a0fd5c6b875d33'
        AND COALESCE({alias}.business_id, '') =
            'exe_channel_welcome_root_1ae456f3e96b4a97a478a5d4630e8907'
        AND COALESCE({alias}.last_attempt_id, '') =
            'eea_be52aed9558e43e89c189dd6d472e6fc'
        AND {alias}.attempt_count = 1
        AND {alias}.max_attempts = 5
        AND {alias}.side_effect_executed IS TRUE
        AND {alias}.provider_result_received IS TRUE
        AND {alias}.provider_call_started_at =
            TIMESTAMPTZ '2026-07-24T11:31:32.223640+08:00'
        AND {alias}.reconciliation_required IS FALSE
        AND {alias}.worker_generation = 1
        AND COALESCE({alias}.policy_version, '') = 'queue-v2-production-all-g1'
        AND {alias}.created_at = TIMESTAMPTZ '2026-07-24T11:30:38.262825+08:00'
        AND {alias}.updated_at = TIMESTAMPTZ '2026-07-24T11:31:32.739066+08:00'
        AND {alias}.completed_at = TIMESTAMPTZ '2026-07-24T11:31:32.739066+08:00'
        AND EXISTS (
            SELECT 1
            FROM channel_welcome_effect_graph acknowledged_graph
            WHERE acknowledged_graph.id = 3
              AND acknowledged_graph.final_effect_job_id = {alias}.id
              AND acknowledged_graph.execution_id = COALESCE({alias}.business_id, '')
              AND acknowledged_graph.status = 'terminal'
        )
        AND 1 = (
            SELECT COUNT(*)
            FROM external_effect_attempt acknowledged_attempt_count
            WHERE acknowledged_attempt_count.job_id = {alias}.id
        )
        AND EXISTS (
            SELECT 1
            FROM external_effect_attempt acknowledged_attempt
            WHERE acknowledged_attempt.job_id = {alias}.id
              AND acknowledged_attempt.attempt_id = COALESCE({alias}.last_attempt_id, '')
              AND acknowledged_attempt.adapter_name = 'wecom_welcome_message'
              AND acknowledged_attempt.adapter_mode = 'execute'
              AND acknowledged_attempt.operation = 'send'
              AND acknowledged_attempt.status = 'failed_terminal'
              AND acknowledged_attempt.error_code = '{WELCOME_ERROR_CODE}'
              AND acknowledged_attempt.provider_call_started_at =
                  TIMESTAMPTZ '2026-07-24T11:31:32.223640+08:00'
              AND acknowledged_attempt.worker_generation = 1
              AND acknowledged_attempt.started_at =
                  TIMESTAMPTZ '2026-07-24T11:31:32.223640+08:00'
              AND acknowledged_attempt.completed_at =
                  TIMESTAMPTZ '2026-07-24T11:31:32.739066+08:00'
        )
        AND NOT EXISTS (
            SELECT 1
            FROM external_effect_job acknowledged_success
            WHERE acknowledged_success.id <> {alias}.id
              AND acknowledged_success.status = 'succeeded'
              AND acknowledged_success.effect_type = {alias}.effect_type
              AND acknowledged_success.business_type = {alias}.business_type
              AND acknowledged_success.business_id = {alias}.business_id
        )
        AND NOT EXISTS (
            SELECT 1
            FROM external_effect_job acknowledged_later_success
            WHERE acknowledged_later_success.id <> {alias}.id
              AND acknowledged_later_success.status = 'succeeded'
              AND acknowledged_later_success.effect_type = {alias}.effect_type
              AND acknowledged_later_success.target_type = {alias}.target_type
              AND acknowledged_later_success.target_id = {alias}.target_id
              AND acknowledged_later_success.updated_at > {alias}.updated_at
        )
        AND EXISTS (
            SELECT 1
            FROM queue_terminal_acknowledgement acknowledgement
            WHERE acknowledgement.acknowledgement_type =
                    '{PRODUCTION_WELCOME_ACKNOWLEDGEMENT_TYPE}'
              AND acknowledgement.job_id = {alias}.id
              AND acknowledgement.job_execution_id = COALESCE({alias}.execution_id, '')
              AND acknowledgement.attempt_id = COALESCE({alias}.last_attempt_id, '')
              AND acknowledgement.graph_id = 3
              AND acknowledgement.status = 'acknowledged_history'
              AND acknowledgement.job_status = 'failed_terminal'
              AND acknowledgement.error_code = '{WELCOME_ERROR_CODE}'
              AND acknowledgement.authorization_base_sha =
                    '{PRODUCTION_WELCOME_AUTHORIZATION_BASE_SHA}'
              AND acknowledgement.authorization_confirmation_sha256 =
                    '{PRODUCTION_WELCOME_AUTHORIZATION_CONFIRMATION_SHA256}'
              AND acknowledgement.release_sha ~ '^[0-9a-f]{{40}}$'
              AND acknowledgement.job_fingerprint_sha256 ~ '^[0-9a-f]{{64}}$'
              AND acknowledgement.replay_prohibited IS TRUE
              AND acknowledgement.provider_success_claimed IS FALSE
              AND COALESCE(acknowledgement.evidence_json ->> 'durable_provider_attempt_count', '') = '1'
              AND COALESCE(acknowledgement.evidence_json ->> 'active_sibling_count', '') = '0'
              AND COALESCE(acknowledgement.evidence_json ->> 'provider_boundary_crossed', '') = 'true'
              AND COALESCE(acknowledgement.evidence_json ->> 'provider_result_received', '') = 'true'
              AND COALESCE(acknowledgement.evidence_json ->> 'real_external_call_executed_by_acknowledgement', '') = 'false'
              AND COALESCE(acknowledgement.evidence_json ->> 'target_values_redacted', '') = 'true'
              AND COALESCE(acknowledgement.evidence_json ->> 'target_hash_sha256', '') =
                  ENCODE(
                      SHA256(
                          CONVERT_TO(COALESCE({alias}.target_type, ''), 'UTF8')
                          || DECODE('00', 'hex')
                          || CONVERT_TO(COALESCE({alias}.target_id, ''), 'UTF8')
                      ),
                      'hex'
                  )
              AND LENGTH(BTRIM(acknowledgement.actor)) > 0
              AND LENGTH(BTRIM(acknowledgement.reason)) > 0
        )
    """


def external_effect_backlog_sql(*, terminal_lookback_hours: int) -> str:
    """Build the read-only external-effect health aggregation query."""

    lookback_hours = max(1, int(terminal_lookback_hours))
    return f"""
        WITH classified_jobs AS (
            SELECT job.*,
                   (
                       ({direct_canary_job_sql("job")})
                       OR ({callback_welcome_failure_sql("job")})
                   ) AS id_validation_canary,
                   ({callback_welcome_failure_sql("job")})
                       AS id_validation_callback_welcome,
                   ({acknowledged_pre_cutover_welcome_failure_sql("job")})
                       AS pre_cutover_acknowledged_welcome,
                   ({acknowledged_production_welcome_41050_failure_sql("job")})
                       AS acknowledged_production_welcome_41050,
                   ({acknowledged_private_message_84061_failure_sql("job")})
                       AS acknowledged_private_message_84061,
                   ({acknowledged_refund_not_enough_failure_sql("job")})
                       AS acknowledged_refund_not_enough,
                   ({external_contact_relationship_absent_terminal_sql(job_alias="job")})
                       AS expected_contact_absence,
                   EXISTS (
                       SELECT 1
                       FROM crm_user_identity_resolution_queue recovery_identity_queue
                       WHERE {post_cutover_identity_recovery_predicate_sql(job_alias="job", queue_alias="recovery_identity_queue")}
                   ) AS post_cutover_recoverable_identity,
                   EXISTS (
                       SELECT 1
                       FROM crm_user_identity_resolution_queue deferred_identity_queue
                       WHERE {pre_provider_identity_adoption_predicate_sql(
                           job_alias="job",
                           queue_alias="deferred_identity_queue",
                           require_active_source_control=True,
                       )}
                   ) AS pre_cutover_deferred_identity
            FROM external_effect_job job
            WHERE job.status IN ('failed_retryable', 'failed_terminal', 'blocked')
        )
        SELECT
            COUNT(*) FILTER (
                WHERE NOT id_validation_canary
                  AND NOT pre_cutover_acknowledged_welcome
                  AND NOT acknowledged_production_welcome_41050
                  AND NOT acknowledged_private_message_84061
                  AND NOT acknowledged_refund_not_enough
                  AND status = 'failed_retryable'
            ) AS failed_retryable_count,
            COUNT(*) FILTER (
                WHERE NOT id_validation_canary
                  AND NOT pre_cutover_acknowledged_welcome
                  AND NOT acknowledged_production_welcome_41050
                  AND NOT acknowledged_private_message_84061
                  AND NOT acknowledged_refund_not_enough
                  AND NOT expected_contact_absence
                  AND status = 'failed_terminal'
                  AND updated_at >= CURRENT_TIMESTAMP - make_interval(hours => {lookback_hours})
            ) AS recent_failed_terminal_count,
            COUNT(*) FILTER (
                WHERE NOT id_validation_canary
                  AND NOT pre_cutover_acknowledged_welcome
                  AND NOT acknowledged_production_welcome_41050
                  AND NOT acknowledged_private_message_84061
                  AND NOT acknowledged_refund_not_enough
                  AND NOT post_cutover_recoverable_identity
                  AND NOT pre_cutover_deferred_identity
                  AND status = 'blocked'
                  AND updated_at >= CURRENT_TIMESTAMP - make_interval(hours => {lookback_hours})
            ) AS recent_blocked_count,
            COUNT(*) FILTER (
                WHERE NOT id_validation_canary
                  AND NOT pre_cutover_acknowledged_welcome
                  AND NOT acknowledged_production_welcome_41050
                  AND NOT acknowledged_private_message_84061
                  AND NOT acknowledged_refund_not_enough
                  AND status = 'failed_terminal'
            ) AS historical_failed_terminal_count,
            COUNT(*) FILTER (
                WHERE NOT id_validation_canary
                  AND NOT pre_cutover_acknowledged_welcome
                  AND NOT acknowledged_production_welcome_41050
                  AND NOT acknowledged_private_message_84061
                  AND NOT acknowledged_refund_not_enough
                  AND NOT post_cutover_recoverable_identity
                  AND NOT pre_cutover_deferred_identity
                  AND status = 'blocked'
            ) AS historical_blocked_count,
            COUNT(*) FILTER (
                WHERE NOT id_validation_canary
                  AND NOT pre_cutover_acknowledged_welcome
                  AND NOT acknowledged_production_welcome_41050
                  AND NOT acknowledged_private_message_84061
                  AND NOT acknowledged_refund_not_enough
                  AND status = 'failed_retryable'
                  AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
            ) AS due_retryable_count,
            EXTRACT(EPOCH FROM (
                CURRENT_TIMESTAMP - MIN(COALESCE(next_retry_at, updated_at)) FILTER (
                    WHERE NOT id_validation_canary
                      AND NOT pre_cutover_acknowledged_welcome
                      AND NOT acknowledged_production_welcome_41050
                      AND NOT acknowledged_private_message_84061
                      AND NOT acknowledged_refund_not_enough
                      AND status = 'failed_retryable'
                )
            )) AS oldest_failed_retryable_age_seconds,
            COUNT(*) FILTER (
                WHERE id_validation_canary AND status = 'failed_retryable'
            ) AS canary_failed_retryable_count,
            COUNT(*) FILTER (
                WHERE id_validation_canary AND status = 'failed_terminal'
            ) AS canary_failed_terminal_count,
            COUNT(*) FILTER (
                WHERE id_validation_canary AND status = 'blocked'
            ) AS canary_blocked_count,
            COUNT(*) FILTER (
                WHERE id_validation_callback_welcome
                  AND status = 'failed_terminal'
            ) AS callback_welcome_failed_terminal_count,
            COUNT(*) FILTER (
                WHERE pre_cutover_acknowledged_welcome
                  AND status = 'failed_terminal'
            ) AS pre_cutover_acknowledged_welcome_count,
            COUNT(*) FILTER (
                WHERE acknowledged_production_welcome_41050
                  AND status = 'failed_terminal'
            ) AS acknowledged_production_welcome_41050_count,
            COUNT(*) FILTER (
                WHERE acknowledged_private_message_84061
                  AND status = 'failed_terminal'
            ) AS acknowledged_private_message_84061_count,
            COUNT(*) FILTER (
                WHERE acknowledged_refund_not_enough
                  AND status = 'failed_terminal'
            ) AS acknowledged_refund_not_enough_count,
            COUNT(*) FILTER (WHERE expected_contact_absence) AS expected_contact_absence_count,
            COUNT(*) FILTER (
                WHERE post_cutover_recoverable_identity AND status = 'blocked'
            ) AS post_cutover_recoverable_identity_count,
            COUNT(*) FILTER (
                WHERE pre_cutover_deferred_identity AND status = 'blocked'
            ) AS pre_cutover_deferred_identity_count
        FROM classified_jobs
    """
