from __future__ import annotations


PRE_PROVIDER_IDENTITY_ADOPTION_SOURCE_POLICY = "queue-v2-test-loopback"
PRE_PROVIDER_IDENTITY_ADOPTION_PREDICATE_VERSION = "identity_contact_detail_test_policy_v2"
POST_CUTOVER_IDENTITY_RECOVERY_POLICY = "queue-v2-production-all-g1"
POST_CUTOVER_IDENTITY_RECOVERY_PREDICATE_VERSION = "identity_contact_detail_all_scope_preprovider_v2"
EXTERNAL_CONTACT_RELATIONSHIP_ABSENT_ERROR_CODES = (
    "external_contact_relationship_absent",
    "wecom_error_84061",
    "wecom_errcode_84061",
)


def pre_provider_identity_adoption_predicate_sql(
    *,
    job_alias: str,
    queue_alias: str,
    require_active_source_control: bool = False,
) -> str:
    """Return the shared fail-closed predicate for generation-0 identity adoption.

    The data-health gate and the all-scope transition deliberately use the same
    predicate. Data health additionally requires the live control plane to
    remain at generation 0/test-loopback so a stale blocked row becomes red
    again immediately after cutover.
    """

    active_control = ""
    if require_active_source_control:
        active_control = f"""
              AND EXISTS (
                  SELECT 1
                  FROM queue_runtime_control adoption_control
                  WHERE adoption_control.singleton = TRUE
                    AND adoption_control.active_generation = 0
                    AND adoption_control.policy_version = '{PRE_PROVIDER_IDENTITY_ADOPTION_SOURCE_POLICY}'
                    AND adoption_control.external_claim_scope = 'test_loopback'
              )
        """
    return f"""
              {queue_alias}.external_effect_job_id = {job_alias}.id
              AND {queue_alias}.id::TEXT = {job_alias}.business_id
              AND {job_alias}.effect_type = 'wecom.external_contact.detail.fetch'
              AND {job_alias}.adapter_name = 'wecom_external_contact_detail'
              AND {job_alias}.operation = 'get_external_contact_detail'
              AND {job_alias}.business_type = 'identity_resolution_queue'
              AND {job_alias}.source_module IN (
                  'aicrm_next.identity_contact.resolution_effects',
                  'aicrm_next.crm.identity_contact.resolution_effects'
              )
              AND {job_alias}.source_route IN (
                  'channel_entry.identity_resolution.enqueue',
                  'message_archive.identity_resolution.enqueue'
              )
              AND {job_alias}.status = 'blocked'
              AND {job_alias}.last_error_code = 'effect_type_not_allowed'
              AND {job_alias}.execution_mode = 'execute'
              AND {job_alias}.attempt_count = 1
              AND {job_alias}.max_attempts = 5
              AND {job_alias}.worker_generation = 0
              AND {job_alias}.policy_version = '{PRE_PROVIDER_IDENTITY_ADOPTION_SOURCE_POLICY}'
              AND {job_alias}.side_effect_executed = FALSE
              AND {job_alias}.provider_result_received = FALSE
              AND {job_alias}.provider_call_started_at IS NULL
              AND {job_alias}.reconciliation_required = FALSE
              AND {job_alias}.lease_token = ''
              AND {job_alias}.lease_expires_at IS NULL
              AND {job_alias}.locked_by = ''
              AND {job_alias}.locked_at IS NULL
              AND {job_alias}.hold_reason = ''
              AND {job_alias}.cancel_requested_at IS NULL
              AND {queue_alias}.status IN ('pending', 'held')
              AND (
                  (
                      {queue_alias}.status = 'pending'
                      AND {queue_alias}.hold_reason = ''
                      AND {queue_alias}.last_error = ''
                  )
                  OR (
                      {queue_alias}.status = 'held'
                      AND {queue_alias}.hold_reason = 'effect_type_not_allowed'
                      AND {queue_alias}.last_error = 'effect_type_not_allowed'
                  )
              )
              AND (
                  SELECT COUNT(*)
                  FROM external_effect_attempt adoption_attempt_count
                  WHERE adoption_attempt_count.job_id = {job_alias}.id
              ) = 1
              AND EXISTS (
                  SELECT 1
                  FROM external_effect_attempt adoption_attempt
                  WHERE adoption_attempt.job_id = {job_alias}.id
                    AND adoption_attempt.status = 'blocked'
                    AND adoption_attempt.error_code = 'effect_type_not_allowed'
                    AND adoption_attempt.adapter_name = 'wecom_external_contact_detail'
                    AND adoption_attempt.operation = 'get_external_contact_detail'
                    AND adoption_attempt.adapter_mode = 'disabled'
                    AND adoption_attempt.provider_call_started_at IS NULL
                    AND adoption_attempt.worker_generation = 0
              )
              {active_control}
    """


