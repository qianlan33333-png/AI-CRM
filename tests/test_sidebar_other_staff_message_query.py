from __future__ import annotations

from datetime import datetime, timezone
import inspect
from typing import Any

from aicrm_next.crm.customer_read_model.sidebar_v2 import (
    SidebarOtherStaffMessagesReadModel,
    SidebarV2SqlRepository,
)


class _MessageRepository:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    def list_other_staff_messages(
        self,
        external_userid: str,
        *,
        current_userid: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "external_userid": external_userid,
                "current_userid": current_userid,
                "limit": limit,
            }
        )
        return list(self.rows)

    def owner_names(self, userids: set[str]) -> dict[str, str]:
        return {userid: f"name-{userid}" for userid in userids}

    def group_names(self, chat_ids: set[str]) -> dict[str, str]:
        return {chat_id: f"group-{chat_id}" for chat_id in chat_ids}


def _message(
    message_id: int,
    *,
    sender: str,
    msgtype: str,
    minute: int,
) -> dict[str, Any]:
    return {
        "id": message_id,
        "msgid": f"message-{message_id}",
        "chat_type": "private",
        "sender": sender,
        "msgtype": msgtype,
        "content": f"content-{message_id}",
        "send_time": datetime(2026, 7, 25, 4, minute, tzinfo=timezone.utc),
        "raw_payload": {},
    }


def test_other_staff_read_model_reverses_only_the_bounded_latest_page() -> None:
    repository = _MessageRepository(
        [
            _message(3, sender="staff-c", msgtype="text", minute=3),
            _message(2, sender="staff-b", msgtype="image", minute=2),
            _message(1, sender="staff-a", msgtype="text", minute=1),
        ]
    )

    payload = SidebarOtherStaffMessagesReadModel(repository)(
        external_userid="external-customer",
        current_userid="staff-current",
        limit=2,
    )

    assert repository.calls == [
        {
            "external_userid": "external-customer",
            "current_userid": "staff-current",
            "limit": 2,
        }
    ]
    assert [item["id"] for item in payload["messages"]] == ["2", "3"]
    assert [item["type"] for item in payload["messages"]] == ["image", "text"]
    assert payload["has_more"] is True


def test_other_staff_read_model_reports_when_the_bounded_page_is_complete() -> None:
    repository = _MessageRepository(
        [
            _message(2, sender="staff-b", msgtype="text", minute=2),
            _message(1, sender="staff-a", msgtype="text", minute=1),
        ]
    )

    payload = SidebarOtherStaffMessagesReadModel(repository)(
        external_userid="external-customer",
        current_userid="staff-current",
        limit=3,
    )

    assert [item["id"] for item in payload["messages"]] == ["1", "2"]
    assert payload["has_more"] is False


def test_other_staff_message_filtering_and_pagination_stay_in_sql() -> None:
    repository_source = inspect.getsource(
        SidebarV2SqlRepository.list_other_staff_messages
    )
    read_model_source = inspect.getsource(SidebarOtherStaffMessagesReadModel.__call__)

    for required in (
        "message.unionid = :unionid",
        "message.unionid <> ''",
        "message.send_time IS NOT NULL",
        "message.sender <> :requested_external_userid",
        "message.sender <> :canonical_external_userid",
        "message.sender <> :current_userid",
        "message.msgtype IN ('text', 'image')",
        "ORDER BY message.send_time DESC, message.id DESC",
        "LIMIT :limit_plus_one",
    ):
        assert required in repository_source

    assert "limit=200" not in read_model_source
    assert "filtered" not in read_model_source
    assert "reversed(rows[:safe_limit])" in read_model_source
