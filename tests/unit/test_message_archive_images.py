from __future__ import annotations

import json

from aicrm_next.extensions.archive.message_archive.archive_sdk import (
    extract_archive_record,
    extract_text_record,
)
from aicrm_next.extensions.archive.message_archive.repo import _project_external_chat_record
from aicrm_next.extensions.archive.message_archive.sync_service import execute_archive_sync


def image_payload(*, sdkfileid: str = "sdk-image-1") -> dict:
    return {
        "msgid": "msg-image-1",
        "from": "wm_customer_1",
        "tolist": ["HuangYouCan"],
        "msgtime": 1788336000000,
        "msgtype": "image",
        "image": {
            "sdkfileid": sdkfileid,
            "md5sum": "masked-md5",
            "filesize": 1024,
        },
    }


def test_extract_archive_record_keeps_image_reference_without_binary() -> None:
    row = extract_archive_record(31, {"seq": 31}, image_payload())

    assert row is not None
    assert row["msgtype"] == "image"
    assert row["content"] == ""
    raw = json.loads(row["raw_payload"])
    assert raw["decrypted_message"]["image"]["sdkfileid"] == "sdk-image-1"
    assert extract_text_record(31, {"seq": 31}, image_payload()) is None


def test_extract_archive_record_rejects_image_without_sdkfileid() -> None:
    assert extract_archive_record(31, {"seq": 31}, image_payload(sdkfileid="")) is None


def test_external_chat_projection_exposes_media_id_for_image_only() -> None:
    raw = json.dumps({"decrypted_message": image_payload()}, ensure_ascii=False)

    image = _project_external_chat_record(
        {
            "id": 9,
            "msgid": "msg-image-1",
            "chat_type": "private",
            "owner_userid": "HuangYouCan",
            "sender": "wm_customer_1",
            "receiver": "HuangYouCan",
            "msgtype": "image",
            "content": "",
            "send_time": "2026-09-02 16:00:00",
            "raw_payload": raw,
        }
    )
    text = _project_external_chat_record(
        {
            "id": 10,
            "msgid": "msg-text-1",
            "chat_type": "private",
            "owner_userid": "HuangYouCan",
            "sender": "wm_customer_1",
            "receiver": "HuangYouCan",
            "msgtype": "text",
            "content": "这是什么",
            "send_time": "2026-09-02 16:00:01",
            "raw_payload": "{}",
        }
    )

    assert image["media_id"] == "sdk-image-1"
    assert text["media_id"] == ""


class FakeArchiveClient:
    def __init__(self) -> None:
        self.closed = False

    def fetch_page(self, *, seq: int, limit: int) -> dict:
        if seq >= 31:
            return {"chatdata": []}
        return {"chatdata": [{"seq": 31, "msgid": "msg-image-1"}]}

    def decrypt_records(self, records: list[dict]) -> list[dict]:
        return [image_payload() for _ in records]

    def close(self) -> None:
        self.closed = True


class FakeArchiveRepo:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.last_seq = 0
        self.finished: dict = {}

    def create_sync_run(self, **_: object) -> int:
        return 7

    def finish_sync_run(self, run_id: int, **kwargs: object) -> None:
        self.finished = {"run_id": run_id, **kwargs}

    def get_archive_last_seq(self) -> int:
        return self.last_seq

    def insert_messages_and_advance_seq(self, messages: list[dict], *, last_seq: int) -> int:
        self.messages.extend(messages)
        self.last_seq = last_seq
        return len(messages)


def test_archive_sync_stages_image_and_advances_cursor_together() -> None:
    repo = FakeArchiveRepo()
    client = FakeArchiveClient()

    result = execute_archive_sync(
        owner_userid="HuangYouCan",
        limit=100,
        max_pages=2,
        repo=repo,
        client=client,
    )

    assert result["inserted_count"] == 1
    assert repo.last_seq == 31
    assert repo.messages[0]["msgtype"] == "image"
    assert client.closed is True
