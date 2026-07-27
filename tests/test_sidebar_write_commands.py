from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.crm.sidebar_write import get_sidebar_write_audit_events
from tests.sidebar_auth_test_helpers import install_sidebar_auth


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    return TestClient(create_app())


@pytest.mark.parametrize(
    ("method", "path", "payload", "expected_command", "expected_write_status"),
    [
        (
            "post",
            "/api/sidebar/bind-mobile",
            {"external_userid": "wx_ext_002", "mobile": "13800138123"},
            "sidebar.bind_mobile",
            "updated",
        ),
        (
            "post",
            "/api/sidebar/lead-pool/upsert-class-term",
            {"external_userid": "wx_ext_001", "class_term": "term-2026-06", "status": "active"},
            "sidebar.upsert_lead_pool_class_term",
            "updated",
        ),
        (
            "post",
            "/api/sidebar/signup-tags/mark",
            {"external_userid": "wx_ext_001", "tag_name": "trial-active", "marked": True},
            "sidebar.mark_signup_tag",
            "updated",
        ),
        (
            "post",
            "/api/sidebar/marketing-status/set-followup-segment",
            {"external_userid": "wx_ext_001", "segment": "high_intent"},
            "sidebar.set_followup_segment",
            "updated",
        ),
        (
            "post",
            "/api/sidebar/marketing-status/mark-enrolled",
            {"external_userid": "wx_ext_001"},
            "sidebar.mark_enrolled",
            "updated",
        ),
        (
            "post",
            "/api/sidebar/marketing-status/unmark-enrolled",
            {"external_userid": "wx_ext_001"},
            "sidebar.unmark_enrolled",
            "updated",
        ),
        (
            "put",
            "/api/sidebar/v2/profile",
            {"external_userid": "wx_ext_001", "remark": "followup ready"},
            "sidebar.update_profile",
            "updated",
        ),
        (
            "post",
            "/api/sidebar/v2/materials/send",
            {"external_userid": "wx_ext_001", "material_id": "mat-001"},
            "sidebar.plan_material_send",
            "planned",
        ),
    ],
)
def test_sidebar_write_routes_execute_next_commandbus(
    client: TestClient,
    method: str,
    path: str,
    payload: dict,
    expected_command: str,
    expected_write_status: str,
) -> None:
    external_userid = str(payload.get("external_userid") or "")
    viewer_userid = "LiuXiao" if external_userid == "wx_ext_002" else "ZhaoYanFang"
    headers = install_sidebar_auth(
        client,
        viewer_userid=viewer_userid,
        external_userid=external_userid,
    )
    headers["Idempotency-Key"] = f"test-{path}"
    response = getattr(client, method)(path, json=payload, headers=headers)

    assert response.status_code == 200, response.text
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    body = response.json()
    assert body["ok"] is True
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["source_status"] == "next_command"
    assert body["command_name"] == expected_command
    assert body["write_model_status"] == expected_write_status
    assert body["audit_recorded"] is True
    assert body["real_external_call_executed"] is False
    assert body["command_id"]

    audit_events = get_sidebar_write_audit_events()
    assert any(event["command_id"] == body["command_id"] for event in audit_events)


def test_sidebar_write_routes_return_controlled_errors(client: TestClient) -> None:
    headers = install_sidebar_auth(
        client,
        viewer_userid="ZhaoYanFang",
        external_userid="wx_ext_001",
    )
    missing_external = client.post(
        "/api/sidebar/bind-mobile",
        json={"mobile": "13800138123"},
        headers=headers,
    )
    assert missing_external.status_code == 400
    assert missing_external.json()["source_status"] == "input_error"
    assert missing_external.json()["fallback_used"] is False

    missing_payload = client.post(
        "/api/sidebar/bind-mobile",
        json={"external_userid": "wx_ext_001"},
        headers=headers,
    )
    assert missing_payload.status_code == 400
    assert missing_payload.json()["source_status"] == "input_error"

    unknown_headers = install_sidebar_auth(
        client,
        viewer_userid="ZhaoYanFang",
        external_userid="wx_ext_missing",
    )
    unknown_customer = client.post(
        "/api/sidebar/bind-mobile",
        json={"external_userid": "wx_ext_missing", "mobile": "13800138123"},
        headers=unknown_headers,
    )
    assert unknown_customer.status_code == 403
    assert unknown_customer.json()["source_status"] == "forbidden"
    assert unknown_customer.json()["fallback_used"] is False


