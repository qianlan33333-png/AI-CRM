from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from aicrm_next.integration_ports import build_wecom_tag_live_gateway
from aicrm_next.platform.platform_foundation.job_runs import FinishJobRunRequest, StartJobRunRequest, build_job_run_ledger_port
from aicrm_next.platform.shared.db_session import get_engine
from aicrm_next.platform.shared.runtime import fixture_mode

from .read_model import _fixture_rows


Json = dict[str, Any]


class WeComTagSyncError(RuntimeError):
    pass


class WeComTagSyncRepository(Protocol):
    source_status: str

    def refresh_catalog(self, *, groups: list[Json], tags: list[Json], synced_at: str, operator: str, raw_response: Json) -> Json: ...


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _normalize_remote_catalog(payload: Json, *, synced_at: str) -> tuple[list[Json], list[Json]]:
    groups: list[Json] = []
    tags: list[Json] = []
    for raw_group in list((payload or {}).get("tag_group") or []):
        group = dict(raw_group or {})
        group_id = _text(group.get("group_id") or group.get("id"))
        group_name = _text(group.get("group_name") or group.get("name")) or "未命名标签组"
        group_key = group_id or f"group-name:{group_name}"
        raw_tags = list(group.get("tag") or [])
        groups.append(
            {
                "group_id": group_id,
                "group_name": group_name,
                "group_key": group_key,
                "tag_count": len(raw_tags),
                "raw_payload": group,
                "synced_at": synced_at,
            }
        )
        for index, raw_tag in enumerate(raw_tags):
            tag = dict(raw_tag or {})
            tag_id = _text(tag.get("id") or tag.get("tag_id"))
            tag_name = _text(tag.get("name") or tag.get("tag_name")) or "未命名标签"
            if not tag_id and not tag_name:
                continue
            try:
                order_index = int(tag.get("order") if tag.get("order") not in (None, "") else tag.get("order_index") or index)
            except (TypeError, ValueError):
                order_index = index
            tags.append(
                {
                    "tag_id": tag_id,
                    "tag_name": tag_name,
                    "group_id": group_id,
                    "group_name": group_name,
                    "order_index": order_index,
                    "raw_payload": tag,
                    "synced_at": synced_at,
                }
            )
    return groups, tags


@dataclass
class PostgresWeComTagSyncRepository:
    engine: Engine | None = None

    source_status: str = "production_postgres_tag_catalog"

    def _engine(self) -> Engine:
        if self.engine is None:
            self.engine = get_engine()
        return self.engine

    def refresh_catalog(self, *, groups: list[Json], tags: list[Json], synced_at: str, operator: str, raw_response: Json) -> Json:
        with self._engine().begin() as connection:
            job_run_ledger = build_job_run_ledger_port()
            run_id = job_run_ledger.start_sqlalchemy(
                connection,
                request=StartJobRunRequest(
                    start_time=synced_at,
                    owner_userid=operator,
                    raw_response=raw_response,
                ),
            )

            upserted_groups = 0
            for group in groups:
                group_id = _text(group.get("group_id"))
                if not group_id:
                    continue
                connection.execute(
                    text(
                        """
                        INSERT INTO wecom_corp_tag_groups (
                            group_id, group_name, group_key, tag_count, raw_payload, synced_at, updated_at
                        )
                        VALUES (
                            :group_id, :group_name, :group_key, :tag_count,
                            CAST(:raw_payload AS jsonb), CAST(:synced_at AS timestamptz), CURRENT_TIMESTAMP
                        )
                        ON CONFLICT (group_id) DO UPDATE SET
                            group_name = EXCLUDED.group_name,
                            group_key = EXCLUDED.group_key,
                            tag_count = EXCLUDED.tag_count,
                            raw_payload = EXCLUDED.raw_payload,
                            synced_at = EXCLUDED.synced_at,
                            updated_at = CURRENT_TIMESTAMP
                        """
                    ),
                    {
                        "group_id": group_id,
                        "group_name": _text(group.get("group_name")),
                        "group_key": _text(group.get("group_key")) or group_id,
                        "tag_count": int(group.get("tag_count") or 0),
                        "raw_payload": _json(group.get("raw_payload")),
                        "synced_at": synced_at,
                    },
                )
                upserted_groups += 1

            upserted_tags = 0
            seen_tag_ids: list[str] = []
            for tag in tags:
                tag_id = _text(tag.get("tag_id"))
                if not tag_id:
                    continue
                seen_tag_ids.append(tag_id)
                connection.execute(
                    text(
                        """
                        INSERT INTO wecom_corp_tags (
                            tag_id, tag_name, group_id, group_name, order_index,
                            deleted_at, raw_payload, synced_at, updated_at
                        )
                        VALUES (
                            :tag_id, :tag_name, :group_id, :group_name, :order_index,
                            NULL, CAST(:raw_payload AS jsonb), CAST(:synced_at AS timestamptz), CURRENT_TIMESTAMP
                        )
                        ON CONFLICT (tag_id) DO UPDATE SET
                            tag_name = EXCLUDED.tag_name,
                            group_id = EXCLUDED.group_id,
                            group_name = EXCLUDED.group_name,
                            order_index = EXCLUDED.order_index,
                            deleted_at = NULL,
                            raw_payload = EXCLUDED.raw_payload,
                            synced_at = EXCLUDED.synced_at,
                            updated_at = CURRENT_TIMESTAMP
                        """
                    ),
                    {
                        "tag_id": tag_id,
                        "tag_name": _text(tag.get("tag_name")),
                        "group_id": _text(tag.get("group_id")),
                        "group_name": _text(tag.get("group_name")),
                        "order_index": int(tag.get("order_index") or 0),
                        "raw_payload": _json(tag.get("raw_payload")),
                        "synced_at": synced_at,
                    },
                )
                upserted_tags += 1

            marked_deleted = 0
            if seen_tag_ids:
                marked_deleted = int(
                    connection.execute(
                        text(
                            """
                            UPDATE wecom_corp_tags
                            SET deleted_at = COALESCE(deleted_at, CAST(:synced_at AS timestamptz)),
                                synced_at = CAST(:synced_at AS timestamptz),
                                updated_at = CURRENT_TIMESTAMP
                            WHERE deleted_at IS NULL
                              AND tag_id NOT IN :seen_tag_ids
                            """
                        ).bindparams(bindparam("seen_tag_ids", expanding=True)),
                        {"synced_at": synced_at, "seen_tag_ids": tuple(seen_tag_ids)},
                    ).rowcount
                    or 0
                )
            else:
                marked_deleted = int(
                    connection.execute(
                        text(
                            """
                            UPDATE wecom_corp_tags
                            SET deleted_at = COALESCE(deleted_at, CAST(:synced_at AS timestamptz)),
                                synced_at = CAST(:synced_at AS timestamptz),
                                updated_at = CURRENT_TIMESTAMP
                            WHERE deleted_at IS NULL
                            """
                        ),
                        {"synced_at": synced_at},
                    ).rowcount
                    or 0
                )

            result = {
                "sync_run_id": int(run_id or 0),
                "upserted_groups": upserted_groups,
                "upserted_tags": upserted_tags,
                "marked_deleted_tags": marked_deleted,
            }
            job_run_ledger.finish_sqlalchemy(
                connection,
                run_id,
                request=FinishJobRunRequest(
                    status="success",
                    end_time=synced_at,
                    fetched_count=len(tags),
                    inserted_count=upserted_tags,
                    raw_response={"ok": True, **result},
                ),
            )
            return result


