from __future__ import annotations

from typing import Any

from aicrm_next.runtime_configuration import RUNTIME_CONFIG_CUTOVER_KEYS_KEY


def _definition(
    key: str,
    label: str,
    *,
    section: str,
    value_type: str = "string",
    description: str = "",
    default: str = "",
    minimum: int | None = None,
    maximum: int | None = None,
    options: tuple[str, ...] = (),
    deprecated_after: str = "",
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "key": key,
        "label": label,
        "section": section,
        "mode": "editable",
        "input_type": "number" if value_type == "integer" else "text",
        "type": value_type,
        "description": description,
        "default": default,
    }
    if minimum is not None:
        item["min"] = minimum
    if maximum is not None:
        item["max"] = maximum
    if options:
        item["options"] = list(options)
    if deprecated_after:
        item["deprecated_after"] = deprecated_after
    return item


_INTERNAL_EVENT_BOOLEAN_KEYS = {
    "AICRM_INTERNAL_EVENTS_ENABLED": "内部事件总开关",
    "AICRM_INTERNAL_EVENTS_PAYMENT_ENABLED": "支付事件投影",
    "AICRM_INTERNAL_EVENTS_QUESTIONNAIRE_ENABLED": "问卷事件投影",
    "AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED": "客户标签事件投影",
    "AICRM_INTERNAL_EVENTS_CUSTOMER_IDENTITY_ENABLED": "客户身份事件投影",
    "AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED": "AI 活动事件投影",
    "AICRM_INTERNAL_EVENTS_OPS_PLAN_ENABLED": "运营计划事件投影",
    "AICRM_INTERNAL_EVENTS_BROADCAST_TASK_ENABLED": "群发任务事件投影",
    "AICRM_INTERNAL_EVENTS_OWNER_MIGRATION_ENABLED": "负责人迁移事件投影",
    "AICRM_INTERNAL_EVENTS_LEGACY_PATH_MARKERS_ENABLED": "Legacy 路径观测标记",
    "AICRM_INTERNAL_EVENTS_SHADOW_ONLY": "内部事件仅影子执行",
    "AICRM_INTERNAL_EVENTS_AUTO_EXECUTE": "内部事件自动执行",
}

_PUSH_CAPABILITY_OWNERS = {
    "AICRM_PUSH_CAPABILITY_AI_ASSIST_PUSH_ENABLED": "extension.ai",
    "AICRM_PUSH_CAPABILITY_CUSTOMER_WEBHOOK_ENABLED": "core.automation",
    "AICRM_PUSH_CAPABILITY_GROUP_BROADCAST_ENABLED": "core.automation",
    "AICRM_PUSH_CAPABILITY_GROUP_OPS_PUSH_ENABLED": "core.automation",
    "AICRM_PUSH_CAPABILITY_INTEGRATIONS_ENABLED": "core.platform",
    "AICRM_PUSH_CAPABILITY_ORDER_PAID_PUSH_ENABLED": "extension.commerce",
    "AICRM_PUSH_CAPABILITY_PAYMENT_QUERY_ENABLED": "extension.commerce",
    "AICRM_PUSH_CAPABILITY_PRIVATE_BROADCAST_ENABLED": "core.automation",
    "AICRM_PUSH_CAPABILITY_QUESTIONNAIRE_EXTERNAL_PUSH_ENABLED": "extension.forms",
    "AICRM_PUSH_CAPABILITY_TAGS_ENABLED": "core.crm",
    "AICRM_PUSH_CAPABILITY_WELCOME_MESSAGE_ENABLED": "core.engagement",
}

