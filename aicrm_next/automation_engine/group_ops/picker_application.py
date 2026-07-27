from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aicrm_next.integration_ports import WeComGroupAssetAdapterContract
from aicrm_next.platform.shared.errors import ContractError
from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting

from .application import ListGroupOpsGroupsQuery, SyncGroupOpsOwnerGroupsCommand
from .domain import clamp_limit, clean_text
from .dto import GroupChatPickerSyncRequest, GroupOpsGroupSyncRequest, GroupOpsGroupsRequest
from .repo import GroupOpsRepository


def _picker_owner(owner_userid: str = "") -> str:
    return clean_text(owner_userid or managed_runtime_setting("WECOM_DEFAULT_OWNER_USERID"))


def _picker_binding_map() -> dict[str, dict[str, Any]]:
    from aicrm_next.media_library.application import ListMediaItemsQuery

    payload = ListMediaItemsQuery("group_invite")(
        limit=500,
        offset=0,
        filters={"enabled_only": False},
    )
    bindings: dict[str, dict[str, Any]] = {}
    for item in list(payload.get("items") or []):
        chat_id = clean_text(item.get("chat_id") or ((item.get("chat_id_list") or [""])[0]))
        if chat_id and chat_id not in bindings:
            bindings[chat_id] = item
    return bindings


def _picker_freshness(items: list[dict[str, Any]]) -> tuple[str, bool]:
    sync_time = max([clean_text(item.get("synced_at")) for item in items if clean_text(item.get("synced_at"))] or [""])
    if not sync_time:
        return "", False
    try:
        parsed = datetime.fromisoformat(sync_time.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return sync_time, False
    return sync_time, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() <= 300


class ListGroupChatPickerQuery:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, *, owner_userid: str = "", keyword: str = "", limit: int = 200) -> dict[str, Any]:
        owner = _picker_owner(owner_userid)
        if not owner:
            raise ContractError("owner_userid is required and WECOM_DEFAULT_OWNER_USERID is not configured")
        group_payload = ListGroupOpsGroupsQuery(self._repo)(
            GroupOpsGroupsRequest(keyword=keyword, owner_userid=owner, limit=clamp_limit(limit, default=200), offset=0)
        )
        if group_payload.get("ok") is False:
            return group_payload
        bindings = _picker_binding_map()
        items: list[dict[str, Any]] = []
        for group in list(group_payload.get("items") or []):
            binding = bindings.get(clean_text(group.get("chat_id")))
            binding_id = int(binding.get("id") or 0) if binding and str(binding.get("id") or "").isdigit() else 0
            binding_status = clean_text(binding.get("binding_status")) if binding else "pending"
            if binding and (not bool(binding.get("enabled", True)) or binding_status == "invalid"):
                binding_status = "invalid"
            elif binding and (binding_status != "ready" or not clean_text(binding.get("join_url"))):
                binding_status = "pending"
            items.append({**group, "binding_id": binding_id, "binding_status": binding_status or "pending"})
        sync_time, fresh = _picker_freshness(items)
        return {
            **group_payload,
            "items": items,
            "total": len(items),
            "owner_userid": owner,
            "sync_time": sync_time,
            "fresh": fresh,
            "needs_sync": not fresh,
        }


class SyncGroupChatPickerCommand:
    def __init__(self, repo: GroupOpsRepository | None = None, sync_adapter: WeComGroupAssetAdapterContract | None = None) -> None:
        self._repo = repo
        self._sync_adapter = sync_adapter

    def __call__(self, request: GroupChatPickerSyncRequest) -> dict[str, Any]:
        owner = _picker_owner(request.owner_userid)
        if not owner:
            raise ContractError("owner_userid is required and WECOM_DEFAULT_OWNER_USERID is not configured")
        sync = SyncGroupOpsOwnerGroupsCommand(repo=self._repo, sync_adapter=self._sync_adapter)(
            GroupOpsGroupSyncRequest(owner_userid=owner, limit=request.limit, cursor="", operator=request.operator)
        )
        if sync.get("ok") is False:
            return sync
        result = ListGroupChatPickerQuery(self._repo)(owner_userid=owner, keyword=request.keyword, limit=request.limit)
        return {**result, "sync": sync}
