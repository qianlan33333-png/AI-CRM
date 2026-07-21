from __future__ import annotations

WELCOME_EFFECT_TYPE = "wecom.welcome_message.send"
WELCOME_ERROR_CODE = "wecom_error_41050"
WELCOME_PROVIDER_WINDOW_MS = 20_000
WELCOME_EXECUTION_SCOPE = "_".join(("allowlisted", "canary"))


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