def test_sidebar_write_routes_filter_by_owner_userid(client: TestClient) -> None:
    headers = install_sidebar_auth(
        client,
        viewer_userid="ZhaoYanFang",
        external_userid="wx_ext_001",
    )
    allowed = client.post(
        "/api/sidebar/lead-pool/upsert-class-term",
        json={
            "external_userid": "wx_ext_001",
            "owner_userid": "ZhaoYanFang",
            "class_term": "term-2026-06",
            "status": "active",
        },
        headers=headers,
    )
    blocked = client.post(
        "/api/sidebar/lead-pool/upsert-class-term",
        json={
            "external_userid": "wx_ext_001",
            "owner_userid": "LiuXiao",
            "class_term": "term-2026-06",
            "status": "active",
        },
        headers=headers,
    )

    assert allowed.status_code == 200
    assert blocked.status_code == 403
    assert blocked.json()["source_status"] == "forbidden"
    assert blocked.json()["fallback_used"] is False


def test_sidebar_write_production_unavailable_does_not_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://sidebar-write:sidebar-write@127.0.0.1:1/aicrm_sidebar")
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "sidebar-write-production-unavailable")

    client = TestClient(create_app())
    headers = install_sidebar_auth(
        client,
        viewer_userid="ZhaoYanFang",
        external_userid="wx_ext_001",
    )
    response = client.post(
        "/api/sidebar/bind-mobile",
        json={"external_userid": "wx_ext_001", "mobile": "13800138123"},
        headers=headers,
    )

    assert response.status_code == 503
    body = response.json()
    assert body["source_status"] == "production_unavailable"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False


