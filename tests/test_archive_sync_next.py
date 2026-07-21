from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from aicrm_next.admin_jobs_archive_sync_gateway import record_archive_source_change as _record_archive_source_change
from aicrm_next.message_archive.repo import PostgresArchiveSyncRepository
from aicrm_next.message_archive.sync_service import execute_archive_sync
from tests.admin_auth_test_helpers import access_token_headers, install_access_token


class FakeArchiveClient:
    def __init__(self) -> None:
        self.closed = False
        self.fetch_calls: list[dict[str, int]] = []

    def health(self) -> dict:
        return {"ok": True, "mode": "fake-sdk"}

    def fetch_page(self, *, seq: int, limit: int) -> dict:
        self.fetch_calls.append({"seq": seq, "limit": limit})
        return {
            "chatdata": [
                {"seq": 30652, "msgid": "encrypted-1"},
                {"seq": 30653, "msgid": "encrypted-2"},
            ]
        }

    def decrypt_record(self, record: dict) -> dict:
        if int(record["seq"]) == 30653:
            return {"msgid": "image-1", "msgtype": "image", "from": "wm_ext_001", "tolist": ["HuangYouCan"], "msgtime": 1780240000}
        return {
            "msgid": "text-1",
            "msgtype": "text",
            "from": "wm_ext_001",
            "tolist": ["HuangYouCan"],
            "text": {"content": "断点后的第一条"},
            "msgtime": 1780240000,
        }

    def close(self) -> None:
        self.closed = True


class FakeBatchArchiveClient(FakeArchiveClient):
    def __init__(self) -> None:
        super().__init__()
        self.decrypt_batch_sizes: list[int] = []

    def decrypt_records(self, records: list[dict]) -> list[dict]:
        self.decrypt_batch_sizes.append(len(records))
        return [self.decrypt_record(record) for record in records]


class FakeArchiveRepo:
    def __init__(self) -> None:
        self.finished: dict | None = None
        self.inserted: list[dict] = []
        self.last_seq = 30651

    def create_sync_run(self, *, start_time: str, end_time: str, owner_userid: str, cursor: str) -> int:
        assert owner_userid == "HuangYouCan"
        assert cursor == ""
        return 42

    def finish_sync_run(self, run_id: int, *, status: str, fetched_count: int, inserted_count: int, raw_response=None, error_message: str = "") -> None:
        self.finished = {
            "run_id": run_id,
            "status": status,
            "fetched_count": fetched_count,
            "inserted_count": inserted_count,
            "raw_response": raw_response,
            "error_message": error_message,
        }

    def get_archive_last_seq(self) -> int:
        return self.last_seq

    def insert_messages_and_advance_seq(self, messages: list[dict], *, last_seq: int) -> int:
        self.last_seq = last_seq
        self.inserted.extend(messages)
        return len(messages)


class _FakeCursor:
    def __init__(self, row=None) -> None:
        self._row = row

    def fetchone(self):
        return self._row


class _FakeArchiveConnection:
    def __init__(self, *, insert_row: dict | None = None) -> None:
        self.insert_row = insert_row
        self.executed: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append((sql, params))
        if "INSERT INTO archived_messages" in sql:
            return _FakeCursor(self.insert_row)
        return _FakeCursor()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_execute_archive_sync_fetches_from_last_seq_and_only_persists_archive(monkeypatch) -> None:
    monkeypatch.setenv("WECOM_DEFAULT_OWNER_USERID", "HuangYouCan")
    repo = FakeArchiveRepo()
    client = FakeArchiveClient()

    result = execute_archive_sync(repo=repo, client=client, owner_userid="HuangYouCan", limit=100)

    assert result["ok"] is True
    assert result["fetched_count"] == 2
    assert result["accepted_count"] == 1
    assert result["inserted_count"] == 1
    assert result["last_seq"] == 30653
    assert result["reply_monitor_skipped"] is True
    assert result["contacts_sync_skipped"] is True
    assert repo.inserted[0]["msgid"] == "text-1"
    assert repo.last_seq == 30653
    assert repo.finished and repo.finished["status"] == "success"
    assert client.fetch_calls == [{"seq": 30651, "limit": 100}]
    assert client.closed is True


def test_execute_archive_sync_uses_batch_native_decrypt_boundary_when_available() -> None:
    repo = FakeArchiveRepo()
    client = FakeBatchArchiveClient()

    result = execute_archive_sync(repo=repo, client=client, owner_userid="HuangYouCan", limit=100)

    assert result["ok"] is True
    assert client.decrypt_batch_sizes == [2]


def test_archive_insert_and_refresh_event_share_one_transaction(monkeypatch) -> None:
    connection = _FakeArchiveConnection(insert_row={"id": 91})
    captured: dict = {}

    def fake_enqueue(conn, request):
        captured["connection"] = conn
        captured["request"] = request
        return {"id": 44}

    monkeypatch.setattr(
        "aicrm_next.platform_foundation.internal_events.outbox.enqueue_transactional_internal_event_outbox",
        fake_enqueue,
    )
    repository = PostgresArchiveSyncRepository(
        "postgresql://example.invalid/aicrm",
        source_change_recorder=_record_archive_source_change,
    )
    monkeypatch.setattr(repository, "_connect", lambda: connection)

    inserted = repository.insert_messages_and_advance_seq(
        [
            {
                "seq": 30654,
                "msgid": "archive-message-30654",
                "chat_type": "private",
                "unionid": "union-test",
                "owner_userid": "HuangYouCan",
                "sender": "union-test",
                "receiver": "HuangYouCan",
                "msgtype": "text",
                "content": "must-not-enter-event",
                "send_time": "2026-07-18T16:00:00+00:00",
                "raw_payload": "must-not-enter-event",
            }
        ],
        last_seq=30654,
    )

    request = captured["request"]
    assert inserted == 1
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert captured["connection"] is connection
    assert request.event_type == "message_archive.batch_ingested"
    expected_batch_key = hashlib.sha256(b"91").hexdigest()
    assert request.idempotency_key == f"message_archive.batch_ingested:{expected_batch_key}"
    assert request.aggregate_id == expected_batch_key
    assert request.payload == {"inserted_count": 1, "last_seq": 30654}
    assert "must-not-enter-event" not in str(request)