def post_cutover_identity_recovery_predicate_sql(
    *,
    job_alias: str,
    queue_alias: str,
    policy_version: str = POST_CUTOVER_IDENTITY_RECOVERY_POLICY,
) -> str:
    """Return the exact predicate for post-cutover pre-provider gate-only blocks.

    The first all-scope transition preserved the generation-0 blocked attempt,
    then the still-missing typed effect allowlist produced one more blocked
    attempt without opening the provider boundary. New identity work can also
    reach the same gate once under generation 1 before the typed effect is
    enabled. Recovery is allowed only for either strict one- or two-attempt
    history on the immutable identity routes, with every durable attempt still
    proving that the provider boundary was never opened.
    """

    normalized_policy = str(policy_version or "").strip()
    if not normalized_policy or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
        for character in normalized_policy
    ):
        raise ValueError("post-cutover recovery policy version is invalid")

    return f"""
              {queue_alias}.external_effect_job_id = {job_alias}.id
              AND {queue_alias}.id::TEXT = {job_alias}.business_id
              AND {job_alias}.effect_type = 'wecom.external_contact.detail.fetch'
              AND {job_alias}.adapter_name = 'wecom_external_contact_detail'
              AND {job_alias}.operation = 'get_external_contact_detail'
              AND {job_alias}.target_type = 'external_user'
              AND {job_alias}.business_type = 'identity_resolution_queue'
              AND {job_alias}.source_module IN (
                  'aicrm_next.identity_contact.resolution_effects',
                  'aicrm_next.crm.identity_contact.resolution_effects'
              )
              AND {job_alias}.source_route IN (
                  'channel_entry.identity_resolution.enqueue',
                  'message_archive.identity_resolution.enqueue'
              )
              AND {job_alias}.status = 'blocked'
              AND {job_alias}.last_error_code = 'effect_type_not_allowed'
              AND {job_alias}.execution_mode = 'execute'
              AND {job_alias}.attempt_count BETWEEN 1 AND 2
              AND {job_alias}.max_attempts = 5
              AND {job_alias}.worker_generation = 1
              AND {job_alias}.policy_version = '{normalized_policy}'
              AND {job_alias}.side_effect_executed = FALSE
              AND {job_alias}.provider_result_received = FALSE
              AND {job_alias}.provider_call_started_at IS NULL
              AND {job_alias}.reconciliation_required = FALSE
              AND {job_alias}.lease_token = ''
              AND {job_alias}.lease_expires_at IS NULL
              AND {job_alias}.locked_by = ''
              AND {job_alias}.locked_at IS NULL
              AND {job_alias}.hold_reason = ''
              AND {job_alias}.cancel_requested_at IS NULL
              AND {queue_alias}.status = 'held'
              AND {queue_alias}.hold_reason = 'effect_type_not_allowed'
              AND {queue_alias}.last_error = 'effect_type_not_allowed'
              AND (
                  SELECT COUNT(*)
                  FROM external_effect_attempt recovery_attempt_count
                  WHERE recovery_attempt_count.job_id = {job_alias}.id
              ) = {job_alias}.attempt_count
              AND (
                  SELECT COUNT(*)
                  FROM external_effect_attempt recovery_attempt
                  WHERE recovery_attempt.job_id = {job_alias}.id
                    AND recovery_attempt.status = 'blocked'
                    AND recovery_attempt.error_code = 'effect_type_not_allowed'
                    AND recovery_attempt.adapter_name = 'wecom_external_contact_detail'
                    AND recovery_attempt.operation = 'get_external_contact_detail'
                    AND recovery_attempt.adapter_mode = 'disabled'
                    AND recovery_attempt.provider_call_started_at IS NULL
                    AND COALESCE(recovery_attempt.provider_result_json, '{{}}'::jsonb) = '{{}}'::jsonb
              ) = {job_alias}.attempt_count
              AND NOT EXISTS (
                  SELECT 1
                  FROM external_effect_attempt unsafe_recovery_attempt
                  WHERE unsafe_recovery_attempt.job_id = {job_alias}.id
                    AND (
                        unsafe_recovery_attempt.provider_call_started_at IS NOT NULL
                        OR unsafe_recovery_attempt.status <> 'blocked'
                        OR unsafe_recovery_attempt.error_code <> 'effect_type_not_allowed'
                        OR unsafe_recovery_attempt.adapter_mode <> 'disabled'
                        OR unsafe_recovery_attempt.worker_generation NOT IN (0, 1)
                        OR COALESCE(unsafe_recovery_attempt.provider_result_json, '{{}}'::jsonb) <> '{{}}'::jsonb
                    )
              )
              AND EXISTS (
                  SELECT 1
                  FROM queue_runtime_control recovery_control
                  WHERE recovery_control.singleton = TRUE
                    AND recovery_control.active_generation = 1
                    AND recovery_control.claim_enabled = TRUE
                    AND recovery_control.rollout_mode = 'execute'
                    AND recovery_control.policy_version = '{normalized_policy}'
                    AND recovery_control.external_claim_scope = 'all'
              )
    """


