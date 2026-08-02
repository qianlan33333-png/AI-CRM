from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Literal


CapabilityTier = Literal["core", "extension"]

REGISTRY_SCHEMA_VERSION = 2
CORE_VERSION = "0.1.0"


@dataclass(frozen=True)
class CapabilitySpec:
    """Static ownership contract for one logical AI-CRM capability.

    A capability may temporarily span several legacy top-level contexts while
    the modular-monolith migration is in progress.  Runtime composition reads
    this registry; business modules must not discover capabilities through
    dynamic imports.
    """

    capability_id: str
    logical_domain: str
    tier: CapabilityTier
    version: str
    core_compat: str
    enabled_by_default: bool
    dependencies: tuple[str, ...] = ()
    package_root: str = ""
    current_contexts: tuple[str, ...] = ()
    route_groups: tuple[str, ...] = ()
    config_sections: tuple[str, ...] = ()
    table_domains: tuple[str, ...] = ()
    event_prefixes: tuple[str, ...] = ()
    effect_types: tuple[str, ...] = ()
    job_types: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    health_checks: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _spec(
    capability_id: str,
    logical_domain: str,
    *,
    tier: CapabilityTier = "core",
    enabled_by_default: bool = True,
    dependencies: tuple[str, ...] = (),
    package_root: str = "",
    current_contexts: tuple[str, ...] = (),
    route_groups: tuple[str, ...] = (),
    config_sections: tuple[str, ...] = (),
    table_domains: tuple[str, ...] = (),
    event_prefixes: tuple[str, ...] = (),
    effect_types: tuple[str, ...] = (),
    job_types: tuple[str, ...] = (),
    permissions: tuple[str, ...] = (),
    health_checks: tuple[str, ...] = (),
) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        logical_domain=logical_domain,
        tier=tier,
        version="1.0.0",
        core_compat=">=0.1,<1.0",
        enabled_by_default=enabled_by_default,
        dependencies=dependencies,
        package_root=package_root or f"aicrm_next.{logical_domain}",
        current_contexts=current_contexts,
        route_groups=route_groups,
        config_sections=config_sections,
        table_domains=table_domains,
        event_prefixes=event_prefixes,
        effect_types=effect_types,
        job_types=job_types,
        permissions=permissions,
        health_checks=health_checks,
    )


