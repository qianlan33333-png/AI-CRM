from __future__ import annotations

import argparse
import json

from aicrm_next.channels.channel_entry import repo


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair channel QR drift. Defaults to dry-run.")
    parser.add_argument("--channel-id", type=int, required=True)
    parser.add_argument("--retire-stale-assets", action="store_true")
    parser.add_argument("--quarantine-scene", default="")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    actions: list[dict] = []
    if args.retire_stale_assets:
        actions.append({"action": "retire_active_qrcode_assets", "channel_id": args.channel_id})
        if args.execute:
            repo.retire_active_qrcode_assets(args.channel_id)
    if args.quarantine_scene:
        actions.append({"action": "quarantine_scene", "scene_value": args.quarantine_scene, "note": "manual SQL repair required for explicit alias reassignment"})

    print(json.dumps({"ok": True, "dry_run": not args.execute, "actions": actions}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
