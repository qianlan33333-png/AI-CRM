from __future__ import annotations

from dataclasses import dataclass


DEFAULT_LANE_CAPACITY: dict[str, int] = {
    "internal_general": 4,
    "internal_financial": 1,
    "webhook_inbox": 4,
    "wecom_interactive": 4,
    "wecom_bulk": 1,
    "wecom_media": 2,
    "outbound_webhook": 4,
}


@dataclass(frozen=True)
class QueueLane:
    name: str
    max_in_flight: int
    enabled: bool = True
    rollout_mode: str = "standby"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("queue lane name is required")
        if int(self.max_in_flight) < 1:
            raise ValueError("queue lane max_in_flight must be >= 1")
        if self.rollout_mode not in {"blocked", "standby", "shadow", "canary", "execute"}:
            raise ValueError("unsupported queue lane rollout_mode")