_INTEGRATION_GATEWAY_BOOLEAN_KEYS = frozenset(
    {
        "AICRM_ENABLE_REAL_WECOM_GROUP_SYNC",
        "AICRM_MEDIA_UPLOAD_CONFIG_REVIEWED",
        "AICRM_MEDIA_UPLOAD_LIVE_ADAPTER_ENABLED",
        "AICRM_MEDIA_UPLOAD_LIVE_UPLOAD_APPROVED",
        "AICRM_NEXT_ENABLE_REAL_ALIPAY",
        "AICRM_NEXT_ENABLE_REAL_ARCHIVE_SYNC",
        "AICRM_NEXT_ENABLE_REAL_CLOUD_STORAGE",
        "AICRM_NEXT_ENABLE_REAL_CONTACTS_SYNC",
        "AICRM_NEXT_ENABLE_REAL_CUSTOMER_PROJECTION_SYNC",
        "AICRM_NEXT_ENABLE_REAL_IDENTITY_MAPPING",
        "AICRM_NEXT_ENABLE_REAL_MCP_TOOLS",
        "AICRM_NEXT_ENABLE_REAL_OPENCLAW_BRIDGE",
        "AICRM_NEXT_ENABLE_REAL_PAYMENT_NOTIFY",
        "AICRM_NEXT_ENABLE_REAL_PRODUCT_WRITES",
        "AICRM_NEXT_ENABLE_REAL_QUESTIONNAIRE_WEBHOOK",
        "AICRM_NEXT_ENABLE_REAL_USER_OPS_BATCH_SEND",
        "AICRM_NEXT_ENABLE_REAL_USER_OPS_DEFERRED_JOBS",
        "AICRM_NEXT_ENABLE_REAL_USER_OPS_DND",
        "AICRM_NEXT_ENABLE_REAL_WECHAT_OAUTH",
        "AICRM_NEXT_ENABLE_REAL_WECHAT_PAY",
        "AICRM_NEXT_ENABLE_REAL_WECOM_DISPATCH",
        "AICRM_NEXT_ENABLE_REAL_WECOM_MEDIA",
        "AICRM_NEXT_ENABLE_REAL_WECOM_TAG",
        "AICRM_OAUTH_IDENTITY_CONFIG_REVIEWED",
        "AICRM_OAUTH_IDENTITY_LIVE_ADAPTER_ENABLED",
        "AICRM_OAUTH_IDENTITY_LIVE_CALLBACK_APPROVED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_CONFIG_REVIEWED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_CREDENTIAL_SOURCE_REVIEWED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_ENDPOINT_REVIEWED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_LIVE_ADAPTER_ENABLED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_LIVE_CALL_APPROVED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_NO_AUTOMATION_EXECUTION_CONFIRMED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_NO_OUTBOUND_SEND_CONFIRMED",
        "AICRM_OPENCLAW_MCP_AI_ASSIST_PROMPT_REDACTION_CONFIRMED",
        "AICRM_PAYMENT_COMMERCE_LIVE_ADAPTER_ENABLED",
        "AICRM_PAYMENT_COMMERCE_LIVE_CALL_APPROVED",
        "AICRM_PAYMENT_COMMERCE_NO_MONEY_MOVEMENT_CONFIRMED",
        "AICRM_PAYMENT_COMMERCE_PROVIDER_CONFIG_REVIEWED",
        "AICRM_PAYMENT_COMMERCE_SANDBOX_MODE_APPROVED",
        "AICRM_WECOM_CONTACT_CALLBACK_CONFIG_REVIEWED",
        "AICRM_WECOM_CONTACT_CALLBACK_LIVE_ADAPTER_ENABLED",
        "AICRM_WECOM_CONTACT_CALLBACK_LIVE_PROCESSING_APPROVED",
    }
)

_INTEGRATION_GATEWAY_MODE_KEYS = frozenset(
    {
        "AICRM_NEXT_ARCHIVE_SYNC_MODE",
        "AICRM_NEXT_CONTACTS_SYNC_MODE",
        "AICRM_NEXT_CUSTOMER_CONTEXT_TOOL_MODE",
        "AICRM_NEXT_CUSTOMER_PROJECTION_SYNC_MODE",
        "AICRM_NEXT_IDENTITY_MAPPING_MODE",
        "AICRM_NEXT_MCP_TOOL_MODE",
        "AICRM_NEXT_MEDIA_STORAGE_MODE",
        "AICRM_NEXT_OPENCLAW_LEGACY_MODE",
        "AICRM_NEXT_PAYMENT_NOTIFY_MODE",
        "AICRM_NEXT_PRODUCT_WRITE_MODE",
        "AICRM_NEXT_QUESTIONNAIRE_WEBHOOK_MODE",
        "AICRM_NEXT_USER_OPS_BATCH_SEND_MODE",
        "AICRM_NEXT_USER_OPS_DEFERRED_JOBS_MODE",
        "AICRM_NEXT_USER_OPS_DND_MODE",
        "AICRM_NEXT_WECHAT_OAUTH_MODE",
        "AICRM_NEXT_WECOM_DISPATCH_MODE",
        "AICRM_NEXT_WECOM_MEDIA_MODE",
        "AICRM_NEXT_WECOM_TAG_MODE",
    }
)

