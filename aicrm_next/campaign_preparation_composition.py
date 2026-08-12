from __future__ import annotations

from .automation.background_jobs.broadcast_queue_worker import (
    configure_content_package_attachment_resolver,
    configure_dynamic_miniprogram_attachment_resolver,
)
from .platform.platform_foundation.background_jobs.immediate_broadcast_delegate import (
    configure_broadcast_material_plan_resolver,
)
from .crm.identity_contact.campaign_admission_port import build_campaign_admission_port
from .engagement.media_library.dynamic_card_port import build_dynamic_card_media_port
from .engagement.media_library.broadcast_effect_port import build_broadcast_material_plan_port
from .extensions.ai.ai_assist.campaign_preparations import (
    CampaignPreparationDependencies,
    configure_campaign_preparation_dependencies as _configure,
)
from .extensions.growth.cloud_orchestrator.campaign_preparation_port import (
    build_campaign_preparation_command_port,
)
from .extensions.hxc.operation_cycles.strategy_context_port import (
    build_operation_cycle_execution_context_port,
)


def configure_campaign_preparation_dependencies() -> None:
    media_port = build_dynamic_card_media_port()
    _configure(
        CampaignPreparationDependencies(
            context_port=build_operation_cycle_execution_context_port(),
            admission_port=build_campaign_admission_port(),
            media_port=media_port,
            command_port=build_campaign_preparation_command_port(),
        )
    )
    material_plan_port = build_broadcast_material_plan_port()
    configure_dynamic_miniprogram_attachment_resolver(media_port.resolve_attachment)
    configure_content_package_attachment_resolver(material_plan_port.plan)
    configure_broadcast_material_plan_resolver(material_plan_port.plan)


__all__ = ["configure_campaign_preparation_dependencies"]