class LocalContractWeComTagSyncRepository:
    source_status = "local_contract_probe"

    def refresh_catalog(self, *, groups: list[Json], tags: list[Json], synced_at: str, operator: str, raw_response: Json) -> Json:
        return {
            "sync_run_id": 0,
            "upserted_groups": len(groups),
            "upserted_tags": len(tags),
            "marked_deleted_tags": 0,
        }


def build_wecom_tag_sync_repository() -> WeComTagSyncRepository:
    if fixture_mode():
        return LocalContractWeComTagSyncRepository()
    return PostgresWeComTagSyncRepository()


def execute_wecom_tag_catalog_sync(*, operator: str = "", gateway: Any | None = None, repository: WeComTagSyncRepository | None = None) -> Json:
    synced_at = _timestamp()
    repo = repository or build_wecom_tag_sync_repository()
    try:
        if fixture_mode() and gateway is None:
            fixture_groups, fixture_tags = _fixture_rows()
            groups = [
                {
                    "group_id": _text(group.get("group_id")),
                    "group_name": _text(group.get("group_name")),
                    "group_key": _text(group.get("group_key")) or _text(group.get("group_id")),
                    "tag_count": int(group.get("tag_count") or 0),
                    "raw_payload": dict(group),
                    "synced_at": synced_at,
                }
                for group in fixture_groups
            ]
            tags = [
                {
                    "tag_id": _text(tag.get("tag_id")),
                    "tag_name": _text(tag.get("tag_name")),
                    "group_id": _text(tag.get("group_id")),
                    "group_name": _text(tag.get("group_name")),
                    "order_index": int(tag.get("order") or 0),
                    "raw_payload": dict(tag),
                    "synced_at": synced_at,
                }
                for tag in fixture_tags
            ]
            refresh = repo.refresh_catalog(groups=groups, tags=tags, synced_at=synced_at, operator=operator, raw_response={"fixture": True})
            return {
                "ok": True,
                "source_status": repo.source_status,
                "sync_model_status": "local_contract_refreshed",
                "route_owner": "ai_crm_next",
                "fallback_used": False,
                "real_external_call_executed": False,
                "sync_executed": False,
                "fetched_groups": len(groups),
                "fetched_tags": len(tags),
                "synced_at": synced_at,
                **refresh,
            }

        live_gateway = gateway or build_wecom_tag_live_gateway()
        remote_payload = live_gateway.list_wecom_tags_live()
        groups, tags = _normalize_remote_catalog(remote_payload, synced_at=synced_at)
        refresh = repo.refresh_catalog(groups=groups, tags=tags, synced_at=synced_at, operator=operator, raw_response=remote_payload)
        return {
            "ok": True,
            "source_status": "next_live_remote_synced",
            "sync_model_status": repo.source_status,
            "route_owner": "ai_crm_next",
            "fallback_used": False,
            "real_external_call_executed": True,
            "sync_executed": True,
            "fetched_groups": len(groups),
            "fetched_tags": len(tags),
            "synced_at": synced_at,
            **refresh,
        }
    except Exception as exc:
        raise WeComTagSyncError(str(exc)) from exc