_INTEGRATION_GATEWAY_INTEGER_DEFAULTS = {
    "AICRM_HUANGYOUCAN_DB_CONNECT_TIMEOUT_SECONDS": 10,
    "AICRM_HUANGYOUCAN_DB_PORT": 3306,
    "AICRM_HUANGYOUCAN_DB_READ_TIMEOUT_SECONDS": 60,
    "AICRM_NEXT_WECHAT_OAUTH_TIMEOUT": 15,
    "AICRM_WECOM_ADMIN_AUTH_TIMEOUT": 10,
    "AICRM_WECOM_GROUP_TIMEOUT": 15,
    "AICRM_WECOM_MEDIA_TIMEOUT": 15,
    "AICRM_WECOM_OPERATION_MEMBERS_TIMEOUT": 15,
    "AICRM_WECOM_TAG_TIMEOUT_SECONDS": 15,
    "WECHAT_OAUTH_TIMEOUT": 15,
    "WECOM_AUTH_TIMEOUT": 10,
}

_INTEGRATION_GATEWAY_STRING_KEYS = frozenset(
    {
        "AICRM_HUANGYOUCAN_DB_HOST",
        "AICRM_HUANGYOUCAN_DB_NAME",
        "AICRM_HUANGYOUCAN_DB_PASSWORD",
        "AICRM_HUANGYOUCAN_DB_USER",
        "AICRM_MEDIA_UPLOAD_PROVIDER_NAME",
        "AICRM_MEDIA_UPLOAD_PROVIDER_SECRET",
        "AICRM_NEXT_WECHAT_OAUTH_BASE_URL",
        "AICRM_OAUTH_IDENTITY_APP_ID",
        "AICRM_OAUTH_IDENTITY_APP_SECRET",
        "AICRM_PAYMENT_COMMERCE_PROVIDER_NAME",
        "AICRM_PAYMENT_COMMERCE_PROVIDER_SECRET",
        "AICRM_WECOM_CONTACT_CALLBACK_AES_KEY",
        "AICRM_WECOM_CONTACT_CALLBACK_CORP_ID",
        "AICRM_WECOM_CONTACT_CALLBACK_TOKEN",
        "AICRM_WECOM_GROUP_API_BASE",
        "AICRM_WECOM_GROUP_CORP_ID",
        "AICRM_WECOM_GROUP_SECRET",
        "AICRM_WECOM_MEDIA_API_BASE",
        "AICRM_WECOM_MEDIA_CORP_ID",
        "AICRM_WECOM_MEDIA_SECRET",
        "AICRM_WECOM_OPERATION_MEMBERS_API_BASE",
        "AICRM_WECOM_OPERATION_MEMBERS_CORP_ID",
        "AICRM_WECOM_OPERATION_MEMBERS_SECRET",
        "AICRM_WECOM_TAG_AGENT_SECRET",
        "AICRM_WECOM_TAG_API_BASE",
        "AICRM_WECOM_TAG_CORP_ID",
    }
)

_INTEGRATION_GATEWAY_NEW_KEYS = frozenset(
    _INTEGRATION_GATEWAY_BOOLEAN_KEYS
    | _INTEGRATION_GATEWAY_MODE_KEYS
    | set(_INTEGRATION_GATEWAY_INTEGER_DEFAULTS)
    | _INTEGRATION_GATEWAY_STRING_KEYS
)


def _integration_gateway_capability(key: str) -> str:
    if key.startswith("AICRM_HUANGYOUCAN_"):
        return "extension.hxc"
    if "OPENCLAW" in key or "MCP_" in key or "CUSTOMER_CONTEXT_TOOL" in key:
        return "extension.ai"
    if "PAYMENT" in key or "WECHAT_PAY" in key or "ALIPAY" in key or "PRODUCT_WRITE" in key:
        return "extension.commerce"
    if "ARCHIVE_SYNC" in key:
        return "extension.archive"
    if "QUESTIONNAIRE" in key or "WECHAT_OAUTH" in key:
        return "extension.forms"
    if "USER_OPS" in key or "WECOM_DISPATCH" in key:
        return "core.automation"
    if (
        "CONTACTS_SYNC" in key
        or "CUSTOMER_PROJECTION" in key
        or "IDENTITY_MAPPING" in key
        or "WECOM_OPERATION_MEMBERS" in key
        or "WECOM_TAG" in key
    ):
        return "core.crm"
    if (
        "MEDIA_UPLOAD" in key
        or "MEDIA_STORAGE" in key
        or "WECOM_MEDIA" in key
        or "WECOM_GROUP" in key
        or "CLOUD_STORAGE" in key
    ):
        return "core.engagement"
    return "core.channels"


def _integration_gateway_section(capability_id: str) -> str:
    return {
        "extension.ai": "ai_services",
        "extension.archive": "wecom_archive",
        "extension.commerce": "wechat_pay_h5",
        "extension.forms": "wechat_mp",
        "core.automation": "reliability",
    }.get(capability_id, "wecom_base" if capability_id != "extension.hxc" else "infrastructure")


