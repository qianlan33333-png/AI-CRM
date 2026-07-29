from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

CommandStatus = Literal["pending", "completed", "failed", "dry_run"]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class IdempotencyKey:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("idempotency_key cannot be empty")


@dataclass(frozen=True)
class CommandContext:
    actor_id: str = ""
    actor_type: str = "system"
    request_id: str = ""
    trace_id: str = ""
    source_route: str = ""
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Command:
    command_name: str
    payload: dict[str, Any] = field(default_factory=dict)
    command_id: str = field(default_factory=lambda: uuid4().hex)
    idempotency_key: str = ""
    context: CommandContext = field(default_factory=CommandContext)
    created_at: str = field(default_factory=utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["context"] = self.context.to_dict()
        return payload


@dataclass(frozen=True)
class CommandResult:
    command_name: str
    command_id: str
    status: CommandStatus
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    actor_id: str = ""
    actor_type: str = ""
    request_id: str = ""
    trace_id: str = ""
    source_route: str = ""
    created_at: str = ""
    completed_at: str = field(default_factory=utcnow_iso)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
