from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from aicrm_next.send_content.dto import SendContentPackage


class HxcBroadcastTaskRequest(BaseModel):
    source_type: str = "hxc_dashboard_broadcast"
    source_id: str = ""
    idempotency_key: str = ""
    sender_userid: str = ""
    audience_filter: dict[str, Any] = Field(default_factory=dict)
    selected_customer_ids: list[Any] = Field(default_factory=list)
    content_package: SendContentPackage = Field(default_factory=SendContentPackage)
    dry_run: bool = False
