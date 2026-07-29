from __future__ import annotations

import argparse
import json
from typing import Any

from aicrm_next.channels.channel_entry import repo


def _payload(channel_id: int = 0, scene_value: str = "") -> dict[str, Any]:
    channel = repo.get_channel_by_id(channel_id) if channel_id else None
    if not channel and scene_value:
        asset = repo.find_qrcode_asset_by_scene("", scene_value)
        if asset:
            channel = repo.get_channel_by_id(int(asset.get("channel_id") or 0))
    channel_id = int((channel or {}).get("id") or channel_id or 0)
    assets = repo.list_channel_qrcode_assets(channel_id) if channel_id else []
    aliases = repo.list_channel_scene_aliases(channel_id) if channel_id else []
    scene = scene_value or str((channel or {}).get("scene_value") or "")
    return {
        "channel": channel or {},
        "active_qrcode_asset": repo.get_active_qrcode_asset(channel_id) if channel_id else {},
        "qrcode_assets": assets,
        "aliases": aliases,
        "recent_callback_states": repo.list_recent_events(scene, limit=20) if scene else [],
        "recent_effect_logs": repo.list_channel_entry_effect_logs(channel_id=channel_id or None, scene_value=scene, limit=20),
        "suspected_actual_callback_state": scene,
        "recommended_action": "regenerate_qrcode_for_channel" if channel_id and not repo.get_active_qrcode_asset(channel_id) else "inspect_consistency",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose channel QR asset drift.")
    parser.add_argument("--channel-id", type=int, default=0)
    parser.add_argument("--scene-value", default="")
    args = parser.parse_args()
    print(json.dumps(_payload(channel_id=args.channel_id, scene_value=args.scene_value), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