CAPABILITY_SPECS: tuple[CapabilitySpec, ...] = (
    _spec(
        "core.platform",
        "platform",
        current_contexts=(
            "admin_auth",
            "admin_config",
            "admin_jobs",
            "external_push",
            "navigation_target",
            "platform_foundation",
            "shared",
        ),
        route_groups=(
            "platform",
            "auth_platform",
            "external_effects",
            "execution_runtime",
            "internal_events",
            "push_center",
            "webhook_inbox",
            "admin_auth",
            "admin_config",
            "admin_config_api_key",
            "admin_jobs",
        ),
        config_sections=("admin_auth", "infrastructure", "reliability", "webhooks"),
        table_domains=(
            "admin_config",
            "admin_identity",
            "admin_jobs",
            "auth_platform",
            "external",
            "external_effects",
            "internal_events",
            "legacy_auth",
            "legacy_callback",
            "legacy_routing",
            "legacy_schema_audit",
            "legacy_schema_backup",
            "legacy_webhook_cleanup",
            "outbound_webhook",
            "platform_foundation",
            "schema_migration",
            "webhook_ingress",
        ),
        event_prefixes=("external_effect.", "queue.runtime."),
        effect_types=("feishu.webhook.notify", "openclaw.context.push", "webhook.generic.push"),
        job_types=(
            "webhook_inbox.dispatch",
            "internal_event.dispatch",
            "external_effect.dispatch",
            "external_effect.reconcile",
        ),
        permissions=(
            "admin.config.read",
            "admin.config.publish",
            "admin.config.rollback",
            "admin.pii.export",
            "runtime.jobs.manage",
        ),
        health_checks=("database", "secret_store", "queue_runtime", "config_release"),
    ),
    _spec(
        "core.channels",
        "channels",
        dependencies=("core.platform",),
        current_contexts=("auth_wecom", "channel_entry", "integration_gateway"),
        route_groups=("channel_entry", "auth_wecom", "mcp", "sidebar_jssdk", "verification_files"),
        config_sections=("wecom_base", "wecom_callback"),
        table_domains=("channel_entry",),
        event_prefixes=("channel_entry.",),
        health_checks=("wecom_credentials", "wecom_callback"),
    ),
    _spec(
        "core.crm",
        "crm",
        dependencies=("core.platform", "core.channels"),
        current_contexts=(
            "customer_read_model",
            "customer_tags",
            "identity_contact",
            "operation_members",
            "owner_migration",
            "sidebar_write",
        ),
        route_groups=(
            "common_operation_members",
            "customer_read_model",
            "customer_admin_pages",
            "customer_tags",
            "customer_tags_read",
            "customer_tags_write",
            "customer_tags_admin_pages",
            "identity",
            "identity_admin_pages",
            "sidebar_write",
            "owner_migration",
        ),
        table_domains=(
            "contacts",
            "customer_read_model",
            "customer_tags",
            "identity_contact",
            "legacy_customer_projection",
            "legacy_customer_tags",
            "legacy_identity",
            "owner_migration",
        ),
        event_prefixes=("customer.", "customer_read_model.", "identity.", "owner_migration."),
        effect_types=(
            "wecom.contact.tag.mark",
            "wecom.contact.tag.unmark",
            "wecom.external_contact.detail.fetch",
            "wecom.profile.update",
        ),
        permissions=("crm.customer.read", "crm.customer.write", "crm.customer.transfer", "crm.tags.manage"),
        health_checks=("customer_projection", "identity_resolution"),
    ),
    _spec(
        "core.engagement",
        "engagement",
        dependencies=("core.platform", "core.channels", "core.crm"),
        current_contexts=("media_library", "send_content", "send_targets"),
        route_groups=(
            "automation_channels",
            "channel_admin_pages",
            "group_ops_admin_pages",
            "media_library",
            "media_library_admin_pages",
            "send_content",
        ),
        table_domains=("media_library", "messaging"),
        effect_types=("media.storage.upload", "wecom.media.upload", "wecom.welcome_message.send"),
        permissions=("engagement.content.manage", "engagement.channel.manage", "engagement.groups.manage"),
        health_checks=("media_storage", "welcome_delivery"),
    ),
    _spec(
        "core.automation",
        "automation",
        dependencies=("core.platform", "core.channels", "core.crm", "core.engagement"),
        current_contexts=("automation_engine", "background_jobs", "ops_enrollment"),
        route_groups=("automation", "user_ops", "user_ops_admin_pages"),
        table_domains=(
            "automation",
            "automation_engine",
            "automation_engine_group_ops",
            "automation_group_ops",
            "automation_legacy",
            "broadcast",
            "broadcast_jobs",
            "campaign",
            "group_ops",
            "legacy_automation",
            "legacy_automation_profile",
            "legacy_user_ops",
            "user_ops",
        ),
        event_prefixes=("broadcast_task.", "ops_plan."),
        effect_types=(
            "group_ops.message.loopback",
            "group_ops.webhook.action.loopback",
            "webhook.customer_automation.retry",
            "webhook.customer_automation.retry_due",
            "wecom.message.broadcast.send",
            "wecom.message.group.send",
            "wecom.message.private.send",
        ),
        job_types=("campaign.plan", "campaign.dispatch", "group_ops.plan"),
        permissions=("automation.rule.manage", "automation.campaign.approve", "automation.campaign.send"),
        health_checks=("automation_queue", "broadcast_queue"),
    ),
    _spec(
        "core.insights",
        "insights",
        dependencies=("core.platform", "core.channels", "core.crm", "core.engagement", "core.automation"),
        current_contexts=("admin_read_model", "data_health", "delivery_lineage"),
        route_groups=("data_health", "delivery_lineage"),
        table_domains=("data_health",),
        health_checks=("read_models", "data_health"),
    ),
    _spec(
        "core.app",
        "app",
        dependencies=(
            "core.platform",
            "core.channels",
            "core.crm",
            "core.engagement",
            "core.automation",
            "core.insights",
        ),
        current_contexts=("admin_console",),
        route_groups=("admin_shell",),
        permissions=("admin.shell.access",),
        health_checks=("router_registry", "capability_registry"),
    ),
    _spec(
        "extension.ai",
        "extensions",
        tier="extension",
        enabled_by_default=False,
        dependencies=("core.platform", "core.crm", "core.automation"),
        package_root="aicrm_next.extensions.ai",
        current_contexts=("ai_assist", "ai_audience_ops", "automation_agents"),
        route_groups=(
            "ai_assist",
            "ai_audience_admin_pages",
            "ai_audience_admin_api",
            "ai_audience_external_api",
            "ai_audience_ops",
            "automation_agents_admin_pages",
            "automation_agents_admin_api",
            "automation_agents_api",
        ),
        config_sections=("ai_services",),
        table_domains=("ai_audience_ops", "automation_agents", "legacy_automation_agent"),
        event_prefixes=("ai_audience.", "ai_campaign.", "automation_agent."),
        effect_types=("ai.agent.generate", "ai_assist.campaign.message.loopback", "ai_assist.campaign.message.plan"),
        job_types=("ai_audience.refresh", "ai_agent.plan"),
        permissions=("ai.audience.manage", "ai.agent.manage"),
        health_checks=("ai_provider",),
    ),
    _spec(
        "extension.commerce",
        "extensions",
        tier="extension",
        enabled_by_default=False,
        dependencies=("core.platform", "core.channels", "core.crm"),
        package_root="aicrm_next.extensions.commerce",
        current_contexts=("commerce", "public_product", "service_period"),
        route_groups=(
            "commerce",
            "coupons_admin_pages",
            "coupons_admin_api",
            "coupons_public",
            "coupons_sidebar",
            "public_product",
            "service_period",
        ),
        config_sections=("wechat_pay_h5", "alipay_pay_wap"),
        table_domains=("commerce", "commerce_events", "legacy_commerce", "legacy_conversion", "service_period"),
        event_prefixes=("commerce.", "payment.", "payment_succeeded", "transaction.", "refund."),
        effect_types=(
            "payment.alipay.order.query",
            "payment.alipay.refund.query",
            "payment.wechat.order.query",
            "payment.wechat.refund.query",
            "payment.wechat.refund.request",
            "webhook.order_paid.push",
        ),
        job_types=("payment.reconcile", "service_period.project"),
        permissions=("commerce.manage", "commerce.refund.approve"),
        health_checks=("payment_provider", "entitlement_projection"),
    ),
    _spec(
        "extension.forms",
        "extensions",
        tier="extension",
        enabled_by_default=False,
        dependencies=("core.platform", "core.channels", "core.crm", "core.automation"),
        package_root="aicrm_next.extensions.forms",
        current_contexts=("questionnaire",),
        route_groups=("questionnaire", "questionnaire_admin_pages"),
        config_sections=("wechat_mp",),
        table_domains=("legacy_questionnaire", "questionnaire"),
        event_prefixes=("external_form.", "questionnaire."),
        effect_types=("webhook.questionnaire_submission.push",),
        permissions=("forms.manage", "forms.responses.export"),
        health_checks=("questionnaire_projection",),
    ),
    _spec(
        "extension.archive",
        "extensions",
        tier="extension",
        enabled_by_default=False,
        dependencies=("core.platform", "core.channels", "core.crm"),
        package_root="aicrm_next.extensions.archive",
        current_contexts=("message_archive",),
        route_groups=("message_archive",),
        config_sections=("wecom_archive",),
        event_prefixes=("message_archive.",),
        permissions=("archive.read", "archive.sync"),
        health_checks=("archive_sdk",),
    ),
    _spec(
        "extension.radar",
        "extensions",
        tier="extension",
        enabled_by_default=False,
        dependencies=("core.platform", "core.crm", "core.engagement"),
        package_root="aicrm_next.extensions.radar",
        current_contexts=("radar_links",),
        route_groups=("radar_links", "radar_links_admin_pages"),
        table_domains=("radar",),
        event_prefixes=("radar.",),
        permissions=("radar.manage", "radar.analytics.read"),
        health_checks=("radar_projection",),
    ),
    _spec(
        "extension.growth",
        "extensions",
        tier="extension",
        enabled_by_default=False,
        dependencies=("core.platform", "core.crm", "core.automation"),
        package_root="aicrm_next.extensions.growth",
        current_contexts=("cloud_orchestrator",),
        route_groups=("cloud_orchestrator",),
        table_domains=("cloud_orchestrator",),
        permissions=("growth.plan.manage",),
        health_checks=("growth_projection",),
    ),
    _spec(
        "extension.hxc",
        "extensions",
        tier="extension",
        enabled_by_default=False,
        dependencies=("core.platform", "core.crm", "core.automation", "core.insights"),
        package_root="aicrm_next.extensions.hxc",
        current_contexts=("class_user_management", "hxc_dashboard", "operation_cycles"),
        route_groups=(
            "class_user_management",
            "hxc_dashboard",
            "operation_cycles",
            "operation_cycles_admin_pages",
        ),
        table_domains=("operation_cycles",),
        permissions=("hxc.operations.manage",),
        health_checks=("hxc_projection",),
    ),
)


