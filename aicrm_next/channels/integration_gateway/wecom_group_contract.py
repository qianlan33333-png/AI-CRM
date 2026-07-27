from __future__ import annotations

from typing import Any, Protocol


Json = dict[str, Any]


class WeComGroupMessageAdapterContract(Protocol):
    def create_group_message_task(self, payload: dict[str, Any], *, idempotency_key: str = "") -> Json: ...


class WeComGroupChatSyncAdapterContract(Protocol):
    def list_group_chats(self, *, owner_userid: str, limit: int = 100, cursor: str = "") -> Json: ...
    def get_group_chat(self, chat_id: str, *, owner_userid: str = "") -> Json: ...


class WeComGroupAssetAdapterContract(Protocol):
    def list_group_chats(self, *, owner_userid: str, cursor: str = "", limit: int = 100) -> Json: ...
    def get_group_chat(self, *, chat_id: str, need_name: int = 1, owner_userid: str = "") -> Json: ...


class GroupOpsQueueStatsGatewayContract(Protocol):
    def count_group_ops_queue(self) -> int: ...
