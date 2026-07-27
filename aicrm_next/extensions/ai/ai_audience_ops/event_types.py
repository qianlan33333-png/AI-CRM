from __future__ import annotations

INCREMENTAL_TICK_EVENT = "ai_audience.refresh.incremental_tick"
DAILY_TICK_EVENT = "ai_audience.refresh.daily_tick"
HXC_INCREMENTAL_PROJECTION_REQUESTED_EVENT = (
    "ai_audience.hxc_projection.incremental_requested"
)
HXC_DAILY_PROJECTION_REQUESTED_EVENT = "ai_audience.hxc_projection.daily_requested"
SOURCE_CHANGED_EVENT = "ai_audience.source.changed"
REFRESH_REQUESTED_EVENT = "ai_audience.refresh.requested"
MEMBER_EVENT_PREFIX = "ai_audience.member."
RUN_REFRESHED_EVENT = "ai_audience.run.refreshed"
INBOUND_RECEIVED_EVENT = "ai_audience.inbound.received"

INCREMENTAL_REFRESH_CONSUMER = "ai_audience_incremental_refresh_consumer"
DAILY_REFRESH_CONSUMER = "ai_audience_daily_refresh_consumer"
HXC_INCREMENTAL_PROJECTION_CONSUMER = "ai_audience_hxc_incremental_projection_consumer"
HXC_DAILY_PROJECTION_CONSUMER = "ai_audience_hxc_daily_projection_consumer"
SOURCE_POKE_CONSUMER = "ai_audience_source_poke_consumer"
REFRESH_INTENT_CONSUMER = "ai_audience_refresh_intent_consumer"
OUTBOUND_EFFECT_CONSUMER = "ai_audience_outbound_effect_planner"
INBOUND_ACTION_CONSUMER = "ai_audience_inbound_action_planner"
