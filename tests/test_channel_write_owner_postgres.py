from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

from aicrm_next.channel_entry.channel_write_port import (
    SaveChannelRequest,
    UpdateChannelAssignmentRequest,
    UpdateChannelQRCodeRequest,
    build_channel_write_port,
)
from aicrm_next.channel_entry.channel_write_repository import CHANNEL_COLUMNS


def _channel_data(*, name: str = "owner test") -> dict:
    data = {column: "" for column in CHANNEL_COLUMNS}
    data.update(
        {
            "channel_type": "qrcode",
            "carrier_type": "qrcode",
            "channel_name": name,
            "channel_code": "owner-test",
            "scene_value": "scene-owner-test",
            "qr_url": "https://example.test/old.png",
            "status": "active",
            "owner_staff_id": "staff-1",
            "welcome_image_library_ids": [],
            "welcome_miniprogram_library_ids": [],
            "welcome_attachment_library_ids": [],
            "welcome_group_invite_library_ids": [],
            "auto_accept_friend": True,
            "assignment_mode": "multi_staff",
            "assignment_strategy": "ratio",
            "overflow_policy": "least_loaded",
            "assignment_config_json": {},
        }
    )
    return data


def test_channel_write_owner_preserves_one_caller_transaction(next_pg_schema) -> None:
    port = build_channel_write_port()
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            channel_id = port.save(cursor, request=SaveChannelRequest(data=_channel_data()))
            port.update_assignment(
                cursor,
                request=UpdateChannelAssignmentRequest(
                    channel_id=channel_id,
                    assignment_mode="multi_staff",
                    assignment_strategy="round_robin",
                    overflow_policy="first_available",
                ),
            )
            qrcode_row = port.update_qrcode(
                cursor,
                request=UpdateChannelQRCodeRequest(
                    channel_id=channel_id,
                    scene_value="scene-owner-updated",
                    qr_url="https://example.test/updated.png",
                    config_id="config-owner",
                ),
            )
            port.save(
                cursor,
                request=SaveChannelRequest(
                    data=_channel_data(name="owner updated"),
                    channel_id=channel_id,
                ),
            )
            row = cursor.execute(
                "SELECT channel_name, assignment_mode, assignment_strategy, overflow_policy "
                "FROM automation_channel WHERE id = %s",
                (channel_id,),
            ).fetchone()
        connection.rollback()

    assert qrcode_row["scene_value"] == "scene-owner-updated"
    assert qrcode_row["qr_ticket"] == "config-owner"
    assert row == {
        "channel_name": "owner updated",
        "assignment_mode": "multi_staff",
        "assignment_strategy": "ratio",
        "overflow_policy": "least_loaded",
    }