def external_contact_relationship_absent_terminal_sql(*, job_alias: str) -> str:
    """Recognize an exact provider-declared business-negative identity result.

    WeCom 84061 means the external-contact relationship does not exist. It is
    terminal and must never be replayed, but it is not an infrastructure or
    queue failure. Every earlier attempt, if present, must be the reviewed
    pre-provider typed-effect gate history and exactly one final attempt must
    contain the real provider response.
    """

    error_codes = ", ".join(f"'{item}'" for item in EXTERNAL_CONTACT_RELATIONSHIP_ABSENT_ERROR_CODES)
    return f"""
              {job_alias}.effect_type = 'wecom.external_contact.detail.fetch'
              AND {job_alias}.adapter_name = 'wecom_external_contact_detail'
              AND {job_alias}.operation = 'get_external_contact_detail'
              AND {job_alias}.target_type = 'external_user'
              AND {job_alias}.business_type = 'identity_resolution_queue'
              AND {job_alias}.source_module IN (
                  'aicrm_next.identity_contact.resolution_effects',
                  'aicrm_next.crm.identity_contact.resolution_effects'
              )
              AND {job_alias}.source_route IN (
                  'channel_entry.identity_resolution.enqueue',
                  'message_archive.identity_resolution.enqueue'
              )
              AND {job_alias}.status = 'failed_terminal'
              AND {job_alias}.last_error_code IN ({error_codes})
              AND {job_alias}.execution_mode = 'execute'
              AND {job_alias}.attempt_count BETWEEN 1 AND 3
              AND {job_alias}.max_attempts = 5
              AND {job_alias}.worker_generation = 1
              AND {job_alias}.policy_version = '{POST_CUTOVER_IDENTITY_RECOVERY_POLICY}'
              AND {job_alias}.side_effect_executed = TRUE
              AND {job_alias}.provider_result_received = TRUE
              AND {job_alias}.provider_call_started_at IS NOT NULL
              AND {job_alias}.reconciliation_required = FALSE
              AND {job_alias}.lease_token = ''
              AND {job_alias}.lease_expires_at IS NULL
              AND {job_alias}.locked_by = ''
              AND {job_alias}.locked_at IS NULL
              AND {job_alias}.hold_reason = ''
              AND {job_alias}.cancel_requested_at IS NULL
              AND (
                  SELECT COUNT(*)
                  FROM external_effect_attempt relationship_absent_all_attempts
                  WHERE relationship_absent_all_attempts.job_id = {job_alias}.id
              ) = {job_alias}.attempt_count
              AND (
                  SELECT COUNT(*)
                  FROM external_effect_attempt relationship_absent_gate_attempt
                  WHERE relationship_absent_gate_attempt.job_id = {job_alias}.id
                    AND relationship_absent_gate_attempt.status = 'blocked'
                    AND relationship_absent_gate_attempt.error_code = 'effect_type_not_allowed'
                    AND relationship_absent_gate_attempt.adapter_name = 'wecom_external_contact_detail'
                    AND relationship_absent_gate_attempt.operation = 'get_external_contact_detail'
                    AND relationship_absent_gate_attempt.adapter_mode = 'disabled'
                    AND relationship_absent_gate_attempt.provider_call_started_at IS NULL
                    AND relationship_absent_gate_attempt.worker_generation IN (0, 1)
                    AND COALESCE(relationship_absent_gate_attempt.provider_result_json, '{{}}'::jsonb) = '{{}}'::jsonb
              ) = {job_alias}.attempt_count - 1
              AND 1 = (
                  SELECT COUNT(*)
                  FROM external_effect_attempt relationship_absent_provider_attempt
                  WHERE relationship_absent_provider_attempt.job_id = {job_alias}.id
                    AND relationship_absent_provider_attempt.status = 'failed_terminal'
                    AND relationship_absent_provider_attempt.error_code IN ({error_codes})
                    AND relationship_absent_provider_attempt.adapter_name = 'wecom_external_contact_detail'
                    AND relationship_absent_provider_attempt.operation = 'get_external_contact_detail'
                    AND relationship_absent_provider_attempt.adapter_mode = 'execute'
                    AND relationship_absent_provider_attempt.provider_call_started_at IS NOT NULL
                    AND relationship_absent_provider_attempt.worker_generation = 1
                    AND COALESCE((relationship_absent_provider_attempt.response_summary_json->>'errcode')::INTEGER, 0) = 84061
                    AND COALESCE((relationship_absent_provider_attempt.response_summary_json->>'real_external_call_executed')::BOOLEAN, FALSE)
              )
    """


