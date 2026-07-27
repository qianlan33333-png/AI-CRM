from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class WeComTagWriteCommand:
    command_id: str = field(default_factory=lambda: uuid4().hex)
    idempotency_key: str = ""
    actor_id: str = "wecom_tag_admin"
    actor_type: str = "user"
    target_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False
    source_route: str = ""
    trace_id: str = field(default_factory=lambda: uuid4().hex)

    command_name: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("command_name", None)
        return payload


@dataclass(frozen=True)
class CreateWeComTagCommand(WeComTagWriteCommand):
    command_name: str = "wecom.tag.create"


@dataclass(frozen=True)
class UpdateWeComTagCommand(WeComTagWriteCommand):
    command_name: str = "wecom.tag.update"


@dataclass(frozen=True)
class DeleteWeComTagCommand(WeComTagWriteCommand):
    command_name: str = "wecom.tag.delete"


@dataclass(frozen=True)
class CreateWeComTagGroupCommand(WeComTagWriteCommand):
    command_name: str = "wecom.tag_group.create"


@dataclass(frozen=True)
class UpdateWeComTagGroupCommand(WeComTagWriteCommand):
    command_name: str = "wecom.tag_group.update"


@dataclass(frozen=True)
class DeleteWeComTagGroupCommand(WeComTagWriteCommand):
    command_name: str = "wecom.tag_group.delete"


@dataclass(frozen=True)
class SyncWeComTagCatalogCommand(WeComTagWriteCommand):
    command_name: str = "wecom.tag.sync"
