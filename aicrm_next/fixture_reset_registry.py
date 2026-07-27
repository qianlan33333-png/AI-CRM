from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .admin_auth import reset_admin_auth_fixture_state
from .admin_jobs.repository import reset_admin_jobs_fixture_state
from .automation_engine.channels_api import reset_wecom_customer_acquisition_link_fixture_state
from .automation_engine.group_ops.repo import reset_group_ops_fixture_state
from .automation_engine.repo import reset_automation_fixture_state
from .extensions.growth.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from .extensions.growth.cloud_orchestrator.campaigns_write import reset_campaign_write_fixture_state
from .extensions.growth.cloud_orchestrator.repository import reset_cloud_plan_fixture_state
from .extensions.commerce.commerce.coupons.repo import reset_coupon_fixture_state
from .extensions.commerce.commerce.repo import reset_commerce_fixture_state
from .crm.customer_tags.admin_write import reset_wecom_tag_write_fixture_state
from .crm.customer_tags.live_mutation import reset_wecom_tag_live_mutation_fixture_state
from .extensions.hxc.hxc_dashboard.repo import reset_hxc_dashboard_fixture_state
from .extensions.hxc.hxc_dashboard.safe_mode import reset_hxc_safe_mode_fixture_state
from .channels.integration_gateway.wecom_jssdk_adapter import reset_sidebar_jssdk_attempts
from .media_library.repo import reset_media_library_fixture_state
from .ops_enrollment.application import reset_user_ops_fixture_state
from .platform_foundation.external_effects import reset_external_effect_fixture_state
from .platform_foundation.internal_events import reset_internal_event_fixture_state
from .extensions.forms.questionnaire.admin_write import reset_questionnaire_admin_write_fixture_state
from .extensions.forms.questionnaire.h5_write import reset_questionnaire_h5_write_fixture_state
from .extensions.forms.questionnaire.repo import reset_questionnaire_fixture_state
from .extensions.radar.radar_links.repo import reset_radar_links_fixture_state
from .extensions.commerce.service_period import reset_service_period_fixture_state
from .crm.sidebar_write import reset_sidebar_write_fixture_state


@dataclass(frozen=True)
class FixtureResetStep:
    name: str
    reset: Callable[[], None]


FIXTURE_RESET_STEPS: tuple[FixtureResetStep, ...] = (
    FixtureResetStep("user_ops", reset_user_ops_fixture_state),
    FixtureResetStep("questionnaire", reset_questionnaire_fixture_state),
    FixtureResetStep("questionnaire_h5_write", reset_questionnaire_h5_write_fixture_state),
    FixtureResetStep("automation", reset_automation_fixture_state),
    FixtureResetStep("group_ops", reset_group_ops_fixture_state),
    FixtureResetStep("commerce", reset_commerce_fixture_state),
    FixtureResetStep("commerce_coupons", reset_coupon_fixture_state),
    FixtureResetStep("service_period", reset_service_period_fixture_state),
    FixtureResetStep("media_library", reset_media_library_fixture_state),
    FixtureResetStep("admin_jobs", reset_admin_jobs_fixture_state),
    FixtureResetStep("hxc_dashboard", reset_hxc_dashboard_fixture_state),
    FixtureResetStep("hxc_safe_mode", reset_hxc_safe_mode_fixture_state),
    FixtureResetStep("radar_links", reset_radar_links_fixture_state),
    FixtureResetStep("cloud_plan", reset_cloud_plan_fixture_state),
    FixtureResetStep("campaign_read", reset_campaign_read_fixture_state),
    FixtureResetStep("campaign_write", reset_campaign_write_fixture_state),
    FixtureResetStep("sidebar_write", reset_sidebar_write_fixture_state),
    FixtureResetStep("wecom_customer_acquisition_link", reset_wecom_customer_acquisition_link_fixture_state),
    FixtureResetStep("admin_auth", reset_admin_auth_fixture_state),
    FixtureResetStep("questionnaire_admin_write", reset_questionnaire_admin_write_fixture_state),
    FixtureResetStep("wecom_tag_write", reset_wecom_tag_write_fixture_state),
    FixtureResetStep("wecom_tag_live_mutation", reset_wecom_tag_live_mutation_fixture_state),
    FixtureResetStep("sidebar_jssdk_attempts", reset_sidebar_jssdk_attempts),
    FixtureResetStep("external_effect", reset_external_effect_fixture_state),
    FixtureResetStep("internal_event", reset_internal_event_fixture_state),
)


def reset_fixture_state() -> None:
    for step in FIXTURE_RESET_STEPS:
        step.reset()
