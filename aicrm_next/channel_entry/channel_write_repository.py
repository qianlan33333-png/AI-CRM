from __future__ import annotations

from typing import Any

from .channel_write_port import (
    SaveChannelRequest,
    UpdateChannelAssignmentRequest,
    UpdateChannelQRCodeRequest,
)


CHANNEL_COLUMNS = (
    "channel_type",
    "carrier_type",
    "channel_name",
    "channel_code",
    "scene_value",
    "qr_url",
    "status",
    "owner_staff_id",
    "customer_channel",
    "link_url",
    "final_url",
    "welcome_message",
    "welcome_image_library_ids",
    "welcome_miniprogram_library_ids",
    "welcome_attachment_library_ids",
    "welcome_group_invite_library_ids",
    "auto_accept_friend",
    "entry_tag_id",
    "entry_tag_name",
    "entry_tag_group_name",
    "assignment_mode",
    "assignment_strategy",
    "overflow_policy",
    "assignment_config_json",
)

JSON_COLUMNS = {
    "welcome_image_library_ids",
    "welcome_miniprogram_library_ids",
    "welcome_attachment_library_ids",
    "welcome_group_invite_library_ids",
    "assignment_config_json",
}


class PostgresChannelWriteRepository:
    """Sole automation_channel SQL writer; caller retains commit ownership."""

    def save(self, executor: Any, *, request: SaveChannelRequest) -> int:
        from psycopg.types.json import Jsonb

        values = [
            Jsonb(request.data[column]) if column in JSON_COLUMNS else request.data[column]
            for column in CHANNEL_COLUMNS
        ]
        if request.channel_id is not None:
            assignments = ", ".join(f"{column} = %s" for column in CHANNEL_COLUMNS)
            executor.execute(
                f"UPDATE automation_channel SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = %s RETURNING id",
                tuple(values + [int(request.channel_id)]),
            )
            row = executor.fetchone()
            return int((row or {}).get("id") or request.channel_id)

        placeholders = ", ".join(["%s"] * len(CHANNEL_COLUMNS))
        executor.execute(
            f"INSERT INTO automation_channel ({', '.join(CHANNEL_COLUMNS)}) VALUES ({placeholders}) RETURNING id",
            tuple(values),
        )
        row = executor.fetchone()
        return int((row or {}).get("id") or 0)

    def update_assignment(self, executor: Any, *, request: UpdateChannelAssignmentRequest) -> None:
        executor.execute(
            """
            UPDATE automation_channel
            SET assignment_mode = %s,
                assignment_strategy = %s,
                overflow_policy = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (
                request.assignment_mode,
                request.assignment_strategy,
                request.overflow_policy,
                int(request.channel_id),
            ),
        )

    def update_qrcode(self, executor: Any, *, request: UpdateChannelQRCodeRequest) -> dict[str, Any]:
        executor.execute(
            """
            UPDATE automation_channel
            SET scene_value = %s,
                qr_url = %s,
                qr_ticket = %s,
                carrier_type = CASE WHEN carrier_type = '' THEN 'qrcode' ELSE carrier_type END,
                channel_type = CASE WHEN channel_type = '' THEN 'qrcode' ELSE channel_type END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING *
            """,
            (request.scene_value, request.qr_url, request.config_id, int(request.channel_id)),
        )
        row = executor.fetchone()
        return dict(row) if row else {}


__all__ = ["PostgresChannelWriteRepository"]