_BY_ID = {spec.capability_id: spec for spec in CAPABILITY_SPECS}
_BY_CONTEXT = {context: spec for spec in CAPABILITY_SPECS for context in spec.current_contexts}
_BY_ROUTE_GROUP = {group: spec for spec in CAPABILITY_SPECS for group in spec.route_groups}
_BY_CONFIG_SECTION = {section: spec for spec in CAPABILITY_SPECS for section in spec.config_sections}
_BY_TABLE_DOMAIN = {domain: spec for spec in CAPABILITY_SPECS for domain in spec.table_domains}
_BY_EFFECT_TYPE = {effect_type: spec for spec in CAPABILITY_SPECS for effect_type in spec.effect_types}
_BY_JOB_TYPE = {job_type: spec for spec in CAPABILITY_SPECS for job_type in spec.job_types}


def get_capability_spec(capability_id: str) -> CapabilitySpec | None:
    return _BY_ID.get(str(capability_id or "").strip())


def capability_for_context(context: str) -> CapabilitySpec | None:
    return _BY_CONTEXT.get(str(context or "").strip())


def capability_for_module(module: str) -> CapabilitySpec | None:
    normalized = str(module or "").strip()
    parts = normalized.split(".")
    if len(parts) < 2 or parts[0] != "aicrm_next":
        return None
    matches = [
        (f"{spec.package_root}.{context}", spec)
        for spec in CAPABILITY_SPECS
        for context in spec.current_contexts
        if normalized == f"{spec.package_root}.{context}"
        or normalized.startswith(f"{spec.package_root}.{context}.")
    ]
    if matches:
        return max(matches, key=lambda item: len(item[0]))[1]
    return capability_for_context(parts[1])


