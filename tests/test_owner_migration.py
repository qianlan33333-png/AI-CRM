from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.crm.owner_migration.application import OwnerMigrationCommand, OwnerMigrationService
from aicrm_next.crm.owner_migration.repo import PostgresOwnerMigrationRepository


class FakeOwnerMigrationRepository:
    source_status = "fake"

    def __init__(self) -> None:
        self.executed = False

    def preview_owner_migration(self, *, source_owner_userid: str, target_owner_userid: str, external_userids: list[str] | None = None) -> dict:
        all_external_userids = ["wm_ext_1", "wm_ext_2"]
        if external_userids is not None:
            allowed = set(external_userids)
            all_external_userids = [item for item in all_external_userids if item in allowed]
        return {
            "source_status": self.source_status,
            "candidate_count": len(all_external_userids),
            "all_external_userids": all_external_userids,
            "sample_external_userids": all_external_userids,
            "surface_counts": {"contacts": len(all_external_userids)},
            "pending_review": {"pending_user_ops_deferred_jobs": 1},
        }

    def execute_owner_migration(
        self,
        *,
        source_owner_userid: str,
        target_owner_userid: str,
        operator: str,
        external_userids: list[str] | None = None,
        target_owner_display_name: str | None = None,
    ) -> dict:
        del target_owner_display_name
        self.executed = True
        return {
            **self.preview_owner_migration(
                source_owner_userid=source_owner_userid,
                target_owner_userid=target_owner_userid,
            ),
            "executed": True,
            "touched_count": 2,
            "update_counts": {"contacts": 2},
            "touched_external_userids": external_userids or ["wm_ext_1", "wm_ext_2"],
        }

    def resolve_operation_members(self, userids: list[str]) -> dict:
        return {
            userid: {"user_id": userid, "display_name": userid.title(), "status": "active"}
            for userid in userids
        }

    def lookup_customer_owners(self, external_userids: list[str]) -> dict:
        return {
            "wm_ext_1": {"owner_userids": ["mengyu"], "customer_name": "客户一"},
            "wm_ext_2": {"owner_userids": ["other"], "customer_name": "客户二"},
        }