def _integration_gateway_definition(key: str) -> dict[str, Any]:
    capability_id = _integration_gateway_capability(key)
    common = {
        "capability_id": capability_id,
        "description": (
            "通过配置发布管理；切换前现有环境值保持权威。真实 Provider 开关仍需同时满足外发执行门禁。"
        ),
    }
    if key in _INTEGRATION_GATEWAY_BOOLEAN_KEYS:
        return {
            **_definition(
                key,
                f"运行开关：{key}",
                section=_integration_gateway_section(capability_id),
                value_type="boolean",
                default="false",
            ),
            **common,
        }
    if key in _INTEGRATION_GATEWAY_MODE_KEYS:
        options = (
            ("disabled", "fake", "staging")
            if key in {"AICRM_NEXT_MEDIA_STORAGE_MODE", "AICRM_NEXT_WECOM_MEDIA_MODE"}
            else ("disabled", "fake", "staging", "production")
        )
        return {
            **_definition(
                key,
                f"Adapter 模式：{key}",
                section=_integration_gateway_section(capability_id),
                default="fake",
                options=options,
            ),
            **common,
        }
    if key in _INTEGRATION_GATEWAY_INTEGER_DEFAULTS:
        maximum = 65535 if key.endswith("_PORT") else 3600
        return {
            **_definition(
                key,
                f"运行参数：{key}",
                section=_integration_gateway_section(capability_id),
                value_type="integer",
                default=str(_INTEGRATION_GATEWAY_INTEGER_DEFAULTS[key]),
                minimum=1,
                maximum=maximum,
            ),
            **common,
        }
    return {
        **_definition(
            key,
            f"运行参数：{key}",
            section=_integration_gateway_section(capability_id),
            default=(
                "https://api.weixin.qq.com"
                if key == "AICRM_NEXT_WECHAT_OAUTH_BASE_URL"
                else "https://qyapi.weixin.qq.com"
                if key.endswith("_API_BASE")
                else ""
            ),
        ),
        **common,
    }


