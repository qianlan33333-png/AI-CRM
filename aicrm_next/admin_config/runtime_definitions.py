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
}


__all__ = ["RUNTIME_CONFIG_DEFINITIONS"]
