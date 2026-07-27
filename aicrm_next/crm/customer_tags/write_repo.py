from __future__ import annotations

from copy import deepcopy
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from aicrm_next.platform_foundation.command_bus.models import utcnow_iso
from aicrm_next.shared.db_session import get_engine
from aicrm_next.shared.typing import JsonDict

from .read_model import _fixture_rows


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


class WeComTagWriteRepository:
    def __init__(self) -> None:
        groups, tags = _fixture_rows()
        self._groups: dict[str, JsonDict] = {str(group["group_id"]): deepcopy(group) for group in groups}
        self._tags: dict[str, JsonDict] = {str(tag["tag_id"]): deepcopy(tag) for tag in tags}
        self._writes: list[JsonDict] = []

    def list_writes(self) -> list[JsonDict]:
        return deepcopy(self._writes)

    def get_group(self, group_id: str) -> JsonDict | None:
        group = self._groups.get(_text(group_id))
        return deepcopy(group) if group else None

    def get_tag(self, tag_id: str) -> JsonDict | None:
        tag = self._tags.get(_text(tag_id))
        return deepcopy(tag) if tag else None

    def create_group(self, *, command_id: str, group_name: str, first_tag_name: str = "") -> JsonDict:
        group_name = _text(group_name)
        if not group_name:
            raise ValueError("group_name is required")
        if any(_text(group.get("group_name")) == group_name for group in self._groups.values()):
            raise ValueError("group_name already exists")
        group_id = f"group_next_{uuid4().hex[:10]}"
        now = utcnow_iso()
        group = {
            "group_id": group_id,
            "tag_group_id": group_id,
            "group_key": group_id,
            "group_name": group_name,
            "tag_count": 0,
            "source": "local_projection",
            "updated_at": now,
            "synced_at": now,
        }
        self._groups[group_id] = group
        created_tags: list[JsonDict] = []
        if _text(first_tag_name):
            created_tags.append(self.create_tag(command_id=command_id, group_id=group_id, tag_name=first_tag_name))
        self._record(command_id, "tag_group_created", {"group": group, "tags": created_tags})
        return {"group": deepcopy(group), "tags": deepcopy(created_tags)}

    def update_group(self, *, command_id: str, group_id: str, group_name: str) -> JsonDict:
        group = self._require_group(group_id)
        group_name = _text(group_name)
        if not group_name:
            raise ValueError("group_name is required")
        group["group_name"] = group_name
        group["updated_at"] = utcnow_iso()
        for tag in self._tags.values():
            if _text(tag.get("group_id")) == _text(group_id):
                tag["group_name"] = group_name
                tag["updated_at"] = group["updated_at"]
        self._record(command_id, "tag_group_updated", {"group": group})
        return deepcopy(group)

    def delete_group(self, *, command_id: str, group_id: str) -> JsonDict:
        group = self._require_group(group_id)
        deleted_tags = [tag_id for tag_id, tag in self._tags.items() if _text(tag.get("group_id")) == _text(group_id)]
        for tag_id in deleted_tags:
            self._tags.pop(tag_id, None)
        self._groups.pop(_text(group_id), None)
        self._record(command_id, "tag_group_deleted", {"group": group, "deleted_tag_ids": deleted_tags})
        return {"group": deepcopy(group), "deleted_tag_ids": deleted_tags}

    def create_tag(self, *, command_id: str, group_id: str, tag_name: str) -> JsonDict:
        group = self._require_group(group_id)
        tag_name = _text(tag_name)
        if not tag_name:
            raise ValueError("tag_name is required")
        if any(_text(tag.get("group_id")) == _text(group_id) and _text(tag.get("tag_name")) == tag_name for tag in self._tags.values()):
            raise ValueError("tag_name already exists in group")
        now = utcnow_iso()
        tag_id = f"tag_next_{uuid4().hex[:10]}"
        tag = {
            "tag_id": tag_id,
            "tag_group_id": _text(group.get("group_id")),
            "tag_name": tag_name,
            "group_id": _text(group.get("group_id")),
            "group_name": _text(group.get("group_name")),
            "order": len([item for item in self._tags.values() if _text(item.get("group_id")) == _text(group_id)]) + 1,
            "status": "active",
            "source": "local_projection",
            "updated_at": now,
            "synced_at": now,
        }
        self._tags[tag_id] = tag
        group["tag_count"] = len([item for item in self._tags.values() if _text(item.get("group_id")) == _text(group_id)])
        group["updated_at"] = now
        self._record(command_id, "tag_created", {"tag": tag})
        return deepcopy(tag)

    def update_tag(self, *, command_id: str, tag_id: str, tag_name: str) -> JsonDict:
        tag = self._require_tag(tag_id)
        tag_name = _text(tag_name)
        if not tag_name:
            raise ValueError("tag_name is required")
        tag["tag_name"] = tag_name
        tag["updated_at"] = utcnow_iso()
        self._record(command_id, "tag_updated", {"tag": tag})
        return deepcopy(tag)

    def delete_tag(self, *, command_id: str, tag_id: str) -> JsonDict:
        tag = self._require_tag(tag_id)
        self._tags.pop(_text(tag_id), None)
        group = self._groups.get(_text(tag.get("group_id")))
        if group:
            group["tag_count"] = len([item for item in self._tags.values() if _text(item.get("group_id")) == _text(group.get("group_id"))])
            group["updated_at"] = utcnow_iso()
        self._record(command_id, "tag_deleted", {"tag": tag})
        return deepcopy(tag)

    def sync_catalog(self, *, command_id: str) -> JsonDict:
        now = utcnow_iso()
        for group in self._groups.values():
            group["synced_at"] = now
            group["updated_at"] = group.get("updated_at") or now
        for tag in self._tags.values():
            tag["synced_at"] = now
            tag["updated_at"] = tag.get("updated_at") or now
        payload = {"synced_at": now, "groups": len(self._groups), "tags": len(self._tags)}
        self._record(command_id, "tag_catalog_sync_planned", payload)
        return payload

    def _require_group(self, group_id: str) -> JsonDict:
        group = self._groups.get(_text(group_id))
        if not group:
            raise KeyError("tag group not found")
        return group

    def _require_tag(self, tag_id: str) -> JsonDict:
        tag = self._tags.get(_text(tag_id))
        if not tag:
            raise KeyError("tag not found")
        return tag

    def _record(self, command_id: str, write_type: str, payload: JsonDict) -> None:
        self._writes.append({"command_id": command_id, "write_type": write_type, "payload": deepcopy(payload), "created_at": utcnow_iso()})