def test_sidebar_bind_mobile_executes_postgres_binding_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://sidebar-write:sidebar-write@127.0.0.1:5432/aicrm_sidebar")
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "sidebar-write-postgres-binding")

    class FakeResult:
        def __init__(self, row=None, rows=None):
            self._row = row
            self._rows = list(rows or ([] if row is None else [row]))

        def fetchone(self):
            return self._row

        def fetchall(self):
            return self._rows

    class FakeConnection:
        def __init__(self):
            self.identities = {
                "wm_prod_sidebar_001": {
                    "unionid": "union_prod_sidebar_001",
                    "primary_external_userid": "wm_prod_sidebar_001",
                    "external_userids_json": [{"external_userid": "wm_prod_sidebar_001"}],
                    "mobile": "13800138123",
                    "mobile_normalized": "13800138123",
                    "mobile_source": "legacy_migration",
                    "customer_name": "生产侧边栏客户",
                    "primary_owner_userid": "sales_09",
                    "remark": "侧边栏",
                    "created_at": "2026-06-01T00:00:00Z",
                    "updated_at": "2026-06-01T00:00:00Z",
                }
            }
            self.commits = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            compact_sql = " ".join(sql.split())
            if compact_sql.startswith("SELECT DISTINCT owner_userid"):
                return FakeResult(rows=[{"owner_userid": "sales_09"}])
            if "FROM crm_user_identity" in compact_sql:
                return FakeResult(self.identities.get(params[0]))
            if "FROM wecom_external_contact_follow_users" in compact_sql:
                return FakeResult(rows=[{"owner_userid": "sales_09"}])
            if compact_sql.startswith("UPDATE crm_user_identity"):
                assert "'sidebar_bind_by_userid', %s::text" in compact_sql
                assert "'sidebar_external_userid', %s::text" in compact_sql
                row = next(item for item in self.identities.values() if item["unionid"] == params[5])
                row.update(
                    {
                        "mobile": params[0],
                        "mobile_normalized": params[1],
                        "mobile_source": "sidebar_bind",
                        "primary_owner_userid": params[2] or row["primary_owner_userid"],
                        "updated_at": "2026-06-10T13:25:00Z",
                    }
                )
                return FakeResult(row)
            raise AssertionError(f"unexpected SQL: {compact_sql}")

        def commit(self):
            self.commits += 1

    connections: list[FakeConnection] = []

    def connect(*_args, **_kwargs):
        connection = FakeConnection()
        connections.append(connection)
        return connection

    psycopg = types.ModuleType("psycopg")
    psycopg.connect = connect
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)

    client = TestClient(create_app())
    headers = install_sidebar_auth(
        client,
        viewer_userid="sales_09",
        external_userid="wm_prod_sidebar_001",
    )
    response = client.post(
        "/api/sidebar/bind-mobile",
        json={
            "external_userid": "wm_prod_sidebar_001",
            "owner_userid": "sales_09",
            "bind_by_userid": "sales_09",
            "mobile": "17380533527",
            "force_rebind": True,
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["source_status"] == "next_command"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
    assert body["binding"]["mobile"] == "17380533527"
    assert body["binding"]["unionid"] == "union_prod_sidebar_001"
    assert body["binding"]["owner_userid"] == "sales_09"
    assert body["lead_pool_merge"]["action_type"] == "customer_mobile_bound_event"
    assert "production-ready for command execution" not in response.text
    assert sum(connection.commits for connection in connections) == 1


def test_sidebar_bind_mobile_allows_active_follow_user_owner_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://sidebar-write:sidebar-write@127.0.0.1:5432/aicrm_sidebar")
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "sidebar-write-active-follow")

    class FakeResult:
        def __init__(self, row=None, rows=None):
            self._row = row
            self._rows = list(rows or ([] if row is None else [row]))

        def fetchone(self):
            return self._row

        def fetchall(self):
            return self._rows

    class FakeConnection:
        def __init__(self):
            self.identity = {
                "unionid": "union_meixin",
                "primary_external_userid": "wmbNXyCwAA48u3o0zHSMvMrGAHbBrHxw",
                "external_userids_json": ["wmbNXyCwAA48u3o0zHSMvMrGAHbBrHxw"],
                "mobile": "",
                "mobile_normalized": "",
                "mobile_source": "",
                "customer_name": "美心",
                "primary_owner_userid": "ZhaoYanFang",
                "remark": "",
                "created_at": "2026-07-03T15:22:55+08:00",
                "updated_at": "2026-07-03T15:22:55+08:00",
            }
            self.commits = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            compact_sql = " ".join(sql.split())
            if compact_sql.startswith("SELECT DISTINCT owner_userid"):
                return FakeResult(
                    rows=[
                        {"owner_userid": "HuangYouCan"},
                        {"owner_userid": "ZhaoYanFang"},
                    ]
                )
            if "FROM crm_user_identity" in compact_sql:
                return FakeResult(self.identity)
            if "FROM wecom_external_contact_follow_users" in compact_sql:
                return FakeResult(
                    rows=[
                        {"owner_userid": "HuangYouCan"},
                        {"owner_userid": "ZhaoYanFang"},
                    ]
                )
            if compact_sql.startswith("UPDATE crm_user_identity"):
                assert "'sidebar_bind_by_userid', %s::text" in compact_sql
                assert "'sidebar_external_userid', %s::text" in compact_sql
                self.identity.update(
                    {
                        "mobile": params[0],
                        "mobile_normalized": params[1],
                        "mobile_source": "sidebar_bind",
                        "primary_owner_userid": params[2] or self.identity["primary_owner_userid"],
                        "updated_at": "2026-07-06T21:55:00+08:00",
                    }
                )
                return FakeResult(self.identity)
            raise AssertionError(f"unexpected SQL: {compact_sql}")

        def commit(self):
            self.commits += 1

    connections: list[FakeConnection] = []

    def connect(*_args, **_kwargs):
        connection = FakeConnection()
        connections.append(connection)
        return connection

    psycopg = types.ModuleType("psycopg")
    psycopg.connect = connect
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)

    client = TestClient(create_app())
    headers = install_sidebar_auth(
        client,
        viewer_userid="HuangYouCan",
        external_userid="wmbNXyCwAA48u3o0zHSMvMrGAHbBrHxw",
    )
    response = client.post(
        "/api/sidebar/bind-mobile",
        json={
            "external_userid": "wmbNXyCwAA48u3o0zHSMvMrGAHbBrHxw",
            "owner_userid": "HuangYouCan",
            "bind_by_userid": "HuangYouCan",
            "mobile": "18826079430",
            "force_rebind": True,
        },
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["binding"]["mobile"] == "18826079430"
    assert body["binding"]["owner_userid"] == "HuangYouCan"
    assert body["binding"]["unionid"] == "union_meixin"
    assert body["write_model_status"] == "updated"
    assert sum(connection.commits for connection in connections) == 1


def test_sidebar_profile_fields_execute_postgres_projection_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://sidebar-write:sidebar-write@127.0.0.1:5432/aicrm_sidebar")
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "sidebar-write-profile-fields")

    class FakeResult:
        def __init__(self, row=None, rows=None):
            self._row = row
            self._rows = list(rows or ([] if row is None else [row]))

        def fetchone(self):
            return self._row

        def fetchall(self):
            return self._rows

    class FakeConnection:
        def __init__(self):
            self.identity = {
                "unionid": "union_prod_sidebar_001",
                "primary_external_userid": "wm_prod_sidebar_001",
                "external_userids_json": ["wm_prod_sidebar_001"],
                "mobile": "13800138123",
                "primary_owner_userid": "sales_09",
                "customer_name": "生产侧边栏客户",
                "remark": "",
                "created_at": "2026-06-01T00:00:00Z",
                "updated_at": "2026-06-01T00:00:00Z",
            }
            self.profile_fields = {}
            self.commits = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            compact_sql = " ".join(sql.split())
            if compact_sql.startswith("SELECT DISTINCT owner_userid"):
                return FakeResult(rows=[{"owner_userid": "sales_09"}])
            if "FROM crm_user_identity" in compact_sql:
                return FakeResult(self.identity)
            if "FROM wecom_external_contact_follow_users" in compact_sql:
                return FakeResult(rows=[{"owner_userid": "sales_09"}])
            if compact_sql.startswith("INSERT INTO sidebar_customer_profile_fields"):
                self.profile_fields = {
                    "source": params[1],
                    "industry": params[2],
                    "industry_description": params[3],
                    "needs_blockers_followup": params[4],
                    "updated_by": params[5],
                    "updated_at": "2026-07-08T12:00:00+08:00",
                }
                return FakeResult(self.profile_fields)
            raise AssertionError(f"unexpected SQL: {compact_sql}")

        def commit(self):
            self.commits += 1

    connections: list[FakeConnection] = []

    def connect(*_args, **_kwargs):
        connection = FakeConnection()
        connections.append(connection)
        return connection

    psycopg = types.ModuleType("psycopg")
    psycopg.connect = connect
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)

    client = TestClient(create_app())
    headers = install_sidebar_auth(
        client,
        viewer_userid="sales_09",
        external_userid="wm_prod_sidebar_001",
    )
    response = client.put(
        "/api/sidebar/v2/profile",
        json={
            "external_userid": "wm_prod_sidebar_001",
            "owner_userid": "sales_09",
            "source": "朋友圈投放",
            "industry": "教育培训",
            "industry_description": "AI 课程咨询",
            "needs_blockers_followup": "需要补发体验课资料",
            "updated_by": "sales_09",
        },
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["write_model_status"] == "updated"
    assert body["real_external_call_executed"] is False
    assert body["side_effect_plan"]["adapter_mode"] == "real_blocked"
    assert body["write"]["changes"]["profile_fields"]["industry"] == "教育培训"
    assert "production-ready for command execution" not in response.text
    write_connection = next(connection for connection in connections if connection.profile_fields)
    assert write_connection.profile_fields["needs_blockers_followup"] == "需要补发体验课资料"
    assert sum(connection.commits for connection in connections) == 1


def test_sidebar_material_send_uses_postgres_plan_and_cached_media_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://sidebar-write:sidebar-write@127.0.0.1:5432/aicrm_sidebar")
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "sidebar-write-material-send")

    class FakeResult:
        def __init__(self, row=None, rows=None):
            self._row = row
            self._rows = list(rows or ([] if row is None else [row]))

        def fetchone(self):
            return self._row

        def fetchall(self):
            return self._rows

    class FakeConnection:
        def __init__(self):
            self.identity = {
                "unionid": "union_prod_sidebar_001",
                "primary_external_userid": "wm_prod_sidebar_001",
                "external_userids_json": ["wm_prod_sidebar_001"],
                "mobile": "13800138123",
                "primary_owner_userid": "sales_09",
                "customer_name": "生产侧边栏客户",
                "remark": "",
                "created_at": "2026-06-01T00:00:00Z",
                "updated_at": "2026-06-01T00:00:00Z",
            }
            self.material_plan_json = ""
            self.commits = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            compact_sql = " ".join(sql.split())
            if compact_sql.startswith("SELECT DISTINCT owner_userid"):
                return FakeResult(rows=[{"owner_userid": "sales_09"}])
            if "FROM crm_user_identity" in compact_sql:
                return FakeResult(self.identity)
            if "FROM wecom_external_contact_follow_users" in compact_sql:
                return FakeResult(rows=[{"owner_userid": "sales_09"}])
            if "FROM image_library" in compact_sql:
                assert params == (42,)
                return FakeResult({"id": 42, "title": "AI 分享海报", "media_id": "media-real-image-001"})
            if compact_sql.startswith("UPDATE crm_user_identity"):
                assert "last_material_send_plan" in compact_sql
                self.material_plan_json = params[0]
                return FakeResult({"updated_at": "2026-07-08T12:05:00+08:00"})
            raise AssertionError(f"unexpected SQL: {compact_sql}")

        def commit(self):
            self.commits += 1

    connections: list[FakeConnection] = []

    def connect(*_args, **_kwargs):
        connection = FakeConnection()
        connections.append(connection)
        return connection

    psycopg = types.ModuleType("psycopg")
    psycopg.connect = connect
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)

    client = TestClient(create_app())
    headers = install_sidebar_auth(
        client,
        viewer_userid="sales_09",
        external_userid="wm_prod_sidebar_001",
    )
    response = client.post(
        "/api/sidebar/v2/materials/send",
        json={
            "external_userid": "wm_prod_sidebar_001",
            "owner_userid": "sales_09",
            "type": "image",
            "material_id": "42",
            "operator": "sales_09",
            "delivery_mode": "chat_toolbar",
        },
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["write_model_status"] == "planned"
    assert body["media_id"] == "media-real-image-001"
    assert body["real_external_call_executed"] is False
    assert body["side_effect_plan"]["adapter_mode"] == "real_blocked"
    assert body["side_effect_plan"]["real_external_call_executed"] is False
    write_connection = next(connection for connection in connections if connection.material_plan_json)
    assert '"material_id": "42"' in write_connection.material_plan_json
    assert '"media_id": "media-real-image-001"' in write_connection.material_plan_json
    assert "production-ready for command execution" not in response.text
    assert sum(connection.commits for connection in connections) == 1