def capability_for_route_group(route_group: str) -> CapabilitySpec | None:
    return _BY_ROUTE_GROUP.get(str(route_group or "").strip())


def capability_for_config_section(section: str) -> CapabilitySpec | None:
    return _BY_CONFIG_SECTION.get(str(section or "").strip())


def capability_for_table_domain(domain: str) -> CapabilitySpec | None:
    return _BY_TABLE_DOMAIN.get(str(domain or "").strip())


def capability_for_event_type(event_type: str) -> CapabilitySpec | None:
    normalized = str(event_type or "").strip()
    matches = [
        (prefix, spec)
        for spec in CAPABILITY_SPECS
        for prefix in spec.event_prefixes
        if normalized.startswith(prefix)
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item[0]))[1]


def capability_for_effect_type(effect_type: str) -> CapabilitySpec | None:
    return _BY_EFFECT_TYPE.get(str(effect_type or "").strip())


def capability_for_job_type(job_type: str) -> CapabilitySpec | None:
    return _BY_JOB_TYPE.get(str(job_type or "").strip())


def default_capability_ids() -> tuple[str, ...]:
    return tuple(spec.capability_id for spec in CAPABILITY_SPECS if spec.enabled_by_default)


def registry_summary(specs: Iterable[CapabilitySpec] = CAPABILITY_SPECS) -> dict[str, object]:
    rows = tuple(specs)
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "core_version": CORE_VERSION,
        "capability_count": len(rows),
        "core_count": sum(1 for spec in rows if spec.tier == "core"),
        "extension_count": sum(1 for spec in rows if spec.tier == "extension"),
        "logical_domains": sorted({spec.logical_domain for spec in rows}),
        "capabilities": [spec.to_dict() for spec in rows],
    }


