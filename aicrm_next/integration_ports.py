from __future__ import annotations

"""Versioned integration surface consumed by business capabilities.

Concrete provider adapters remain owned by ``integration_gateway``. Business
contexts import only this app-level surface so provider implementation modules
can evolve without creating context-to-context dependencies.
"""

from aicrm_next.channels.integration_gateway.channel_completion_client import (
    ChannelCompletionClient,
    ChannelCompletionReadPort,
)
from aicrm_next.channels.integration_gateway.customer_sync_adapters import (
    build_archive_sync_adapter,
    build_contacts_sync_adapter,
    build_customer_projection_sync_gateway,
    build_identity_mapping_adapter,
    customer_sync_side_effect_safety,
)
from aicrm_next.channels.integration_gateway.huangyoucan_usage_client import (
    HuangYouCanUsageSource,
    build_huangyoucan_usage_source,
)
from aicrm_next.channels.integration_gateway.idempotency import make_idempotency_key
from aicrm_next.channels.integration_gateway.lesson_card_cover_client import (
    LessonCardCoverClientError,
    build_lesson_card_cover_client,
)
from aicrm_next.channels.integration_gateway.media_adapters import (
    WeComMediaAdapter,
    build_cloud_storage_adapter,
    build_wecom_media_adapter,
    extract_base64_payload,
)
from aicrm_next.channels.integration_gateway.payment_adapters import (
    AlipayAdapter,
    PaymentNotifyGateway,
    PaymentReturnGateway,
    ProductWriteGateway,
    WeChatPayAdapter,
    build_alipay_adapter,
    build_payment_notify_gateway,
    build_payment_return_gateway,
    build_product_write_gateway,
    build_wechat_pay_adapter,
)
from aicrm_next.channels.integration_gateway.questionnaire_adapters import (
    QuestionnaireSubmitSideEffectGateway,
    WeChatOAuthAdapter,
    build_wechat_oauth_adapter,
)
from aicrm_next.channels.integration_gateway.user_ops_adapters import (
    UserOpsBatchSendGateway,
    UserOpsDeferredJobGateway,
    UserOpsDndWriteGateway,
    build_user_ops_batch_send_gateway,
    build_user_ops_deferred_job_gateway,
    build_user_ops_dnd_gateway,
    build_wecom_message_dispatch_adapter,
)
from aicrm_next.channels.integration_gateway.wechat_oauth_client import (
    WeChatOAuthClientError,
    build_wechat_oauth_client,
)
from aicrm_next.channels.integration_gateway.wechat_pay_client import (
    WeChatPayClient,
    WeChatPayClientConfig,
    WeChatPayClientError,
    wechat_pay_client_config_from_env,
)
from aicrm_next.channels.integration_gateway.wechat_shop_client import (
    WeChatShopClient,
    WeChatShopClientConfig,
    WeChatShopClientError,
)
from aicrm_next.channels.integration_gateway.wecom_admin_auth_client import (
    WeComAdminAuthClient,
    WeComAdminAuthClientError,
    build_wecom_admin_auth_client,
)
from aicrm_next.channels.integration_gateway.wecom_channel_entry_client import (
    GuardedWeComAdapter,
    ProductionWeComAdapter,
    WeComAdapterBlocked,
    WeComApiError,
    build_default_wecom_channel_entry_adapter,
    missing_wecom_config,
    real_wecom_calls_enabled,
    wecom_adapter_diagnostics,
)
from aicrm_next.channels.integration_gateway.wecom_group_adapter import (
    build_group_ops_queue_stats_gateway,
    build_wecom_group_asset_adapter,
)
from aicrm_next.channels.integration_gateway.wecom_group_contract import WeComGroupAssetAdapterContract
from aicrm_next.channels.integration_gateway.wecom_group_invite_adapter import build_wecom_group_invite_adapter
from aicrm_next.channels.integration_gateway.wecom_jssdk_adapter import (
    SidebarJSSDKConfigError,
    SidebarJSSDKInputError,
    build_sidebar_jssdk_config,
    normalize_jssdk_url,
)
from aicrm_next.channels.integration_gateway.wecom_media_upload_client import (
    WeComMediaUploadClientError,
    build_wecom_media_upload_client,
)
from aicrm_next.channels.integration_gateway.wecom_operation_members_client import (
    WeComOperationMembersClient,
    build_wecom_operation_members_client,
)
from aicrm_next.channels.integration_gateway.wecom_tag_live_gateway import (
    WeComTagLiveGateway,
    build_wecom_tag_live_gateway,
)

__all__ = [
    "AlipayAdapter",
    "ChannelCompletionClient",
    "ChannelCompletionReadPort",
    "GuardedWeComAdapter",
    "HuangYouCanUsageSource",
    "LessonCardCoverClientError",
    "PaymentNotifyGateway",
    "PaymentReturnGateway",
    "ProductWriteGateway",
    "ProductionWeComAdapter",
    "QuestionnaireSubmitSideEffectGateway",
    "SidebarJSSDKConfigError",
    "SidebarJSSDKInputError",
    "UserOpsBatchSendGateway",
    "UserOpsDeferredJobGateway",
    "UserOpsDndWriteGateway",
    "WeChatOAuthAdapter",
    "WeChatOAuthClientError",
    "WeChatPayAdapter",
    "WeChatPayClient",
    "WeChatPayClientConfig",
    "WeChatPayClientError",
    "WeChatShopClient",
    "WeChatShopClientConfig",
    "WeChatShopClientError",
    "WeComAdapterBlocked",
    "WeComAdminAuthClient",
    "WeComAdminAuthClientError",
    "WeComApiError",
    "WeComGroupAssetAdapterContract",
    "WeComMediaAdapter",
    "WeComMediaUploadClientError",
    "WeComOperationMembersClient",
    "WeComTagLiveGateway",
    "build_alipay_adapter",
    "build_archive_sync_adapter",
    "build_cloud_storage_adapter",
    "build_contacts_sync_adapter",
    "build_customer_projection_sync_gateway",
    "build_default_wecom_channel_entry_adapter",
    "build_group_ops_queue_stats_gateway",
    "build_huangyoucan_usage_source",
    "build_identity_mapping_adapter",
    "build_lesson_card_cover_client",
    "build_payment_notify_gateway",
    "build_payment_return_gateway",
    "build_product_write_gateway",
    "build_sidebar_jssdk_config",
    "build_user_ops_batch_send_gateway",
    "build_user_ops_deferred_job_gateway",
    "build_user_ops_dnd_gateway",
    "build_wechat_oauth_adapter",
    "build_wechat_oauth_client",
    "build_wechat_pay_adapter",
    "build_wecom_admin_auth_client",
    "build_wecom_group_asset_adapter",
    "build_wecom_group_invite_adapter",
    "build_wecom_media_adapter",
    "build_wecom_media_upload_client",
    "build_wecom_message_dispatch_adapter",
    "build_wecom_operation_members_client",
    "build_wecom_tag_live_gateway",
    "customer_sync_side_effect_safety",
    "extract_base64_payload",
    "make_idempotency_key",
    "missing_wecom_config",
    "normalize_jssdk_url",
    "real_wecom_calls_enabled",
    "wechat_pay_client_config_from_env",
    "wecom_adapter_diagnostics",
]
