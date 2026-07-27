from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class SaveChannelRequest:
    data: Mapping[str, Any]
    channel_id: int | None = None


@dataclass(frozen=True)
class UpdateChannelAssignmentRequest:
    channel_id: int
    assignment_mode: str
    assignment_strategy: str
    overflow_policy: str


@dataclass(frozen=True)
class UpdateChannelQRCodeRequest:
    channel_id: int
    scene_value: str
    qr_url: str
    config_id: str = ""


class ChannelWritePort(Protocol):
    """Public transaction-preserving writer for canonical channel metadata."""

    def save(self, executor: Any, *, request: SaveChannelRequest) -> int: ...

    def update_assignment(self, executor: Any, *, request: UpdateChannelAssignmentRequest) -> None: ...

    def update_qrcode(self, executor: Any, *, request: UpdateChannelQRCodeRequest) -> dict[str, Any]: ...


def build_channel_write_port() -> ChannelWritePort:
    from .channel_write_repository import PostgresChannelWriteRepository

    return PostgresChannelWriteRepository()


__all__ = [
    "ChannelWritePort",
    "SaveChannelRequest",
    "UpdateChannelAssignmentRequest",
    "UpdateChannelQRCodeRequest",
    "build_channel_write_port",
]