def validate_capability_registry(specs: Iterable[CapabilitySpec] = CAPABILITY_SPECS) -> list[str]:
    rows = tuple(specs)
    errors: list[str] = []
    ids = [spec.capability_id for spec in rows]
    if len(ids) != len(set(ids)):
        errors.append("capability ids must be unique")
    known = set(ids)
    for spec in rows:
        if not spec.capability_id or not spec.logical_domain:
            errors.append("capability id and logical domain are required")
        if not spec.package_root.startswith("aicrm_next."):
            errors.append(f"{spec.capability_id}: package_root must be inside aicrm_next")
        if spec.tier not in {"core", "extension"}:
            errors.append(f"{spec.capability_id}: invalid tier {spec.tier}")
        if spec.capability_id in spec.dependencies:
            errors.append(f"{spec.capability_id}: self dependency is forbidden")
        for dependency in spec.dependencies:
            if dependency not in known:
                errors.append(f"{spec.capability_id}: unknown dependency {dependency}")
    for label, values in (
        ("context", [item for spec in rows for item in spec.current_contexts]),
        ("route group", [item for spec in rows for item in spec.route_groups]),
        ("config section", [item for spec in rows for item in spec.config_sections]),
        ("table domain", [item for spec in rows for item in spec.table_domains]),
        ("effect type", [item for spec in rows for item in spec.effect_types]),
        ("job type", [item for spec in rows for item in spec.job_types]),
    ):
        duplicates = sorted({item for item in values if values.count(item) > 1})
        if duplicates:
            errors.append(f"duplicate {label} ownership: {', '.join(duplicates)}")
    errors.extend(_dependency_cycle_errors(rows))
    return errors


def _dependency_cycle_errors(specs: tuple[CapabilitySpec, ...]) -> list[str]:
    graph = {spec.capability_id: set(spec.dependencies) for spec in specs}
    visiting: set[str] = set()
    visited: set[str] = set()
    errors: list[str] = []

    def visit(node: str, path: tuple[str, ...]) -> None:
        if node in visited:
            return
        if node in visiting:
            cycle = path[path.index(node) :] + (node,)
            errors.append("capability dependency cycle: " + " -> ".join(cycle))
            return
        visiting.add(node)
        for dependency in sorted(graph.get(node, ())):
            visit(dependency, path + (dependency,))
        visiting.remove(node)
        visited.add(node)

    for capability_id in sorted(graph):
        visit(capability_id, (capability_id,))
    return errors


__all__ = [
    "CAPABILITY_SPECS",
    "CORE_VERSION",
    "REGISTRY_SCHEMA_VERSION",
    "CapabilitySpec",
    "capability_for_config_section",
    "capability_for_context",
    "capability_for_effect_type",
    "capability_for_event_type",
    "capability_for_job_type",
    "capability_for_module",
    "capability_for_route_group",
    "capability_for_table_domain",
    "default_capability_ids",
    "get_capability_spec",
    "registry_summary",
    "validate_capability_registry",
]