def test_owner_migration_page_renders_frontend_workbench_contract(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = TestClient(create_app()).get("/admin/owner-migration")

    assert response.status_code == 200
    html = response.text
    assert "客户负责人迁移 / 在职继承" in html
    assert "先完成企微客户转接，再同步 CRM 本地归属；执行前必须预览。" in html
    assert "① 选择人员与话术" in html
    assert "② 选择迁移范围" in html
    assert "③ 预览结果" in html
    assert "④ 二次确认与执行" in html
    assert 'name="scope_type" value="excel_include" checked' in html
    assert 'name="scope_type" value="all"' in html
    assert 'type="hidden" value="mengyu" data-owner-userid="source"' in html
    assert 'type="hidden" value="huangyoucan" data-owner-userid="target"' in html
    assert 'type="text" name="source_owner_userid"' not in html
    assert 'type="text" name="target_owner_userid"' not in html
    assert "OperationMemberPicker.open" in html
    assert 'scope: "owner_migration"' in html
    assert "includeInactive: kind === \"source\"" in html
    assert "/api/admin/owner-migration/template.xlsx" in html
    assert "/api/admin/owner-migration/import" in html
    assert "/api/admin/owner-migration/preview" in html
    assert "/api/admin/owner-migration/execute" in html
    assert "/api/admin/owner-migration/sessions/" in html
    assert "/api/admin/owner-migration/results/" in html
    assert "preview_token" in html
    assert "preview_hash" in html
    assert "confirm_phrase" in html
    assert "eligible_external_userids" not in html
    assert "all_external_userids" not in html
    assert "external_userids: [" not in html
    assert "fakeCustomerDB" not in html
    assert "xlsx.full.min.js" not in html


def test_owner_migration_service_rejects_same_owner():
    service = OwnerMigrationService(FakeOwnerMigrationRepository())

    result = service.run(
        OwnerMigrationCommand(
            source_owner_userid="mengyu",
            target_owner_userid="mengyu",
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "same_owner_userid"


def test_owner_migration_api_preview_uses_service(monkeypatch):
    repo = FakeOwnerMigrationRepository()
    service = OwnerMigrationService(repo)
    monkeypatch.setattr("aicrm_next.crm.owner_migration.api.build_owner_migration_service", lambda: service)

    response = TestClient(create_app()).post(
        "/api/admin/owner-migration/preview",
        json={"source_owner_userid": "mengyu", "target_owner_userid": "huangyoucan"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["candidate_count"] == 2
    assert payload["sample_external_userids"] == ["wm_ext_1", "wm_ext_2"]
    assert repo.executed is False


def test_owner_migration_api_execute_requires_confirm(monkeypatch):
    service = OwnerMigrationService(FakeOwnerMigrationRepository())
    monkeypatch.setattr("aicrm_next.crm.owner_migration.api.build_owner_migration_service", lambda: service)

    response = TestClient(create_app()).post(
        "/api/admin/owner-migration/execute",
        json={"source_owner_userid": "mengyu", "target_owner_userid": "huangyoucan"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "confirm_required"


def test_owner_migration_api_execute_with_confirm(monkeypatch):
    repo = FakeOwnerMigrationRepository()
    service = OwnerMigrationService(repo)
    monkeypatch.setattr("aicrm_next.crm.owner_migration.api.build_owner_migration_service", lambda: service)

    response = TestClient(create_app()).post(
        "/api/admin/owner-migration/execute",
        json={
            "source_owner_userid": "mengyu",
            "target_owner_userid": "huangyoucan",
            "operator": "pytest",
            "confirm": True,
            "perform_wecom_transfer": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "execute"
    assert payload["update_counts"] == {"contacts": 2}
    assert repo.executed is True


def test_owner_migration_service_executes_wecom_transfer_before_local_update(monkeypatch):
    class TransferAdapter:
        def transfer_customer(self, payload):
            assert payload["handover_userid"] == "mengyu"
            assert payload["takeover_userid"] == "huangyoucan"
            return {
                "errcode": 0,
                "errmsg": "ok",
                "customer": [
                    {"external_userid": "wm_ext_1", "errcode": 0},
                    {"external_userid": "wm_ext_2", "errcode": 40096},
                ],
            }

    captured = {}

    class ScopedRepo(FakeOwnerMigrationRepository):
        def preview_owner_migration(self, *, source_owner_userid: str, target_owner_userid: str) -> dict:
            payload = super().preview_owner_migration(
                source_owner_userid=source_owner_userid,
                target_owner_userid=target_owner_userid,
            )
            payload["all_external_userids"] = ["wm_ext_1", "wm_ext_2"]
            return payload

        def execute_owner_migration(self, **kwargs) -> dict:
            captured["external_userids"] = kwargs.get("external_userids")
            return super().execute_owner_migration(**kwargs)

    monkeypatch.setattr("aicrm_next.crm.owner_migration.application.missing_wecom_config", lambda: [])
    monkeypatch.setattr("aicrm_next.crm.owner_migration.application.ProductionWeComAdapter", lambda: TransferAdapter())

    result = OwnerMigrationService(ScopedRepo()).run(
        OwnerMigrationCommand(
            source_owner_userid="mengyu",
            target_owner_userid="huangyoucan",
            operator="pytest",
            execute=True,
            confirm=True,
        )
    )

    assert result["ok"] is True
    assert captured["external_userids"] == ["wm_ext_1"]
    assert result["wecom_transfer"]["success_count"] == 1
    assert result["wecom_transfer"]["failed_customers"] == [{"external_userid": "wm_ext_2", "errcode": 40096}]


def test_pending_review_counts_skip_existing_tables_with_missing_owner_columns():
    class FakeCursor:
        def __init__(self, conn):
            self.conn = conn
            self.query = ""
            self.params = ()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=()):
            self.query = str(query)
            self.params = tuple(params)
            self.conn.executed_queries.append(self.query)

        def fetchone(self):
            if "to_regclass" in self.query:
                return {"exists": self.params[0] in self.conn.tables}
            if "information_schema.columns" in self.query:
                table_name, column_name = self.params
                return {"exists": column_name in self.conn.tables.get(table_name, set())}
            if "FROM outbound_tasks" in self.query:
                return {"count": 3}
            return {"count": 0}

    class FakeConn:
        tables = {
            "broadcast_jobs": {"status"},
            "outbound_tasks": {"request_payload", "status"},
        }

        def __init__(self):
            self.executed_queries = []

        def cursor(self):
            return FakeCursor(self)

    conn = FakeConn()

    counts = PostgresOwnerMigrationRepository()._pending_review_counts(conn, "MengYu")

    assert counts == {"pending_outbound_tasks": 3}
    assert not any("FROM broadcast_jobs WHERE owner_userid" in query for query in conn.executed_queries)
