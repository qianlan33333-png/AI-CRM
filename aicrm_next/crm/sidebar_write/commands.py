from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class SidebarWriteCommand:
    command_id: str = field(default_factory=lambda: uuid4().hex)
    idempotency_key: str = ""
    actor_id: str = ""
    actor_type: str = "system"
    external_userid: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False
    source_route: str = ""
    trace_id: str = ""

    command_name: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("command_name", None)
        return payload


@dataclass(frozen=True)
class BindMobileCommand(SidebarWriteCommand):
    command_name: str = "sidebar.bind_mobile"


@dataclass(frozen=True)
class UpsertLeadPoolClassTermCommand(SidebarWriteCommand):
    command_name: str = "sidebar.upsert_lead_pool_class_term"


@dataclass(frozen=True)
class MarkSignupTagCommand(SidebarWriteCommand):
    command_name: str = "sidebar.mark_signup_tag"


@dataclass(frozen=True)
class SetFollowupSegmentCommand(SidebarWriteCommand):
    command_name: str = "sidebar.set_followup_segment"


@dataclass(frozen=True)
class MarkEnrolledCommand(SidebarWriteCommand):
    command_name: str = "sidebar.mark_enrolled"


@dataclass(frozen=True)
class UnmarkEnrolledCommand(SidebarWriteCommand):
    command_name: str = "sidebar.unmark_enrolled"


@dataclass(frozen=True)
class UpdateSidebarProfileCommand(SidebarWriteCommand):
    command_name: str = "sidebar.update_profile"


@dataclass(frozen=True)
class PlanMaterialSendCommand(SidebarWriteCommand):
    command_name: str = "sidebar.plan_material_send"