RUNTIME_CONFIG_DEFINITIONS: dict[str, dict[str, Any]] = {
    RUNTIME_CONFIG_CUTOVER_KEYS_KEY: _definition(
        RUNTIME_CONFIG_CUTOVER_KEYS_KEY,
        "运行配置切换清单",
        section="infrastructure",
        description="逗号或换行分隔；仅允许已完成 shadow compare 的受管配置键，禁止通配符。",
    ),
    "AICRM_ADMIN_AUTH_ENFORCED": _definition(
        "AICRM_ADMIN_AUTH_ENFORCED",
        "后台认证强制执行",
        section="admin_auth",
        value_type="boolean",
        description="生产环境始终 fail-closed；该值仅能在非生产环境显式放宽。",
    ),
    "AICRM_ROUTE_POLICY_ENFORCED": _definition(
        "AICRM_ROUTE_POLICY_ENFORCED",
        "路由权限策略强制执行",
        section="admin_auth",
        value_type="boolean",
        description="非测试环境始终强制执行；该值仅用于测试隔离。",
    ),
    "AICRM_ADMIN_SESSION_COOKIE_SECURE": _definition(
        "AICRM_ADMIN_SESSION_COOKIE_SECURE",
        "后台会话 Cookie Secure",
        section="admin_auth",
        value_type="boolean",
        default="false",
        description="公开 HTTPS 或生产环境始终启用 Secure；该配置可在其他环境提前开启。",
    ),
    "AICRM_WECOM_ADMIN_AUTH_ENABLE_REAL": _definition(
        "AICRM_WECOM_ADMIN_AUTH_ENABLE_REAL",
        "启用真实企业微信后台登录",
        section="wecom_base",
        value_type="boolean",
        default="false",
        description="开启后后台登录才允许发起真实企微 OAuth/扫码授权。",
    ),
    **{
        key: _definition(
            key,
            label,
            section="infrastructure",
            value_type="boolean",
            description="由配置发布控制；环境变量只在迁移观察期作为兼容回退。",
        )
        for key, label in _INTERNAL_EVENT_BOOLEAN_KEYS.items()
    },
    "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES": _definition(
        "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES",
        "内部事件类型白名单",
        section="infrastructure",
        description="逗号分隔；为空时不按事件类型缩小范围。",
    ),
    "AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS": _definition(
        "AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS",
        "内部事件 Consumer 白名单",
        section="infrastructure",
        description="逗号分隔；配置事件—Consumer 对时，以更精确的配对白名单为准。",
    ),
    "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS": _definition(
        "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS",
        "内部事件—Consumer 配对白名单",
        section="infrastructure",
        description="逗号或换行分隔，每项格式为 event_type:consumer_name。",
    ),
    "AICRM_INTERNAL_EVENTS_LEGACY_PATH_RETIRE_AFTER_DAYS": _definition(
        "AICRM_INTERNAL_EVENTS_LEGACY_PATH_RETIRE_AFTER_DAYS",
        "Legacy 路径退役观察天数",
        section="infrastructure",
        value_type="integer",
        default="7",
        minimum=1,
        maximum=365,
    ),
    "AICRM_INTERNAL_EVENTS_WORKER_BATCH_SIZE": _definition(
        "AICRM_INTERNAL_EVENTS_WORKER_BATCH_SIZE",
        "内部事件 Worker 批大小",
        section="infrastructure",
        value_type="integer",
        default="50",
        minimum=1,
        maximum=500,
    ),
    "AICRM_INTERNAL_EVENT_WORKER_BATCH_SIZE": _definition(
        "AICRM_INTERNAL_EVENT_WORKER_BATCH_SIZE",
        "内部事件 Worker 批大小（旧别名）",
        section="infrastructure",
        value_type="integer",
        minimum=1,
        maximum=500,
        deprecated_after="2026-10-01",
        description="兼容旧配置；新发布必须使用 AICRM_INTERNAL_EVENTS_WORKER_BATCH_SIZE。",
    ),
    "AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE": _definition(
        "AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE",
        "内部事件同步自动执行上限",
        section="infrastructure",
        value_type="integer",
        default="1",
        minimum=1,
        maximum=500,
    ),
    "AICRM_PII_AUDIT_ENABLED": _definition(
        "AICRM_PII_AUDIT_ENABLED",
        "PII 访问审计",
        section="reliability",
        value_type="boolean",
        description="生产环境始终启用；该配置可在其他环境提前开启。",
    ),
    "AICRM_READINESS_MAX_QUEUE_AGE_SECONDS": _definition(
        "AICRM_READINESS_MAX_QUEUE_AGE_SECONDS",
        "队列最老待处理时长告警阈值",
        section="reliability",
        value_type="integer",
        default="3600",
        minimum=0,
    ),
    "AICRM_READINESS_MAX_TERMINAL_COUNT": _definition(
        "AICRM_READINESS_MAX_TERMINAL_COUNT",
        "队列终态失败数量告警阈值",
        section="reliability",
        value_type="integer",
        default="0",
        minimum=0,
    ),
    "AICRM_WECOM_PROVIDER_TARGET_POLICY": _definition(
        "AICRM_WECOM_PROVIDER_TARGET_POLICY",
        "企微 Provider 目标策略",
        section="wecom_base",
        default="blocked",
        options=("blocked", "allowlisted_canary"),
        description="仅显式 allowlisted_canary 才允许命中白名单的验收目标；默认阻断。",
    ),
    "AICRM_WECOM_CANARY_ALLOWED_MEDIA_TARGETS": _definition(
        "AICRM_WECOM_CANARY_ALLOWED_MEDIA_TARGETS",
        "企微素材验收目标白名单",
        section="wecom_base",
        description="逗号或空白分隔；仅用于受控企微素材 canary。",
    ),
    **{
        key: {
            **_definition(
                key,
                f"推送能力开关：{key.removeprefix('AICRM_PUSH_CAPABILITY_').removesuffix('_ENABLED').lower()}",
                section="reliability",
                value_type="boolean",
                default="false",
                description="能力禁用时 External Effect Worker 必须在最终外发边界阻断。",
            ),
            "capability_id": capability_id,
        }
        for key, capability_id in _PUSH_CAPABILITY_OWNERS.items()
    },
    "AICRM_NEXT_WECHAT_PAY_MODE": _definition(
        "AICRM_NEXT_WECHAT_PAY_MODE",
        "微信支付 Adapter 模式",
        section="wechat_pay_h5",
        default="fake",
        options=("disabled", "fake", "staging", "production"),
    ),
    "AICRM_NEXT_ALIPAY_MODE": _definition(
        "AICRM_NEXT_ALIPAY_MODE",
        "支付宝 Adapter 模式",
        section="alipay_pay_wap",
        default="fake",
        options=("disabled", "fake", "staging", "production"),
    ),
    **{
        key: _integration_gateway_definition(key)
        for key in sorted(_INTEGRATION_GATEWAY_NEW_KEYS)
    },
}


__all__ = ["RUNTIME_CONFIG_DEFINITIONS"]
