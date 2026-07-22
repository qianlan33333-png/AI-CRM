from __future__ import annotations


PRE_PROVIDER_IDENTITY_ADOPTION_SOURCE_POLICY = "queue-v2-test-loopback"
PRE_PROVIDER_IDENTITY_ADOPTION_PREDICATE_VERSION = "identity_contact_detail_test_policy_v2"


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
              AND {job_alias}.source_module = 'aicrm_next.identity_contact.resolution_effects'
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
