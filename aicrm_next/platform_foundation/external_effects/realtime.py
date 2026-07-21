from __future__ import annotations

from typing import Any

from .models import WECOM_CONTACT_TAG_MARK, WECOM_PROFILE_UPDATE, WECOM_WELCOME_MESSAGE_SEND

REALTIME_ENABLED_KEY = "AICRM_EXTERNAL_EFFECT_REALTIME_ENABLED"
REALTIME_ALLOWED_TYPES_KEY = "AICRM_EXTERNAL_EFFECT_REALTIME_ALLOWED_TYPES"
REALTIME_MAX_CONCURRENCY_KEY = "AICRM_EXTERNAL_EFFECT_REALTIME_MAX_CONCURRENCY"
WELCOME_MESSAGE_CAPABILITY_ENABLED_KEY = "AICRM_PUSH_CAPABILITY_WELCOME_MESSAGE_ENABLED"
CHANNEL_ENTRY_REALTIME_EFFECT_TYPES = (
    WECOM_WELCOME_MESSAGE_SEND,
    WECOM_CONTACT_TAG_MARK,
    WECOM_PROFILE_UPDATE,
)
def _text(value: Any) -> str:
    return str(value or "").strip()


def realtime_wakeup_allowed(effect_type: str) -> bool:
    """Return whether the compatibility signal belongs to channel entry.

    Provider execution policy is intentionally absent here.  The queue row was
    already committed with a PostgreSQL wakeup trigger; the generation/lane
    policy and provider adapter are the only owners allowed to decide whether
    it can be claimed and called.
    """

    return _text(effect_type) in CHANNEL_ENTRY_REALTIME_EFFECT_TYPES


def realtime_wakeup_state() -> dict[str, Any]:
    allowed_types = sorted(CHANNEL_ENTRY_REALTIME_EFFECT_TYPES)
    channel_entry_required = list(CHANNEL_ENTRY_REALTIME_EFFECT_TYPES)
    return {
        "enabled": True,
        "status": "durable_signal_only",
        "enabled_source": "postgres_queue_trigger",
        "allowed_types": allowed_types,
        "allowed_types_source": "channel_entry_queue_contract",
        "derived_from_welcome_message_capability": False,
        "max_concurrency": 0,
        "channel_entry_required_types": channel_entry_required,
        "channel_entry_missing_types": [],
        "channel_entry_ready": True,
        "dispatch_boundary": "postgres_execution_runtime_claim_one",
        "uses_process_local_executor": False,
        "provider_dispatch_allowed": False,
        "signal_transport": "transactional_queue_trigger",
        "deprecated_settings": [REALTIME_ENABLED_KEY, REALTIME_ALLOWED_TYPES_KEY, REALTIME_MAX_CONCURRENCY_KEY],
        "deprecated_settings_owner": "integration_gateway",
        "deprecated_settings_delete_after": "queue_runtime_pr5_cleanup",
        "description": "兼容入口只确认 durable effect 已入队；provider 只能由 PostgreSQL queue runtime 的真实空闲 slot 执行。",
    }


def wake_external_effect_job(
    job_id: Any,
    *,
    reason: str,
    effect_type: str,
    repository: Any | None = None,
    adapter_registry: Any | None = None,
    run_inline: bool = True,
) -> bool:
    """Compatibility acknowledgement for a transactionally signalled job.

    ``repository``, ``adapter_registry`` and ``run_inline`` remain accepted so
    older callers don't break during the migration.  They are deliberately
    unused: this function has no claim or provider-dispatch authority.
    """

    del reason, repository, adapter_registry, run_inline
    try:
        normalized_job_id = int(job_id or 0)
    except (TypeError, ValueError):
        normalized_job_id = 0
    normalized_effect_type = _text(effect_type)
    return normalized_job_id > 0 and realtime_wakeup_allowed(normalized_effect_type)