def test_archive_replay_at_same_last_seq_uses_inserted_rows_for_fresh_dirty_key(monkeypatch) -> None:
    observed: list[tuple[int, int, str]] = []

    def record(_conn, inserted_count: int, last_seq: int, batch_key: str):
        observed.append((inserted_count, last_seq, batch_key))
        return {"ok": True}

    for row_id in (101, 102):
        connection = _FakeArchiveConnection(insert_row={"id": row_id})
        repository = PostgresArchiveSyncRepository(
            "postgresql://example.invalid/aicrm",
            source_change_recorder=record,
        )
        monkeypatch.setattr(repository, "_connect", lambda connection=connection: connection)
        assert repository.insert_messages_and_advance_seq(
            [{"seq": 30654, "msgid": f"archive-message-{row_id}", "unionid": "union-test"}],
            last_seq=30654,
        ) == 1

    assert [item[:2] for item in observed] == [(1, 30654), (1, 30654)]
    assert observed[0][2] != observed[1][2]


def test_archive_insert_rolls_back_when_refresh_event_cannot_be_persisted(monkeypatch) -> None:
    connection = _FakeArchiveConnection(insert_row={"id": 92})

    def fail_enqueue(_conn, _request):
        raise RuntimeError("outbox_unavailable")

    monkeypatch.setattr(
        "aicrm_next.platform_foundation.internal_events.outbox.enqueue_transactional_internal_event_outbox",
        fail_enqueue,
    )
    repository = PostgresArchiveSyncRepository(
        "postgresql://example.invalid/aicrm",
        source_change_recorder=_record_archive_source_change,
    )
    monkeypatch.setattr(repository, "_connect", lambda: connection)

    with pytest.raises(RuntimeError, match="outbox_unavailable"):
        repository.insert_messages_and_advance_seq(
            [
                {
                    "seq": 30655,
                    "msgid": "archive-message-30655",
                    "unionid": "union-test",
                }
            ],
            last_seq=30655,
        )

    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_archive_insert_fails_closed_without_source_change_recorder(monkeypatch) -> None:
    connection = _FakeArchiveConnection(insert_row={"id": 93})
    repository = PostgresArchiveSyncRepository("postgresql://example.invalid/aicrm")
    monkeypatch.setattr(repository, "_connect", lambda: connection)

    with pytest.raises(RuntimeError, match="archive_source_change_recorder_required"):
        repository.insert_messages_and_advance_seq(
            [{"seq": 30656, "msgid": "archive-message-30656", "unionid": "union-test"}],
            last_seq=30656,
        )

    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_archive_sync_route_requires_registered_archive_client(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from aicrm_next.main import create_app

    client = TestClient(create_app(), raise_server_exceptions=False)

    missing = client.post("/api/archive/sync", json={"owner_userid": "HuangYouCan"})
    assert missing.status_code == 401
    assert missing.json()["error"] == "access_token_required"


def test_archive_sync_route_passes_archive_request_without_reply_queue(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    monkeypatch.setenv("AICRM_ENABLE_IN_PROCESS_ARCHIVE_SYNC", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    captured: dict = {}

    def fake_execute(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "fetched_count": 1,
            "inserted_count": 1,
            "last_seq": 30652,
            "reply_monitor_skipped": True,
            "route_owner": "ai_crm_next",
            "source_status": "next_archive_sync",
        }

    monkeypatch.setattr("aicrm_next.message_archive.api.execute_archive_sync", fake_execute)
    from aicrm_next.main import create_app

    client = TestClient(create_app(), raise_server_exceptions=False)
    token = install_access_token(
        client,
        audience="internal_worker",
        capabilities=("archive_execute",),
        scopes=("write",),
        client_id="pytest-archive-worker",
        purpose="archive",
    )
    response = client.post(
        "/api/archive/sync",
        json={"owner_userid": "HuangYouCan", "cursor": "30651", "limit": 50, "max_pages": 2},
        headers=access_token_headers(token),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_status"] == "next_archive_sync"
    assert payload["reply_monitor_skipped"] is True
    assert captured["owner_userid"] == "HuangYouCan"
    assert captured["cursor"] == "30651"
    assert captured["limit"] == 50
    assert captured["max_pages"] == 2


def test_archive_sync_route_defaults_to_runner_only(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    monkeypatch.delenv("AICRM_ENABLE_IN_PROCESS_ARCHIVE_SYNC", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from aicrm_next.main import create_app

    client = TestClient(create_app(), raise_server_exceptions=False)
    token = install_access_token(
        client,
        audience="internal_worker",
        capabilities=("archive_execute",),
        scopes=("write",),
        client_id="pytest-archive-worker",
        purpose="archive",
    )
    response = client.post(
        "/api/archive/sync",
        json={"owner_userid": "HuangYouCan", "cursor": "30651"},
        headers=access_token_headers(token),
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["error_code"] == "in_process_archive_sync_disabled"
    assert payload["runner"] == "scripts/run_incremental_archive_sync.py"
    assert payload["reply_monitor_skipped"] is True
