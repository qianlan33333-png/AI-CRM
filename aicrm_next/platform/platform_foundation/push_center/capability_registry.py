from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from aicrm_next.platform.platform_foundation.external_effects.models import (
    AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK,
    AI_ASSIST_CAMPAIGN_MESSAGE_PLAN,
    FEISHU_WEBHOOK_NOTIFY,
    GROUP_OPS_MESSAGE_LOOPBACK,
    GROUP_OPS_WEBHOOK_ACTION_LOOPBACK,
    MEDIA_STORAGE_UPLOAD,
    OPENCLAW_CONTEXT_PUSH,
    PAYMENT_ALIPAY_ORDER_QUERY,
    PAYMENT_ALIPAY_REFUND_QUERY,
    PAYMENT_WECHAT_ORDER_QUERY,
    PAYMENT_WECHAT_REFUND_REQUEST,
    PAYMENT_WECHAT_REFUND_QUERY,
    WEBHOOK_CUSTOMER_AUTOMATION_RETRY,
    WEBHOOK_CUSTOMER_AUTOMATION_RETRY_DUE,
    WEBHOOK_GENERIC_PUSH,
    WEBHOOK_ORDER_PAID_PUSH,
    WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
    WECOM_CONTACT_TAG_MARK,
    WECOM_CONTACT_TAG_UNMARK,
    WECOM_MEDIA_UPLOAD,
    WECOM_MESSAGE_BROADCAST_SEND,
    WECOM_MESSAGE_GROUP_SEND,
    WECOM_MESSAGE_PRIVATE_SEND,
    WECOM_WELCOME_MESSAGE_SEND,
    WECOM_PROFILE_UPDATE,
)


@dataclass(frozen=True)
class PushCapability:
    key: str
    label: str
    description: str
    section: str
    section_label: str
    owner_module: str
    owner_label: str
    effect_types: tuple[str, ...]
    adapter_family: str
    supports_real_execution: bool
    main_visible: bool
    toggleable: bool
    setting_key: str
    readonly_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["effect_types"] = list(self.effect_types)
        return payload


def _setting_key(key: str) -> str:
    return f"AICRM_PUSH_CAPABILITY_{key.upper()}_ENABLED"


