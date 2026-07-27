from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from aicrm_next import common_operation_members
from aicrm_next.channels.integration_gateway.wecom_operation_members_client import (
    WeComOperationMembersClient,
    WeComOperationMembersClientError,
)
from aicrm_next.main import create_app
from aicrm_next.crm.operation_members.application import SyncOperationMembersFromWeComCommand
from aicrm_next.crm.operation_members.repository import OperationMemberDirectoryRepository
from aicrm_next.shared.db_session import reset_engine_cache_for_tests


ROOT = Path(__file__).resolve().parents[1]


def _directory_engine(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'operation_members.sqlite3'}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE admin_wecom_directory_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    corp_id TEXT NOT NULL DEFAULT '',
                    wecom_userid TEXT NOT NULL DEFAULT '',
                    display_name TEXT NOT NULL DEFAULT '',
                    department_ids_json TEXT NOT NULL DEFAULT '[]',
                    department_name TEXT NOT NULL DEFAULT '',
                    position TEXT NOT NULL DEFAULT '',
                    mobile TEXT NOT NULL DEFAULT '',
                    avatar_url TEXT NOT NULL DEFAULT '',
                    wecom_status TEXT NOT NULL DEFAULT '',
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    raw_payload_json TEXT NOT NULL DEFAULT '{}',
                    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_by TEXT NOT NULL DEFAULT '',
                    UNIQUE(corp_id, wecom_userid)
                )
                """
            )
        )
    return engine


def test_wecom_operation_members_client_fetches_follow_users_and_user_profiles() -> None:
    calls: list[dict[str, Any]] = []

    def http_get(url: str, *, params: dict[str, Any], timeout: int):
        calls.append({"url": url, "params": dict(params), "timeout": timeout})
        if url.endswith("/cgi-bin/gettoken"):
            assert params == {"corpid": "ww-test", "corpsecret": "contact-secret"}
            return {"errcode": 0, "access_token": "token-001", "expires_in": 7200}
        if url.endswith("/cgi-bin/externalcontact/get_follow_user_list"):
            assert params == {"access_token": "token-001"}
            return {"errcode": 0, "follow_user": ["sales_01", "sales_02"]}
        if url.endswith("/cgi-bin/user/get") and params["userid"] == "sales_01":
            return {
                "errcode": 0,
                "userid": "sales_01",
                "name": "客服一",
                "department": [1, 3],
                "position": "顾问",
                "avatar": "https://example.test/avatar.png",
                "status": 1,
            }
        if url.endswith("/cgi-bin/user/get") and params["userid"] == "sales_02":
            return {"errcode": 60111, "errmsg": "no permission", "userid": "sales_02"}
        raise AssertionError(url)

    client = WeComOperationMembersClient(
        corp_id="ww-test",
        secret="contact-secret",
        api_base="https://qyapi.example.test",
        http_get=http_get,
    )

    members = client.list_operation_members()

    assert members[0]["wecom_userid"] == "sales_01"
    assert members[0]["display_name"] == "客服一"
    assert members[0]["department_ids"] == [1, 3]
    assert members[0]["avatar_url"] == "https://example.test/avatar.png"
    assert members[1]["wecom_userid"] == "sales_02"
    assert members[1]["display_name"] == "sales_02"
    assert [call["url"].rsplit("/", 1)[-1] for call in calls] == [
        "gettoken",
        "get_follow_user_list",
        "get",
        "get",
    ]


def test_wecom_operation_members_client_requires_real_wecom_config() -> None:
    client = WeComOperationMembersClient(corp_id="", secret="", http_get=lambda *args, **kwargs: {})

    with pytest.raises(WeComOperationMembersClientError) as exc:
        client.list_follow_userids()

    assert exc.value.error_code == "wecom_operation_members_config_missing"
    assert exc.value.stage == "config"


def test_sync_command_writes_wecom_members_and_deactivates_missing_rows(tmp_path) -> None:
    engine = _directory_engine(tmp_path)
    repo = OperationMemberDirectoryRepository(engine=engine)

    class FakeClient:
        corp_id = "ww-sync"

        def __init__(self) -> None:
            self.calls = 0

        def list_operation_members(self) -> list[dict[str, Any]]:
            self.calls += 1
            if self.calls == 1:
                return [
                    {"wecom_userid": "sales_01", "display_name": "客服一", "is_active": True, "raw_payload": {"round": 1}},
                    {"wecom_userid": "sales_02", "display_name": "客服二", "is_active": True, "raw_payload": {"round": 1}},
                ]
            return [
                {"wecom_userid": "sales_02", "display_name": "客服二改名", "is_active": True, "raw_payload": {"round": 2}},
            ]

    fake_client = FakeClient()
    command = SyncOperationMembersFromWeComCommand(client=fake_client, repo=repo)  # type: ignore[arg-type]

    first = command.execute(operator="tester")
    second = command.execute(operator="tester")

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT wecom_userid, display_name, is_active, updated_by
                FROM admin_wecom_directory_members
                ORDER BY wecom_userid
                """
            )
        ).mappings().all()

    assert first["real_external_call_executed"] is True
    assert first["synced_count"] == 2
    assert second["synced_count"] == 1
    assert [dict(row) for row in rows] == [
        {"wecom_userid": "sales_01", "display_name": "客服一", "is_active": False, "updated_by": "tester"},
        {"wecom_userid": "sales_02", "display_name": "客服二改名", "is_active": True, "updated_by": "tester"},
    ]


def test_operation_member_sync_api_reports_real_wecom_refresh(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "operation-member-sync-api-test")
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_engine_cache_for_tests()

    class FakeCommand:
        def execute(self, *, operator: str = "") -> dict[str, Any]:
            return {
                "ok": True,
                "operator": operator,
                "real_external_call_executed": True,
                "synced_count": 2,
            }

    monkeypatch.setattr(common_operation_members, "SyncOperationMembersFromWeComCommand", lambda: FakeCommand())

    response = TestClient(create_app(), raise_server_exceptions=False).post(
        "/api/admin/common/operation-members/sync",
        headers={"X-Admin-User": "tester"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "operator": "tester",
        "real_external_call_executed": True,
        "synced_count": 2,
    }


def test_operation_member_sync_route_registered_in_manifest() -> None:
    source = (ROOT / "docs" / "architecture" / "route_ownership_manifest.yml").read_text(encoding="utf-8")

    assert "/api/admin/common/operation-members/sync" in source
    assert "api_operation_members_sync" in source
    assert "real_requires_approval" in source
