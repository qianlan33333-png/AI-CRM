from __future__ import annotations

WELCOME_EFFECT_TYPE = "wecom.welcome_message.send"
WELCOME_ERROR_CODE = "wecom_error_41050"
WELCOME_PROVIDER_WINDOW_MS = 20_000
WELCOME_EXECUTION_SCOPE = "_".join(("allowlisted", "canary"))
PRE_CUTOVER_ACKNOWLEDGEMENT_TYPE = "pre_cutover_welcome_41050_no_replay"
PRE_CUTOVER_AUTHORIZATION_BASE_SHA = "7369fa6c7858165097f25dff26f324d109cf7b80"
PRE_CUTOVER_AUTHORIZATION_CONFIRMATION_SHA256 = (
    "23255deb8941ea4a7307fff1c7f45c53721447e3969ad8b7ea58c7306553166e"
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
