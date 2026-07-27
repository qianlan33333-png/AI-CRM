from __future__ import annotations

from dataclasses import dataclass

from .context import PrincipalType


@dataclass(frozen=True)
class ApiClientProfile:
    purpose: str
    client_id: str
    principal_id: str
    principal_type: PrincipalType
    display_name: str
    audiences: tuple[str, ...]
    scopes: tuple[str, ...]
    capabilities: tuple[str, ...]
    client_id_setting: str
    client_secret_reference_setting: str
    allowed_cidrs: tuple[str, ...] = ()
    token_ttl_seconds: int = 1800


@dataclass(frozen=True)
class WebhookClientProfile:
    purpose: str
    client_id: str
    principal_id: str
    display_name: str
    capabilities: tuple[str, ...]
    secret_store_key: str
    allowed_cidrs: tuple[str, ...] = ()


def _api_profile(
    purpose: str,
    *,
    audiences: tuple[str, ...],
    scopes: tuple[str, ...],
    capabilities: tuple[str, ...],
    principal_type: PrincipalType = PrincipalType.SERVICE,
    setting_stem: str = "",
) -> ApiClientProfile:
    actual_setting_stem = setting_stem or purpose.upper()
    return ApiClientProfile(
        purpose=purpose,
        client_id=f"aicrm-{purpose.replace('_', '-')}",
        principal_id=f"{principal_type.value}:{purpose}",
        principal_type=principal_type,
        display_name=purpose.replace("_", " ").title(),
        audiences=audiences,
        scopes=scopes,
        capabilities=capabilities,
        client_id_setting=f"AICRM_AUTH_{actual_setting_stem}_CLIENT_ID",
        client_secret_reference_setting=f"AICRM_AUTH_{actual_setting_stem}_CLIENT_SECRET_REF",
    )


API_CLIENT_PROFILES = (
    _api_profile(
        "automation_worker",
        audiences=("internal_worker",),
        scopes=("read", "write"),
        capabilities=(
            "cloud_run_due_execute",
            "external_effect_execute",
            "internal_event_execute",
            "internal_execute",
            "jobs_execute",
            "push_queue_execute",
            "runtime_route_read",
            "webhook_inbox_execute",
        ),
    ),
    _api_profile(
        "archive",
        audiences=("internal_worker",),
        scopes=("read", "write"),
        capabilities=("archive_execute", "archive_read"),
        setting_stem="ARCHIVE_WORKER",
    ),
    _api_profile(
        "callback",
        audiences=("internal_worker",),
        scopes=("read", "write"),
        capabilities=("callback_execute",),
        setting_stem="CALLBACK_WORKER",
    ),
    _api_profile(
        "group_broadcast",
        audiences=("external_integration",),
        scopes=("write",),
        capabilities=("group_broadcast_execute",),
    ),
    _api_profile(
        "identity",
        audiences=("external_integration",),
        scopes=("read",),
        capabilities=("identity_resolve",),
        principal_type=PrincipalType.API_CLIENT,
    ),
    _api_profile(
        "mcp",
        audiences=("external_integration",),
        scopes=("read", "write"),
        capabilities=("mcp_execute", "mcp_read"),
        principal_type=PrincipalType.API_CLIENT,
    ),
    _api_profile(
        "external_agent",
        audiences=("external_integration",),
        scopes=("read", "write"),
        capabilities=("external_read", "external_write"),
        principal_type=PrincipalType.API_CLIENT,
    ),
    _api_profile(
        "campaign_agent",
        audiences=("external_integration",),
        scopes=("read", "write"),
        capabilities=(
            "campaign_draft_create",
            "campaign_status_read",
            "customer_read_limited",
            "customer_resolve_read",
            "material_create",
            "material_read",
        ),
        principal_type=PrincipalType.API_CLIENT,
    ),
    _api_profile(
        "ops_reporter",
        audiences=("external_integration",),
        scopes=("write",),
        capabilities=("operation_cycle_report_write",),
        principal_type=PrincipalType.API_CLIENT,
    ),
)


WEBHOOK_CLIENT_PROFILES = (
    WebhookClientProfile(
        purpose="outbound_webhook",
        client_id="aicrm-webhook-external-effect",
        principal_id="api_client:outbound_webhook",
        display_name="External Effect Webhook",
        capabilities=("external_effect_receipt_receive",),
        secret_store_key="AICRM_AUTH_OUTBOUND_WEBHOOK_SECRET",
    ),
    WebhookClientProfile(
        purpose="group_ops_webhook",
        client_id="aicrm-webhook-group-ops",
        principal_id="api_client:group_ops_webhook",
        display_name="Group Ops Webhook",
        capabilities=("group_ops_webhook_receive",),
        secret_store_key="AICRM_AUTH_GROUP_OPS_WEBHOOK_SECRET",
    ),
    WebhookClientProfile(
        purpose="automation_agent_webhook",
        client_id="aicrm-webhook-automation-agent",
        principal_id="api_client:automation_agent_webhook",
        display_name="Automation Agent Webhook",
        capabilities=("automation_agent_webhook_receive",),
        secret_store_key="AICRM_AUTH_AUTOMATION_AGENT_WEBHOOK_SECRET",
    ),
    WebhookClientProfile(
        purpose="ai_audience_webhook",
        client_id="aicrm-webhook-ai-audience",
        principal_id="api_client:ai_audience_webhook",
        display_name="AI Audience Webhook",
        capabilities=("ai_audience_webhook_receive",),
        secret_store_key="AICRM_AUTH_AI_AUDIENCE_WEBHOOK_SECRET",
    ),
    WebhookClientProfile(
        purpose="activation_webhook",
        client_id="aicrm-webhook-activation",
        principal_id="api_client:activation_webhook",
        display_name="Activation Webhook",
        capabilities=("activation_webhook_receive",),
        secret_store_key="AICRM_AUTH_ACTIVATION_WEBHOOK_SECRET",
    ),
)


API_CLIENT_PROFILE_BY_PURPOSE = {profile.purpose: profile for profile in API_CLIENT_PROFILES}
WEBHOOK_CLIENT_PROFILE_BY_PURPOSE = {profile.purpose: profile for profile in WEBHOOK_CLIENT_PROFILES}


__all__ = [
    "API_CLIENT_PROFILES",
    "API_CLIENT_PROFILE_BY_PURPOSE",
    "WEBHOOK_CLIENT_PROFILES",
    "WEBHOOK_CLIENT_PROFILE_BY_PURPOSE",
    "ApiClientProfile",
    "WebhookClientProfile",
]
