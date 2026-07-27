from __future__ import annotations

from typing import Any

from aicrm_next.integration_ports import (
    WeComOperationMembersClient,
    build_wecom_operation_members_client,
)
from aicrm_next.crm.operation_members.repository import OperationMemberDirectoryRepository


class SyncOperationMembersFromWeComCommand:
    def __init__(
        self,
        *,
        client: WeComOperationMembersClient | None = None,
        repo: OperationMemberDirectoryRepository | None = None,
    ) -> None:
        self.client = client or build_wecom_operation_members_client()
        self.repo = repo or OperationMemberDirectoryRepository()

    def execute(self, *, operator: str = "") -> dict[str, Any]:
        members = self.client.list_operation_members()
        result = self.repo.replace_wecom_directory_members(
            corp_id=self.client.corp_id or "default",
            members=members,
            operator=operator or "operation_member_sync",
        )
        return {
            "ok": True,
            "source": "wecom_externalcontact_follow_user_list",
            "real_external_call_executed": True,
            "synced_count": int(result.get("synced_count") or 0),
            "active_userids": list(result.get("active_userids") or []),
        }
