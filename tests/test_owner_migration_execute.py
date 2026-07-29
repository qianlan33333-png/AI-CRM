from __future__ import annotations

from aicrm_next.crm.owner_migration import application
from aicrm_next.crm.owner_migration.application import DEFAULT_TRANSFER_WELCOME_MSG, OwnerMigrationCommand, OwnerMigrationService, build_xlsx

from test_owner_migration_import import StatefulOwnerMigrationRepo


class TwoReadyRepo(StatefulOwnerMigrationRepo):
    def preview_owner_migration(self, *, source_owner_userid: str, target_owner_userid: str, external_userids: list[str] | None = None) -> dict:
        del target_owner_userid
        candidates = ["wm_success", "wm_fail"] if source_owner_userid == "HuangYouCan" else []
        if external_userids is not None:
            allowed = set(external_userids)
            candidates = [item for item in candidates if item in allowed]
        return {
            "source_status": self.source_status,
            "candidate_count": len(candidates),
            "all_external_userids": candidates,
            "sample_external_userids": candidates,
            "surface_counts": {"contacts": len(candidates)},
            "pending_review": {},
            "notes": [],
        }

    def lookup_customer_owners(self, external_userids: list[str]) -> dict:
        del external_userids
        return {
            "wm_success": {"owner_userids": ["HuangYouCan"], "customer_name": "成功客户"},
            "wm_fail": {"owner_userids": ["HuangYouCan"], "customer_name": "失败客户"},
        }


def test_execute_wecom_partial_success_updates_only_successful_customers(monkeypatch):
    class TransferAdapter:
        def transfer_customer(self, payload):
            assert payload["external_userid"] == ["wm_success", "wm_fail"]
            return {
                "errcode": 0,
                "errmsg": "ok",
                "customer": [
                    {"external_userid": "wm_success", "errcode": 0},
                    {"external_userid": "wm_fail", "errcode": 40096, "errmsg": "cannot transfer"},
                ],
            }

    monkeypatch.setattr(application, "missing_wecom_config", lambda: [])
    monkeypatch.setattr(application, "ProductionWeComAdapter", lambda: TransferAdapter())

    repo = TwoReadyRepo()
    service = OwnerMigrationService(repo)
    imported = service.import_file(
        filename="owner_migration.xlsx",
        content=build_xlsx(
            ["external_userid", "是否迁移", "当前负责人userid", "客户备注名", "备注"],
            [
                ["wm_success", "是", "HuangYouCan", "成功客户", ""],
                ["wm_fail", "是", "HuangYouCan", "失败客户", ""],
            ],
            sheet_name="owner_migration",
        ),
        source_owner_userid="HuangYouCan",
        target_owner_userid="QianLan",
        include_wecom_transfer=True,
        transfer_welcome_msg=DEFAULT_TRANSFER_WELCOME_MSG,
        operator="pytest",
    )
    preview = service.run(
        OwnerMigrationCommand(
            scope_type="excel_include",
            session_id=imported["session_id"],
            source_owner_userid="HuangYouCan",
            target_owner_userid="QianLan",
            perform_wecom_transfer=True,
            transfer_success_msg=DEFAULT_TRANSFER_WELCOME_MSG,
        )
    )

    result = service.run(
        OwnerMigrationCommand(
            scope_type="excel_include",
            session_id=imported["session_id"],
            source_owner_userid="HuangYouCan",
            target_owner_userid="QianLan",
            perform_wecom_transfer=True,
            transfer_success_msg=DEFAULT_TRANSFER_WELCOME_MSG,
            execute=True,
            confirm=True,
            preview_token=preview["preview_token"],
            preview_hash=preview["preview_hash"],
            confirm_phrase=preview["confirm_phrase"],
        )
    )

    assert result["ok"] is True
    assert result["requested_external_userids"] == 2
    assert result["wecom_requested"] == 2
    assert result["wecom_success"] == 1
    assert result["wecom_failed"] == 1
    assert repo.executed_external_userids == ["wm_success"]
    row_by_id = {row["external_userid"]: row for row in result["rows"]}
    assert row_by_id["wm_success"]["crm_status"] == "updated"
    assert row_by_id["wm_fail"]["crm_status"] == "skipped"
