from __future__ import annotations

from pathlib import Path
import re

from aicrm_next.channel_entry.channel_write_port import (
    SaveChannelRequest,
    UpdateChannelAssignmentRequest,
    UpdateChannelQRCodeRequest,
    build_channel_write_port,
)


ROOT = Path(__file__).resolve().parents[1]


def _channel_data(*, name: str = "owner test") -> dict:
    return {
        "channel_type": "qrcode",
        "carrier_type": "qrcode",
        "channel_name": name,
        "channel_code": "owner-test",
        "scene_value": "scene-owner-test",
        "qr_url": "https://example.test/old.png",
        "status": "active",
        "owner_staff_id": "staff-1",
        "customer_channel": "owner-test",
        "link_url": "",
        "final_url": "",
        "welcome_message": "hello",
        "welcome_image_library_ids": [],
        "welcome_miniprogram_library_ids": [],
        "welcome_attachment_library_ids": [],
        "welcome_group_invite_library_ids": [],
        "auto_accept_friend": True,
        "entry_tag_id": "",
        "entry_tag_name": "",
        "entry_tag_group_name": "",
        "assignment_mode": "multi_staff",
        "assignment_strategy": "ratio",
        "overflow_policy": "least_loaded",
        "assignment_config_json": {},
    }


class _Executor:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.rows = list(rows or [])

    def execute(self, statement, params) -> None:
        self.calls.append((" ".join(str(statement).split()), tuple(params)))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


def test_channel_write_port_handles_all_canonical_channel_mutations_without_commit() -> None:
    executor = _Executor(rows=[{"id": 41}, {"id": 41, "scene_value": "scene-new"}])
    port = build_channel_write_port()

    channel_id = port.save(executor, request=SaveChannelRequest(data=_channel_data()))
    port.update_assignment(
        executor,
        request=UpdateChannelAssignmentRequest(
            channel_id=channel_id,
            assignment_mode="multi_staff",
            assignment_strategy="round_robin",
            overflow_policy="least_loaded",
        ),
    )
    updated = port.update_qrcode(
        executor,
        request=UpdateChannelQRCodeRequest(
            channel_id=channel_id,
            scene_value="scene-new",
            qr_url="https://example.test/new.png",
            config_id="config-1",
        ),
    )

    assert channel_id == 41
    assert executor.calls[0][0].startswith("INSERT INTO automation_channel")
    assert executor.calls[1][0].startswith("UPDATE automation_channel SET assignment_mode")
    assert executor.calls[2][0].startswith("UPDATE automation_channel SET scene_value")
    assert updated["scene_value"] == "scene-new"
    assert not hasattr(executor, "commit")


def test_channel_write_port_preserves_admin_update_contract() -> None:
    executor = _Executor(rows=[{"id": 42}])

    channel_id = build_channel_write_port().save(
        executor,
        request=SaveChannelRequest(data=_channel_data(name="updated"), channel_id=42),
    )

    assert channel_id == 42
    assert executor.calls[0][0].startswith("UPDATE automation_channel SET channel_type")
    assert executor.calls[0][1][-1] == 42


def test_automation_channel_sql_has_one_module_owner() -> None:
    owner_source = (ROOT / "aicrm_next/channel_entry/channel_write_repository.py").read_text(encoding="utf-8")
    callers = [
        ROOT / "aicrm_next/automation_engine/channels_repo.py",
        ROOT / "aicrm_next/channel_entry/repo.py",
    ]

    assert "INSERT INTO automation_channel" in owner_source
    assert "UPDATE automation_channel" in owner_source
    insert_pattern = re.compile(r"\bINSERT\s+INTO\s+automation_channel\b", re.IGNORECASE)
    update_pattern = re.compile(r"\bUPDATE\s+automation_channel\b", re.IGNORECASE)
    assert all(not insert_pattern.search(path.read_text(encoding="utf-8")) for path in callers)
    assert all(not update_pattern.search(path.read_text(encoding="utf-8")) for path in callers)