class PostgresWeComTagWriteRepository:
    source_status = "production_postgres_tag_catalog"

    def __init__(self, engine: Engine | None = None) -> None:
        self._engine = engine or get_engine()
        self._writes: list[JsonDict] = []

    def list_writes(self) -> list[JsonDict]:
        return deepcopy(self._writes)

    def get_group(self, group_id: str) -> JsonDict | None:
        with self._engine.connect() as connection:
            return self._group_response(self._group_row(connection, group_id))

    def get_tag(self, tag_id: str) -> JsonDict | None:
        with self._engine.connect() as connection:
            return self._tag_response(self._tag_row(connection, tag_id))

    def create_group(self, *, command_id: str, group_name: str, first_tag_name: str = "") -> JsonDict:
        group_name = _text(group_name)
        if not group_name:
            raise ValueError("group_name is required")
        group_id = f"group_next_{uuid4().hex[:10]}"
        created_tags: list[JsonDict] = []
        with self._engine.begin() as connection:
            if self._group_name_exists(connection, group_name):
                raise ValueError("group_name already exists")
            connection.execute(
                text(
                    """
                    INSERT INTO wecom_corp_tag_groups (
                        group_id, group_name, group_key, tag_count, raw_payload, synced_at, updated_at
                    )
                    VALUES (
                        :group_id, :group_name, :group_key, 0,
                        CAST(:raw_payload AS jsonb), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "group_id": group_id,
                    "group_name": group_name,
                    "group_key": group_id,
                    "raw_payload": _json({"source": "admin_write", "command_id": command_id}),
                },
            )
            if _text(first_tag_name):
                created_tags.append(
                    self._create_tag(
                        connection,
                        command_id=command_id,
                        group_id=group_id,
                        tag_name=first_tag_name,
                    )
                )
            group = self._require_group(connection, group_id)
        self._record(command_id, "tag_group_created", {"group": group, "tags": created_tags})
        return {"group": deepcopy(group), "tags": deepcopy(created_tags)}

    def update_group(self, *, command_id: str, group_id: str, group_name: str) -> JsonDict:
        group_name = _text(group_name)
        if not group_name:
            raise ValueError("group_name is required")
        with self._engine.begin() as connection:
            self._require_group(connection, group_id)
            connection.execute(
                text(
                    """
                    UPDATE wecom_corp_tag_groups
                    SET group_name = :group_name,
                        raw_payload = raw_payload || CAST(:raw_payload AS jsonb),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE group_id = :group_id
                    """
                ),
                {
                    "group_id": _text(group_id),
                    "group_name": group_name,
                    "raw_payload": _json({"last_admin_command_id": command_id}),
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE wecom_corp_tags
                    SET group_name = :group_name,
                        raw_payload = raw_payload || CAST(:raw_payload AS jsonb),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE group_id = :group_id
                    """
                ),
                {
                    "group_id": _text(group_id),
                    "group_name": group_name,
                    "raw_payload": _json({"last_admin_command_id": command_id}),
                },
            )
            group = self._require_group(connection, group_id)
        self._record(command_id, "tag_group_updated", {"group": group})
        return deepcopy(group)

    def delete_group(self, *, command_id: str, group_id: str) -> JsonDict:
        with self._engine.begin() as connection:
            group = self._require_group(connection, group_id)
            deleted_tag_ids = [
                _text(row.get("tag_id"))
                for row in connection.execute(
                    text(
                        """
                        SELECT tag_id
                        FROM wecom_corp_tags
                        WHERE group_id = :group_id
                          AND deleted_at IS NULL
                        ORDER BY tag_id ASC
                        """
                    ),
                    {"group_id": _text(group_id)},
                ).mappings()
            ]
            connection.execute(
                text(
                    """
                    UPDATE wecom_corp_tags
                    SET deleted_at = COALESCE(deleted_at, CURRENT_TIMESTAMP),
                        raw_payload = raw_payload || CAST(:raw_payload AS jsonb),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE group_id = :group_id
                    """
                ),
                {
                    "group_id": _text(group_id),
                    "raw_payload": _json({"deleted_by_admin_command_id": command_id}),
                },
            )
            connection.execute(text("DELETE FROM wecom_corp_tag_groups WHERE group_id = :group_id"), {"group_id": _text(group_id)})
        self._record(command_id, "tag_group_deleted", {"group": group, "deleted_tag_ids": deleted_tag_ids})
        return {"group": deepcopy(group), "deleted_tag_ids": deleted_tag_ids}

    def create_tag(self, *, command_id: str, group_id: str, tag_name: str) -> JsonDict:
        with self._engine.begin() as connection:
            tag = self._create_tag(connection, command_id=command_id, group_id=group_id, tag_name=tag_name)
        self._record(command_id, "tag_created", {"tag": tag})
        return deepcopy(tag)

    def update_tag(self, *, command_id: str, tag_id: str, tag_name: str) -> JsonDict:
        tag_name = _text(tag_name)
        if not tag_name:
            raise ValueError("tag_name is required")
        with self._engine.begin() as connection:
            self._require_tag(connection, tag_id)
            connection.execute(
                text(
                    """
                    UPDATE wecom_corp_tags
                    SET tag_name = :tag_name,
                        raw_payload = raw_payload || CAST(:raw_payload AS jsonb),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE tag_id = :tag_id
                      AND deleted_at IS NULL
                    """
                ),
                {
                    "tag_id": _text(tag_id),
                    "tag_name": tag_name,
                    "raw_payload": _json({"last_admin_command_id": command_id}),
                },
            )
            tag = self._require_tag(connection, tag_id)
        self._record(command_id, "tag_updated", {"tag": tag})
        return deepcopy(tag)

    def delete_tag(self, *, command_id: str, tag_id: str) -> JsonDict:
        with self._engine.begin() as connection:
            tag = self._require_tag(connection, tag_id)
            connection.execute(
                text(
                    """
                    UPDATE wecom_corp_tags
                    SET deleted_at = COALESCE(deleted_at, CURRENT_TIMESTAMP),
                        raw_payload = raw_payload || CAST(:raw_payload AS jsonb),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE tag_id = :tag_id
                    """
                ),
                {
                    "tag_id": _text(tag_id),
                    "raw_payload": _json({"deleted_by_admin_command_id": command_id}),
                },
            )
            self._refresh_group_tag_count(connection, _text(tag.get("group_id")))
        self._record(command_id, "tag_deleted", {"tag": tag})
        return deepcopy(tag)

    def sync_catalog(self, *, command_id: str) -> JsonDict:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE wecom_corp_tag_groups
                    SET raw_payload = raw_payload || CAST(:raw_payload AS jsonb),
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {"raw_payload": _json({"last_admin_sync_command_id": command_id})},
            )
            connection.execute(
                text(
                    """
                    UPDATE wecom_corp_tags
                    SET raw_payload = raw_payload || CAST(:raw_payload AS jsonb),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE deleted_at IS NULL
                    """
                ),
                {"raw_payload": _json({"last_admin_sync_command_id": command_id})},
            )
            counts = connection.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM wecom_corp_tag_groups) AS groups,
                        (SELECT COUNT(*) FROM wecom_corp_tags WHERE deleted_at IS NULL) AS tags
                    """
                )
            ).mappings().one()
        payload = {"synced_at": utcnow_iso(), "groups": int(counts.get("groups") or 0), "tags": int(counts.get("tags") or 0)}
        self._record(command_id, "tag_catalog_sync_planned", payload)
        return payload

    def _create_tag(self, connection: Connection, *, command_id: str, group_id: str, tag_name: str) -> JsonDict:
        group = self._require_group(connection, group_id)
        tag_name = _text(tag_name)
        if not tag_name:
            raise ValueError("tag_name is required")
        if self._tag_name_exists(connection, group_id=_text(group_id), tag_name=tag_name):
            raise ValueError("tag_name already exists in group")
        tag_id = f"tag_next_{uuid4().hex[:10]}"
        order_index = int(
            connection.execute(
                text(
                    """
                    SELECT COALESCE(MAX(order_index), 0) + 1
                    FROM wecom_corp_tags
                    WHERE group_id = :group_id
                      AND deleted_at IS NULL
                    """
                ),
                {"group_id": _text(group_id)},
            ).scalar_one()
            or 1
        )
        connection.execute(
            text(
                """
                INSERT INTO wecom_corp_tags (
                    tag_id, tag_name, group_id, group_name, order_index,
                    deleted_at, raw_payload, synced_at, updated_at
                )
                VALUES (
                    :tag_id, :tag_name, :group_id, :group_name, :order_index,
                    NULL, CAST(:raw_payload AS jsonb), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "tag_id": tag_id,
                "tag_name": tag_name,
                "group_id": _text(group_id),
                "group_name": _text(group.get("group_name")),
                "order_index": order_index,
                "raw_payload": _json({"source": "admin_write", "command_id": command_id}),
            },
        )
        self._refresh_group_tag_count(connection, _text(group_id))
        return self._require_tag(connection, tag_id)

    def _group_name_exists(self, connection: Connection, group_name: str) -> bool:
        return bool(
            connection.execute(
                text("SELECT 1 FROM wecom_corp_tag_groups WHERE group_name = :group_name LIMIT 1"),
                {"group_name": _text(group_name)},
            ).scalar_one_or_none()
        )

    def _tag_name_exists(self, connection: Connection, *, group_id: str, tag_name: str) -> bool:
        return bool(
            connection.execute(
                text(
                    """
                    SELECT 1
                    FROM wecom_corp_tags
                    WHERE group_id = :group_id
                      AND tag_name = :tag_name
                      AND deleted_at IS NULL
                    LIMIT 1
                    """
                ),
                {"group_id": _text(group_id), "tag_name": _text(tag_name)},
            ).scalar_one_or_none()
        )

    def _require_group(self, connection: Connection, group_id: str) -> JsonDict:
        group = self._group_response(self._group_row(connection, group_id))
        if not group:
            raise KeyError("tag group not found")
        return group

    def _require_tag(self, connection: Connection, tag_id: str) -> JsonDict:
        tag = self._tag_response(self._tag_row(connection, tag_id))
        if not tag:
            raise KeyError("tag not found")
        return tag

    def _group_row(self, connection: Connection, group_id: str):
        return connection.execute(
            text(
                """
                SELECT group_id, group_key, group_name, tag_count, synced_at, updated_at
                FROM wecom_corp_tag_groups
                WHERE group_id = :group_id
                LIMIT 1
                """
            ),
            {"group_id": _text(group_id)},
        ).mappings().first()

    def _tag_row(self, connection: Connection, tag_id: str):
        return connection.execute(
            text(
                """
                SELECT tag_id, tag_name, group_id, group_name, order_index, deleted_at, synced_at, updated_at
                FROM wecom_corp_tags
                WHERE tag_id = :tag_id
                  AND deleted_at IS NULL
                LIMIT 1
                """
            ),
            {"tag_id": _text(tag_id)},
        ).mappings().first()

    def _refresh_group_tag_count(self, connection: Connection, group_id: str) -> None:
        if not _text(group_id):
            return
        connection.execute(
            text(
                """
                UPDATE wecom_corp_tag_groups
                SET tag_count = (
                    SELECT COUNT(*)
                    FROM wecom_corp_tags
                    WHERE group_id = :group_id
                      AND deleted_at IS NULL
                ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE group_id = :group_id
                """
            ),
            {"group_id": _text(group_id)},
        )

    def _group_response(self, row: Any) -> JsonDict | None:
        if not row:
            return None
        return {
            "group_id": _text(row.get("group_id")),
            "tag_group_id": _text(row.get("group_id")),
            "group_key": _text(row.get("group_key")) or _text(row.get("group_id")),
            "group_name": _text(row.get("group_name")),
            "tag_count": int(row.get("tag_count") or 0),
            "source": self.source_status,
            "updated_at": _iso(row.get("updated_at")),
            "synced_at": _iso(row.get("synced_at")),
        }

    def _tag_response(self, row: Any) -> JsonDict | None:
        if not row:
            return None
        return {
            "tag_id": _text(row.get("tag_id")),
            "tag_group_id": _text(row.get("group_id")),
            "tag_name": _text(row.get("tag_name")),
            "group_id": _text(row.get("group_id")),
            "group_name": _text(row.get("group_name")),
            "order": int(row.get("order_index") or 0),
            "status": "active",
            "source": self.source_status,
            "updated_at": _iso(row.get("updated_at")),
            "synced_at": _iso(row.get("synced_at")),
        }

    def _record(self, command_id: str, write_type: str, payload: JsonDict) -> None:
        self._writes.append({"command_id": command_id, "write_type": write_type, "payload": deepcopy(payload), "created_at": utcnow_iso()})
