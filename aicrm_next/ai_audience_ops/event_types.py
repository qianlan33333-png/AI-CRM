from __future__ import annotations

INCREMENTAL_TICK_EVENT = "ai_audience.refresh.incremental_tick"
DAILY_TICK_EVENT = "ai_audience.refresh.daily_tick"
SOURCE_CHANGED_EVENT = "ai_audience.source.changed"
REFRESH_REQUESTED_EVENT = "ai_audience.refresh.requested"
MEMBER_EVENT_PREFIX = "ai_audience.member."
RUN_REFRESHED_EVENT = "ai_audience.run.refreshed"
INBOUND_RECEIVED_EVENT = "ai_audience.inbound.received"

INCREMENTAL_REFRESH_CONSUMER = "ai_audience_incremental_refresh_consumer"
DAILY_REFRESH_CONSUMER = "ai_audience_daily_refresh_consumer"
SOURCE_POKE_CONSUMER = "ai_audience_source_poke_consumer"
REFRESH_INTENT_CONSUMER = "ai_audience_refresh_intent_consumer"
OUTBOUND_EFFECT_CONSUMER = "ai_audience_outbound_effect_planner"
INBOUND_ACTION_CONSUMER = "ai_audience_inbound_action_planner"
