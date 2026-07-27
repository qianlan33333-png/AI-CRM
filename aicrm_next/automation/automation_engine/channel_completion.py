from __future__ import annotations

from typing import Any

from . import channels_repo
from .channel_fixture_state import FIXTURE_CHANNELS


def _text(value: Any) -> str:
    return str(value or "").strip()


def _channel_projection(row: dict[str, Any]) -> dict[str, Any]:
    channel_id = int(row.get("id") or 0)
    status = _text(row.get("status")) or "active"
    channel_type = _text(row.get("channel_type")) or "qrcode"
    carrier_type = _text(row.get("carrier_type")) or (
        "link" if channel_type == "wecom_customer_acquisition" else "qrcode"
    )
    qr_url = _text(row.get("active_qrcode_asset_url") or row.get("qr_url"))
    qrcode_status = _text(row.get("qrcode_status")) or (
        "legacy_untracked" if qr_url else "not_generated"
    )
    qrcode_asset_id = int(row.get("qrcode_asset_id") or row.get("active_qrcode_asset_id") or 0)
    selectable = True
    unavailable_reason = ""
    if status != "active":
        selectable = False
        unavailable_reason = "channel_inactive"
    elif carrier_type != "qrcode":
        selectable = False
        unavailable_reason = "channel_not_qrcode"
    elif not qrcode_asset_id or qrcode_status not in {"active", "generated", "legacy_untracked"}:
        selectable = False
        unavailable_reason = "channel_qrcode_not_generated"
    elif not qr_url.startswith("https://"):
        selectable = False
        unavailable_reason = "channel_qrcode_unavailable"
    return {
        "channel_id": channel_id,
        "channel_name": _text(row.get("channel_name")) or f"渠道 {channel_id}",
        "status": status,
        "carrier_type": carrier_type,
        "qr_url": qr_url,
        "qrcode_status": qrcode_status,
        "qrcode_asset_id": qrcode_asset_id,
        "selectable": selectable,
        "unavailable_reason": unavailable_reason,
    }


class ChannelQrReadService:
    """Channel-owned application boundary for questionnaire completion QR reads."""

    def get_channel_qr(self, channel_id: int) -> dict[str, Any] | None:
        normalized_id = int(channel_id or 0)
        if normalized_id <= 0:
            return None
        if channels_repo.uses_postgres():
            row = channels_repo.fetch_channel(normalized_id)
        else:
            row = FIXTURE_CHANNELS.get(normalized_id)
        return _channel_projection(dict(row)) if row else None

    def require_usable_channel_qr(self, channel_id: int) -> dict[str, Any]:
        channel = self.get_channel_qr(channel_id)
        if channel is None:
            raise LookupError("channel not found")
        if not channel["selectable"]:
            raise ValueError(channel["unavailable_reason"] or "channel qrcode is unavailable")
        return channel

    def list_usable_channel_qrs(self, *, limit: int = 300) -> list[dict[str, Any]]:
        if channels_repo.uses_postgres():
            rows = channels_repo.list_channels(limit=max(1, min(int(limit), 500)), status="active")
        else:
            rows = list(FIXTURE_CHANNELS.values())
        return [projection for row in rows if (projection := _channel_projection(dict(row)))["selectable"]]


__all__ = ["ChannelQrReadService"]