def private_message_contact_relationship_absent_terminal_sql(*, job_alias: str) -> str:
    """Recognize an exact provider-declared private-message business rejection.

    WeCom 84061 means the target is no longer an external contact.  The send
    flow completed at the provider boundary, but delivery was rejected for a
    deterministic business reason.  Only a single, fully settled production
    attempt with the raw 84061 response is eligible; any missing or
    contradictory evidence remains a health failure.
    """

    error_codes = ", ".join(f"'{item}'" for item in EXTERNAL_CONTACT_RELATIONSHIP_ABSENT_ERROR_CODES)
    standard_broadcast_lineage = f"""
              {job_alias}.business_type = 'broadcast_job'
              AND {job_alias}.source_module = 'background_jobs.broadcast_effect_delegate'
              AND {job_alias}.source_route = 'broadcast_effect_delegate'
              AND NOT EXISTS (
                  SELECT 1
                  FROM external_effect_job private_contact_absence_success
                  WHERE private_contact_absence_success.id <> {job_alias}.id
                    AND private_contact_absence_success.status = 'succeeded'
                    AND private_contact_absence_success.effect_type = {job_alias}.effect_type
                    AND COALESCE({job_alias}.business_id, '') <> ''
                    AND private_contact_absence_success.business_type = {job_alias}.business_type
                    AND private_contact_absence_success.business_id = {job_alias}.business_id
              )
    """
    operator_compensation_lineage = f"""
              {job_alias}.business_type = 'cloud_plan_miniprogram_only_compensation'
              AND {job_alias}.source_module = 'cloud_orchestrator_compensation'
              AND {job_alias}.source_route = 'production_manual_compensation'
              AND {job_alias}.actor_id = 'codex_production_operator'
              AND {job_alias}.actor_type = 'operator'
              AND {job_alias}.risk_level = 'high'
              AND {job_alias}.requires_approval IS FALSE
              AND {job_alias}.approved_at IS NULL
              AND COALESCE({job_alias}.business_id, '') <> ''
              AND COALESCE({job_alias}.target_id, '') <> ''
              AND COALESCE({job_alias}.execution_id, '') <> ''
              AND COALESCE({job_alias}.source_event_id, '') <> ''
              AND COALESCE({job_alias}.source_command_id, '') LIKE 'hxc_monday_abcd_%'
              AND {job_alias}.request_id = {job_alias}.source_command_id
              AND {job_alias}.trace_id LIKE {job_alias}.request_id || ':%'
              AND {job_alias}.idempotency_key LIKE {job_alias}.request_id || ':%'
    """
    return f"""
              {job_alias}.effect_type = 'wecom.message.private.send'
              AND {job_alias}.adapter_name = 'wecom_private_message'
              AND {job_alias}.operation = 'send_private_message'
              AND {job_alias}.target_type = 'external_contact'
              AND (
                  ({standard_broadcast_lineage})
                  OR ({operator_compensation_lineage})
              )
              AND {job_alias}.status = 'failed_terminal'
              AND {job_alias}.last_error_code IN ({error_codes})
              AND {job_alias}.execution_mode = 'execute'
              AND {job_alias}.lane = 'wecom_bulk'
              AND {job_alias}.attempt_count = 1
              AND {job_alias}.max_attempts = 5
              AND {job_alias}.worker_generation = 1
              AND {job_alias}.policy_version = '{POST_CUTOVER_IDENTITY_RECOVERY_POLICY}'
              AND {job_alias}.side_effect_executed = TRUE
              AND {job_alias}.provider_result_received = TRUE
              AND {job_alias}.provider_call_started_at IS NOT NULL
              AND {job_alias}.completed_at IS NOT NULL
              AND {job_alias}.reconciliation_required = FALSE
              AND {job_alias}.lease_token = ''
              AND {job_alias}.lease_expires_at IS NULL
              AND {job_alias}.locked_by = ''
              AND {job_alias}.locked_at IS NULL
              AND {job_alias}.hold_reason = ''
              AND {job_alias}.cancel_requested_at IS NULL
              AND 1 = (
                  SELECT COUNT(*)
                  FROM external_effect_attempt private_contact_absence_all_attempts
                  WHERE private_contact_absence_all_attempts.job_id = {job_alias}.id
              )
              AND 1 = (
                  SELECT COUNT(*)
                  FROM external_effect_attempt private_contact_absence_provider_attempt
                  WHERE private_contact_absence_provider_attempt.job_id = {job_alias}.id
                    AND private_contact_absence_provider_attempt.attempt_id =
                        COALESCE({job_alias}.last_attempt_id, '')
                    AND private_contact_absence_provider_attempt.status = 'failed_terminal'
                    AND private_contact_absence_provider_attempt.error_code IN ({error_codes})
                    AND private_contact_absence_provider_attempt.adapter_name = 'wecom_private_message'
                    AND private_contact_absence_provider_attempt.operation = 'send_private_message'
                    AND private_contact_absence_provider_attempt.adapter_mode = 'execute'
                    AND private_contact_absence_provider_attempt.provider_call_started_at IS NOT NULL
                    AND private_contact_absence_provider_attempt.completed_at IS NOT NULL
                    AND private_contact_absence_provider_attempt.worker_generation = 1
                    AND COALESCE(
                        (private_contact_absence_provider_attempt.response_summary_json->>'errcode')::INTEGER,
                        0
                    ) = 84061
                    AND COALESCE(
                        (
                            private_contact_absence_provider_attempt.response_summary_json
                                ->>'real_external_call_executed'
                        )::BOOLEAN,
                        FALSE
                    )
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM external_effect_job private_contact_absence_later_success
                  WHERE private_contact_absence_later_success.id <> {job_alias}.id
                    AND private_contact_absence_later_success.status = 'succeeded'
                    AND private_contact_absence_later_success.effect_type = {job_alias}.effect_type
                    AND private_contact_absence_later_success.target_type = {job_alias}.target_type
                    AND private_contact_absence_later_success.target_id = {job_alias}.target_id
                    AND private_contact_absence_later_success.updated_at > {job_alias}.updated_at
              )
    """
