from __future__ import annotations

import zipfile
from io import BytesIO

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.crm.owner_migration.application import (
    DEFAULT_TRANSFER_WELCOME_MSG,
    OwnerMigrationCommand,
    OwnerMigrationService,
    build_xlsx,
)


class StatefulOwnerMigrationRepo:
    source_status = "stateful_test"

    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}
        self.previews: dict[str, dict] = {}
        self.results: dict[str, dict] = {}
        self.audit: list[tuple[str, dict]] = []
        self.executed_external_userids: list[str] | None = None

    def preview_owner_migration(self, *, source_owner_userid: str, target_owner_userid: str, external_userids: list[str] | None = None) -> dict:
        del target_owner_userid
        candidates = ["wm_ready"] if source_owner_userid == "HuangYouCan" else []
        if external_userids is not None:
            allowed = set(external_userids)
            candidates = [item for item in candidates if item in allowed]
        return {
            "source_status": self.source_status,
            "candidate_count": len(candidates),
            "all_external_userids": candidates,
            "sample_external_userids": candidates[:20],
            "surface_counts": {"contacts": len(candidates), "customer_list_index_next": len(candidates)},
            "pending_review": {"pending_user_ops_deferred_jobs": 0, "pending_broadcast_jobs": 0, "pending_outbound_tasks": 0},
            "notes": [],
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
        del source_owner_userid, target_owner_userid, operator, target_owner_display_name
        self.executed_external_userids = list(external_userids or [])
        return {"executed": True, "update_counts": {"contacts": len(self.executed_external_userids)}, "touched_count": len(self.executed_external_userids), "touched_external_userids": self.executed_external_userids}

    def resolve_operation_members(self, userids: list[str]) -> dict:
        members = {
            "HuangYouCan": {"user_id": "HuangYouCan", "display_name": "HuangYouCan", "status": "active"},
            "QianLan": {"user_id": "QianLan", "display_name": "钱岚", "status": "active"},
            "InactiveTarget": {"user_id": "InactiveTarget", "display_name": "离职目标", "status": "inactive"},
        }
        return {userid: members[userid] for userid in userids if userid in members}

    def lookup_customer_owners(self, external_userids: list[str]) -> dict:
        del external_userids
        return {
            "wm_ready": {"owner_userids": ["HuangYouCan"], "customer_name": "真实测试客户"},
            "wm_target": {"owner_userids": ["QianLan"], "customer_name": "目标负责人客户"},
            "wm_other": {"owner_userids": ["OtherUser"], "customer_name": "其他负责人客户"},
        }

    def save_import_session(self, session: dict) -> None:
        self.sessions[session["session_id"]] = session

    def get_import_session(self, session_id: str) -> dict | None:
        return self.sessions.get(session_id)

    def save_preview(self, preview: dict) -> None:
        self.previews[preview["preview_token"]] = preview

    def get_preview(self, preview_token: str) -> dict | None:
        return self.previews.get(preview_token)

    def get_latest_preview_by_session(self, session_id: str) -> dict | None:
        previews = [preview for preview in self.previews.values() if preview.get("session_id") == session_id]
        return previews[-1] if previews else None

    def mark_preview_executed(self, preview_token: str, result_id: str) -> None:
        self.previews[preview_token]["executed_result_id"] = result_id

    def save_result(self, result: dict) -> None:
        self.results[result["result_id"]] = result

    def get_result(self, result_id: str) -> dict | None:
        return self.results.get(result_id)

    def audit_owner_migration_event(self, event_type: str, payload: dict) -> None:
        self.audit.append((event_type, payload))


def _workbook(rows: list[list[str]]) -> bytes:
    return build_xlsx(["external_userid", "是否迁移", "当前负责人userid", "客户备注名", "备注"], rows, sheet_name="owner_migration")


def test_template_xlsx_endpoint_contains_required_headers(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    response = TestClient(create_app()).get("/api/admin/owner-migration/template.xlsx")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    for header in ["external_userid", "是否迁移", "当前负责人userid", "客户备注名", "备注"]:
        assert header in sheet_xml


def test_import_parses_xlsx_rows_and_persists_session():
    repo = StatefulOwnerMigrationRepo()
    service = OwnerMigrationService(repo)

    content = _workbook(
        [
            ["wm_ready", "是", "HuangYouCan", "真实测试客户", "真实测试账号"],
            ["wm_ready", "是", "HuangYouCan", "重复客户", ""],
            ["wm_skip", "否", "HuangYouCan", "跳过客户", ""],
            ["", "是", "HuangYouCan", "空客户", ""],
            ["wm_invalid", "随便迁", "HuangYouCan", "非法标记", ""],
            ["wm_other", "是", "OtherUser", "负责人不一致", ""],
        ]
    )

    result = service.import_file(
        filename="owner_migration.xlsx",
        content=content,
        source_owner_userid="HuangYouCan",
        target_owner_userid="QianLan",
        include_wecom_transfer=True,
        transfer_welcome_msg=DEFAULT_TRANSFER_WELCOME_MSG,
        operator="pytest",
    )

    assert result["ok"] is True
    assert result["session_id"] in repo.sessions
    assert result["file_hash"]
    assert result["row_stats"]["total_rows"] == 6
    assert result["row_stats"]["unique_external_userids"] == 4
    assert result["row_stats"]["duplicate_rows"] == 1
    assert result["row_stats"]["invalid_rows"] == 2
    statuses = {row["row_number"]: row["parse_status"] for row in result["rows"]}
    assert statuses[3] == "duplicate"
    assert statuses[5] == "missing_external_userid"
    assert statuses[6] == "invalid_move_flag"
    assert repo.executed_external_userids is None


def test_excel_include_preview_only_allows_ready_intersection():
    repo = StatefulOwnerMigrationRepo()
    service = OwnerMigrationService(repo)
    imported = service.import_file(
        filename="owner_migration.xlsx",
        content=_workbook(
            [
                ["wm_ready", "是", "HuangYouCan", "真实测试客户", ""],
                ["wm_skip", "否", "HuangYouCan", "跳过客户", ""],
                ["wm_target", "是", "HuangYouCan", "目标客户", ""],
                ["wm_other", "是", "HuangYouCan", "其他客户", ""],
                ["wm_missing", "是", "HuangYouCan", "缺失客户", ""],
                ["wm_ready", "是", "HuangYouCan", "重复客户", ""],
            ]
        ),
        source_owner_userid="HuangYouCan",
        target_owner_userid="QianLan",
        include_wecom_transfer=False,
        transfer_welcome_msg=DEFAULT_TRANSFER_WELCOME_MSG,
        operator="pytest",
    )

    preview = service.run(
        OwnerMigrationCommand(
            scope_type="excel_include",
            session_id=imported["session_id"],
            source_owner_userid="HuangYouCan",
            target_owner_userid="QianLan",
            perform_wecom_transfer=False,
            transfer_success_msg=DEFAULT_TRANSFER_WELCOME_MSG,
        )
    )

    assert preview["ok"] is True
    assert preview["eligible_external_userids"] == ["wm_ready"]
    assert preview["row_stats"]["ready"] == 1
    assert preview["surface_counts"] == {"contacts": 1, "customer_list_index_next": 1}
    statuses = {row["external_userid"]: row["status"] for row in preview["rows"] if row["external_userid"]}
    assert statuses["wm_skip"] == "skipped_by_file"
    assert statuses["wm_target"] == "already_target_owner"
    assert statuses["wm_other"] == "not_under_source_owner"
    assert statuses["wm_missing"] == "not_found"
    assert preview["rows"][-1]["status"] == "duplicate"


def test_execute_requires_preview_hash_and_updates_only_ready_local_scope():
    repo = StatefulOwnerMigrationRepo()
    service = OwnerMigrationService(repo)
    imported = service.import_file(
        filename="owner_migration.xlsx",
        content=_workbook([["wm_ready", "是", "HuangYouCan", "真实测试客户", ""]]),
        source_owner_userid="HuangYouCan",
        target_owner_userid="QianLan",
        include_wecom_transfer=False,
        transfer_welcome_msg=DEFAULT_TRANSFER_WELCOME_MSG,
        operator="pytest",
    )
    preview = service.run(
        OwnerMigrationCommand(
            scope_type="excel_include",
            session_id=imported["session_id"],
            source_owner_userid="HuangYouCan",
            target_owner_userid="QianLan",
            perform_wecom_transfer=False,
            transfer_success_msg=DEFAULT_TRANSFER_WELCOME_MSG,
        )
    )

    bad = service.run(
        OwnerMigrationCommand(
            scope_type="excel_include",
            session_id=imported["session_id"],
            source_owner_userid="HuangYouCan",
            target_owner_userid="QianLan",
            perform_wecom_transfer=False,
            transfer_success_msg=DEFAULT_TRANSFER_WELCOME_MSG,
            execute=True,
            confirm=True,
            preview_token=preview["preview_token"],
            preview_hash="tampered",
            confirm_phrase=preview["confirm_phrase"],
        )
    )
    assert bad["ok"] is False
    assert bad["error_code"] == "preview_hash_mismatch"
    assert repo.executed_external_userids is None

    result = service.run(
        OwnerMigrationCommand(
            scope_type="excel_include",
            session_id=imported["session_id"],
            source_owner_userid="HuangYouCan",
            target_owner_userid="QianLan",
            perform_wecom_transfer=False,
            transfer_success_msg=DEFAULT_TRANSFER_WELCOME_MSG,
            execute=True,
            confirm=True,
            preview_token=preview["preview_token"],
            preview_hash=preview["preview_hash"],
            confirm_phrase=preview["confirm_phrase"],
        )
    )

    assert result["ok"] is True
    assert result["mode"] == "local_only"
    assert result["requested_external_userids"] == 1
    assert result["wecom_requested"] == 0
    assert result["crm_updated"] == 1
    assert repo.executed_external_userids == ["wm_ready"]
    assert result["result_id"] in repo.results

    repeated = service.run(
        OwnerMigrationCommand(
            scope_type="excel_include",
            session_id=imported["session_id"],
            source_owner_userid="HuangYouCan",
            target_owner_userid="QianLan",
            perform_wecom_transfer=False,
            transfer_success_msg=DEFAULT_TRANSFER_WELCOME_MSG,
            execute=True,
            confirm=True,
            preview_token=preview["preview_token"],
            preview_hash=preview["preview_hash"],
            confirm_phrase=preview["confirm_phrase"],
        )
    )
    assert repeated["ok"] is False
    assert repeated["error_code"] == "preview_token_already_executed"
