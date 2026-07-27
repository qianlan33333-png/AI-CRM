from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aicrm_next.shared.db_session import get_engine
from aicrm_next.shared.runtime import database_mode, production_environment


Json = dict[str, Any]


class TagCatalogUnavailable(RuntimeError):
    pass


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _fixture_rows() -> tuple[list[Json], list[Json]]:
    synced_at = _timestamp()
    groups = [
        {
            "tag_group_id": "group_fixture_lifecycle",
            "group_id": "group_fixture_lifecycle",
            "group_key": "group_fixture_lifecycle",
            "group_name": "客户阶段",
            "tag_count": 2,
            "source": "local_contract_probe",
            "updated_at": synced_at,
            "synced_at": synced_at,
        }
    ]
    tags = [
        {
            "tag_group_id": "group_fixture_lifecycle",
            "tag_id": "tag_fixture_active",
            "tag_name": "活跃客户",
            "group_id": "group_fixture_lifecycle",
            "group_name": "客户阶段",
            "order": 1,
            "status": "active",
            "source": "local_contract_probe",
            "updated_at": synced_at,
            "synced_at": synced_at,
        },
        {
            "tag_group_id": "group_fixture_lifecycle",
            "tag_id": "tag_fixture_trial",
            "tag_name": "体验中",
            "group_id": "group_fixture_lifecycle",
            "group_name": "客户阶段",
            "order": 2,
            "status": "active",
            "source": "local_contract_probe",
            "updated_at": synced_at,
            "synced_at": synced_at,
        },
    ]
    return groups, tags


@dataclass(frozen=True)
class TagCatalog:
    groups: list[Json]
    tags: list[Json]
    source_status: str
    read_model_status: str

    def to_payload(self) -> Json:
        tags_by_group: dict[str, list[Json]] = {}
        for tag in self.tags:
            tags_by_group.setdefault(_text(tag.get("group_id")) or _text(tag.get("group_name")), []).append(dict(tag))

        groups: list[Json] = []
        known_groups = set()
        for group in self.groups:
            group_key = _text(group.get("group_key")) or _text(group.get("group_id")) or f"group-name:{_text(group.get('group_name'))}"
            group_tags = tags_by_group.get(_text(group.get("group_id")) or _text(group.get("group_name")), [])
            groups.append(
                {
                    **dict(group),
                    "tag_group_id": _text(group.get("tag_group_id")) or _text(group.get("group_id")),
                    "group_key": group_key,
                    "missing_group_id": not bool(_text(group.get("group_id"))),
                    "tag_count": len(group_tags),
                    "tags": group_tags,
                }
            )
            known_groups.add(_text(group.get("group_id")) or _text(group.get("group_name")))

        for group_key, group_tags in sorted(tags_by_group.items()):
            if group_key in known_groups:
                continue
            group_name = _text(group_tags[0].get("group_name")) or "未分组"
            groups.append(
                {
                    "group_id": _text(group_tags[0].get("group_id")),
                    "tag_group_id": _text(group_tags[0].get("tag_group_id")) or _text(group_tags[0].get("group_id")),
                    "group_key": group_key or f"group-name:{group_name}",
                    "group_name": group_name,
                    "missing_group_id": not bool(_text(group_tags[0].get("group_id"))),
                    "tag_count": len(group_tags),
                    "source": group_tags[0].get("source") or self.source_status,
                    "updated_at": group_tags[0].get("updated_at") or "",
                    "synced_at": group_tags[0].get("synced_at") or "",
                    "tags": group_tags,
                }
            )

        return {
            "ok": True,
            "items": list(self.tags),
            "tags": list(self.tags),
            "groups": groups,
            "count": len(self.tags),
            "total_tags": len(self.tags),
            "tag_limit": 1000,
            "synced_at": next((_text(tag.get("synced_at")) for tag in self.tags if _text(tag.get("synced_at"))), ""),
            "source_status": self.source_status,
            "read_model_status": self.read_model_status,
            "route_owner": "ai_crm_next",
            "fallback_used": False,
            "real_external_call_executed": False,
            "sync_executed": False,
            "fixture_used": self.source_status == "local_contract_probe",
        }


class TagCatalogRepository:
    source_status = "production_postgres_tag_catalog"
    read_model_status = "primary"

    def list_catalog(self) -> TagCatalog: ...


class LocalContractTagCatalogRepository(TagCatalogRepository):
    source_status = "local_contract_probe"
    read_model_status = "fixture"

    def list_catalog(self) -> TagCatalog:
        groups, tags = _fixture_rows()
        return TagCatalog(groups=groups, tags=tags, source_status=self.source_status, read_model_status=self.read_model_status)


class PostgresTagCatalogRepository(TagCatalogRepository):
    def __init__(self, engine: Engine | None = None) -> None:
        self._engine = engine or get_engine()

    def list_catalog(self) -> TagCatalog:
        try:
            with self._engine.connect() as connection:
                group_rows = connection.execute(
                    text(
                        """
                        SELECT group_id, group_key, group_name, tag_count, synced_at, updated_at
                        FROM wecom_corp_tag_groups
                        ORDER BY group_name ASC, group_id ASC
                        """
                    )
                ).mappings().all()
                tag_rows = connection.execute(
                    text(
                        """
                        SELECT tag_id, tag_name, group_id, group_name, order_index, deleted_at, synced_at, updated_at
                        FROM wecom_corp_tags
                        WHERE deleted_at IS NULL
                        ORDER BY group_name ASC, order_index ASC, tag_name ASC, tag_id ASC
                        """
                    )
                ).mappings().all()
        except Exception as exc:
            raise TagCatalogUnavailable(str(exc)) from exc

        groups = [
            {
                "group_id": _text(row.get("group_id")),
                "tag_group_id": _text(row.get("group_id")),
                "group_key": _text(row.get("group_key")) or _text(row.get("group_id")),
                "group_name": _text(row.get("group_name")),
                "tag_count": int(row.get("tag_count") or 0),
                "source": self.source_status,
                "updated_at": _iso(row.get("updated_at")),
                "synced_at": _iso(row.get("synced_at")),
            }
            for row in group_rows
        ]
        tags = [
            {
                "tag_id": _text(row.get("tag_id")),
                "tag_name": _text(row.get("tag_name")),
                "tag_group_id": _text(row.get("group_id")),
                "group_id": _text(row.get("group_id")),
                "group_name": _text(row.get("group_name")),
                "order": int(row.get("order_index") or 0),
                "status": "active",
                "source": self.source_status,
                "updated_at": _iso(row.get("updated_at")),
                "synced_at": _iso(row.get("synced_at")),
            }
            for row in tag_rows
        ]
        return TagCatalog(groups=groups, tags=tags, source_status=self.source_status, read_model_status=self.read_model_status)


def build_tag_catalog_repository() -> TagCatalogRepository:
    if database_mode() == "postgres":
        return PostgresTagCatalogRepository()
    if production_environment():
        raise TagCatalogUnavailable("production tag catalog projection is unavailable without PostgreSQL")
    return LocalContractTagCatalogRepository()
