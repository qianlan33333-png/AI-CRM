from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ReceiveWeComCallbackCommand(BaseModel):
    method: str
    query: dict[str, str] = Field(default_factory=dict)
    body: bytes = b""


class ProcessWeComExternalContactEventCommand(BaseModel):
    corp_id: str = ""
    event_data: dict[str, Any] = Field(default_factory=dict)
    payload_xml: str = ""
    route: str = ""
    callback_received_at: datetime | None = None


class ProcessChannelEntryCommand(BaseModel):
    unionid: str = ""
    external_contact_id: str = ""
    phone: str = ""
    payload_json: dict[str, Any] = Field(default_factory=dict)
    operator_id: str = ""
    follow_user_userid: str = ""
    source_type: str = "qrcode"
    event_action: str = "qrcode_enter"
    send_welcome_message: bool = False
    event_log_id: int | None = None
    callback_received_at: datetime | None = None
    dry_run: bool = False


class DiagnoseChannelRuntimeQuery(BaseModel):
    scene_value: str = ""
    channel_id: int | None = None


class GenerateChannelQrCodeCommand(BaseModel):
    channel_id: int
    scene_value: str = ""
    operator_id: str = ""
    owner_staff_id: str = ""
    skip_verify: bool | None = None


class DryRunChannelEntryCommand(ProcessChannelEntryCommand):
    dry_run: bool = True


class RepairChannelEntryCommand(BaseModel):
    event_log_id: int | None = None
    external_userid: str = ""
    scene_value: str = ""
    corp_id: str = ""
