from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DeliveryLineageItem(BaseModel):
    lineage_id: str
    source_type: str = ""
    source_id: str = ""
    business_domain: str = ""
    unionid: str = ""
    broadcast_job_id: int | None = None
    broadcast_job_status: str = ""
    broadcast_event_count: int = 0
    outbound_task_id: int | None = None
    outbound_task_status: str = ""
    external_effect_job_id: int | None = None
    external_effect_status: str = ""
    external_effect_attempt_count: int = 0
    internal_event_id: str = ""
    domain_event_id: str = ""
    last_error: str = ""
    first_created_at: datetime | None = None
    last_updated_at: datetime | None = None
    trace_id: str = ""


class DeliveryLineageList(BaseModel):
    ok: bool = True
    items: list[DeliveryLineageItem] = Field(default_factory=list)
    limit: int = 50
    offset: int = 0


class DeliveryLineageDetail(BaseModel):
    ok: bool = True
    item: DeliveryLineageItem
    related: dict[str, Any] = Field(default_factory=dict)


class DeliveryLineageDailyMetric(BaseModel):
    metric: str
    day: str
    value: int


class DeliveryLineageDailyMetrics(BaseModel):
    ok: bool = True
    items: list[DeliveryLineageDailyMetric] = Field(default_factory=list)
    days: int = 7