PUSH_CAPABILITIES: tuple[PushCapability, ...] = (
    PushCapability(
        key="questionnaire_external_push",
        label="问卷提交外推",
        description="问卷提交后通过 External Effect Queue 推送到业务 webhook。",
        section="questionnaire",
        section_label="问卷外推",
        owner_module="questionnaire",
        owner_label="问卷",
        effect_types=(WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,),
        adapter_family="webhook",
        supports_real_execution=True,
        main_visible=True,
        toggleable=True,
        setting_key=_setting_key("questionnaire_external_push"),
    ),
    PushCapability(
        key="order_paid_push",
        label="订单支付成功外推",
        description="订单支付成功后通过 External Effect Queue 推送到业务 webhook。",
        section="order",
        section_label="订单外推",
        owner_module="commerce",
        owner_label="交易",
        effect_types=(WEBHOOK_ORDER_PAID_PUSH,),
        adapter_family="webhook",
        supports_real_execution=True,
        main_visible=True,
        toggleable=True,
        setting_key=_setting_key("order_paid_push"),
    ),
    PushCapability(
        key="ai_assist_push",
        label="AI 助手推送",
        description="AI 助手活动消息规划、测试回环和企微私信发送能力。",
        section="ai_assist",
        section_label="AI 助手",
        owner_module="ai_assist",
        owner_label="AI 助手",
        effect_types=(AI_ASSIST_CAMPAIGN_MESSAGE_PLAN, AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK, WECOM_MESSAGE_PRIVATE_SEND),
        adapter_family="mixed",
        supports_real_execution=True,
        main_visible=True,
        toggleable=True,
        setting_key=_setting_key("ai_assist_push"),
    ),
    PushCapability(
        key="private_broadcast",
        label="私信群发",
        description="运营私信群发任务通过企微私信 adapter 发送。",
        section="private_broadcast",
        section_label="私信群发",
        owner_module="user_ops",
        owner_label="用户运营",
        effect_types=(WECOM_MESSAGE_PRIVATE_SEND,),
        adapter_family="wecom",
        supports_real_execution=True,
        main_visible=True,
        toggleable=True,
        setting_key=_setting_key("private_broadcast"),
    ),
    PushCapability(
        key="group_ops_push",
        label="群自动化运营",
        description="群运营 SOP、Webhook 触发和企微群消息发送能力。",
        section="group_ops",
        section_label="群自动化运营",
        owner_module="group_ops",
        owner_label="群运营",
        effect_types=(GROUP_OPS_MESSAGE_LOOPBACK, GROUP_OPS_WEBHOOK_ACTION_LOOPBACK, WECOM_MESSAGE_GROUP_SEND),
        adapter_family="mixed",
        supports_real_execution=True,
        main_visible=True,
        toggleable=True,
        setting_key=_setting_key("group_ops_push"),
    ),
    PushCapability(
        key="group_broadcast",
        label="群群发",
        description="群群发外部动作进入统一队列；真实群发仍受企微 adapter 与安全白名单约束。",
        section="group_broadcast",
        section_label="群群发",
        owner_module="broadcast_jobs",
        owner_label="群发队列",
        effect_types=(WECOM_MESSAGE_BROADCAST_SEND, WECOM_MESSAGE_GROUP_SEND),
        adapter_family="wecom",
        supports_real_execution=True,
        main_visible=True,
        toggleable=True,
        setting_key=_setting_key("group_broadcast"),
    ),
    PushCapability(
        key="customer_webhook",
        label="客户自动化 Webhook",
        description="客户自动化 webhook retry / retry-due 统一入队与执行门禁。",
        section="customer_webhook",
        section_label="客户自动化 Webhook",
        owner_module="admin_jobs",
        owner_label="后台任务",
        effect_types=(WEBHOOK_CUSTOMER_AUTOMATION_RETRY, WEBHOOK_CUSTOMER_AUTOMATION_RETRY_DUE),
        adapter_family="legacy_webhook",
        supports_real_execution=True,
        main_visible=True,
        toggleable=True,
        setting_key=_setting_key("customer_webhook"),
    ),
    PushCapability(
        key="tags",
        label="企微标签",
        description="企微客户标签标记、取消标记和外部联系人描述更新统一入队；真实执行仍受企微门禁约束。",
        section="tags",
        section_label="企微标签",
        owner_module="wecom_tags",
        owner_label="企微标签",
        effect_types=(WECOM_CONTACT_TAG_MARK, WECOM_CONTACT_TAG_UNMARK, WECOM_PROFILE_UPDATE),
        adapter_family="wecom",
        supports_real_execution=True,
        main_visible=True,
        toggleable=True,
        setting_key=_setting_key("tags"),
    ),
    PushCapability(
        key="welcome_message",
        label="欢迎语",
        description="企微欢迎语发送外部动作统一入队；真实执行仍受企微门禁约束。",
        section="welcome",
        section_label="欢迎语",
        owner_module="wecom",
        owner_label="企业微信",
        effect_types=(WECOM_WELCOME_MESSAGE_SEND,),
        adapter_family="wecom",
        supports_real_execution=True,
        main_visible=True,
        toggleable=True,
        setting_key=_setting_key("welcome_message"),
    ),
    PushCapability(
        key="payment_query",
        label="支付查询",
        description="微信支付和支付宝订单/退款查询统一入队与执行门禁。",
        section="payment",
        section_label="支付查询",
        owner_module="commerce",
        owner_label="交易",
        effect_types=(PAYMENT_WECHAT_ORDER_QUERY, PAYMENT_WECHAT_REFUND_REQUEST, PAYMENT_WECHAT_REFUND_QUERY, PAYMENT_ALIPAY_ORDER_QUERY, PAYMENT_ALIPAY_REFUND_QUERY),
        adapter_family="payment",
        supports_real_execution=True,
        main_visible=True,
        toggleable=True,
        setting_key=_setting_key("payment_query"),
    ),
    PushCapability(
        key="integrations",
        label="集成推送",
        description="Feishu、OpenClaw、素材存储和企微素材上传等集成外部动作统一入队与执行门禁。",
        section="integrations",
        section_label="集成推送",
        owner_module="integrations",
        owner_label="集成",
        effect_types=(FEISHU_WEBHOOK_NOTIFY, OPENCLAW_CONTEXT_PUSH, MEDIA_STORAGE_UPLOAD, WECOM_MEDIA_UPLOAD, WEBHOOK_GENERIC_PUSH),
        adapter_family="integration",
        supports_real_execution=True,
        main_visible=True,
        toggleable=True,
        setting_key=_setting_key("integrations"),
    ),
    PushCapability(
        key="test_receiver",
        label="测试接收端",
        description="本域名 loopback 虚拟测试接收端，用于验证队列闭环；不开启第三方外呼。",
        section="test_receiver",
        section_label="测试接收端",
        owner_module="external_effects",
        owner_label="平台测试",
        effect_types=(),
        adapter_family="test_receiver",
        supports_real_execution=True,
        main_visible=True,
        toggleable=True,
        setting_key="AICRM_EXTERNAL_EFFECT_TEST_RECEIVER_ENABLED",
    ),
)

CAPABILITY_BY_KEY: dict[str, PushCapability] = {item.key: item for item in PUSH_CAPABILITIES}
CAPABILITY_BY_SECTION: dict[str, PushCapability] = {item.section: item for item in PUSH_CAPABILITIES}


def get_push_capability(key: str) -> PushCapability | None:
    return CAPABILITY_BY_KEY.get(str(key or "").strip())


def capability_for_section(section: str) -> PushCapability | None:
    return CAPABILITY_BY_SECTION.get(str(section or "").strip())


def visible_push_capabilities(*, main_only: bool = True) -> list[PushCapability]:
    return [item for item in PUSH_CAPABILITIES if item.main_visible or not main_only]


def section_metadata() -> list[dict[str, Any]]:
    seen: set[str] = set()
    sections: list[dict[str, Any]] = []
    for capability in PUSH_CAPABILITIES:
        if capability.section in seen:
            continue
        seen.add(capability.section)
        sections.append(
            {
                "key": capability.section,
                "label": capability.section_label,
                "effect_types": list(capability.effect_types),
                "capability_key": capability.key,
            }
        )
    sections.append({"key": "other", "label": "其他", "effect_types": [], "capability_key": ""})
    return sections
