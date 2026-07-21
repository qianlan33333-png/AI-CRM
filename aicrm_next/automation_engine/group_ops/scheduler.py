from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, time, timezone, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aicrm_next.shared.errors import ContractError

from .domain import build_node_group_message_content, clean_text, derive_node_scheduled_time
from .durable_effects_repository import (
    GroupOpsEffectGraphRepository,
    GroupOpsEffectGraphRequest,
    GroupOpsEffectMaterial,
    build_group_ops_effect_graph_repository,
    materialize_group_ops_content_dependencies,
)
from .integration_gateway import resolve_group_ops_content_package_materials
from .repo import GroupOpsRepository, build_group_ops_repository
from aicrm_next.shared.runtime import production_data_ready, raw_database_url


DEFAULT_GROUP_OPS_TIMEZONE = "Asia/Shanghai"


def _business_timezone() -> ZoneInfo:
    name = clean_text(os.getenv("AICRM_GROUP_OPS_TIMEZONE")) or DEFAULT_GROUP_OPS_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_GROUP_OPS_TIMEZONE)


def _parse_datetime(value: Any, *, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = clean_text(value).replace("Z", "+00:00")
        if not text:
            return fallback
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return fallback
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _due_at_for_group(*, plan: dict[str, Any], node: dict[str, Any], group: dict[str, Any], business_tz: ZoneInfo | None = None) -> datetime:
    tz = business_tz or _business_timezone()
    fallback_start = _parse_datetime(plan.get("created_at"), fallback=datetime.now(timezone.utc))
    group_start = _parse_datetime(group.get("created_at"), fallback=fallback_start)
    scheduled_time = derive_node_scheduled_time(node)
    if not scheduled_time:
        raise ContractError("group ops node scheduled_time must use HH:MM")
    hour, minute = [int(item) for item in scheduled_time.split(":", 1)]
    day_index = max(1, int(node.get("day_index") or 1))
    start_anchor_local = group_start.astimezone(tz)
    due_date = start_anchor_local.date() + timedelta(days=day_index - 1)
    return datetime.combine(due_date, time(hour=hour, minute=minute), tzinfo=tz)


def _minute_key(value: datetime) -> str:
    return value.replace(second=0, microsecond=0).astimezone(timezone.utc).strftime("%Y%m%dT%H%MZ")


def _stable_hash(value: Any, *, length: int = 12) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:length]


def _content_hash(payload: dict[str, Any]) -> str:
    payload_for_hash = dict(payload or {})
    payload_for_hash.pop("chat_ids", None)
    return _stable_hash(payload_for_hash, length=16)


def _ids(value: Any, *, limit: int) -> list[int]:
    values = value if isinstance(value, list) else []
    result: list[int] = []
    for item in values:
        try:
            item_id = int(item or 0)
        except (TypeError, ValueError):
            continue
        if item_id > 0 and item_id not in result:
            result.append(item_id)
        if len(result) >= limit:
            break
    return result


def _durable_content_package(
    content_package: dict[str, Any],
) -> tuple[list[dict[str, Any]], tuple[GroupOpsEffectMaterial, ...]]:
    """Resolve local metadata without crossing the WeCom provider boundary."""

    from aicrm_next.media_library.postgres_repo import PostgresMediaLibraryRepository

    repository = PostgresMediaLibraryRepository(raw_database_url())
    materials: list[GroupOpsEffectMaterial] = []
    static_attachments: list[dict[str, Any]] = []
    for item_id in _ids(content_package.get("image_library_ids"), limit=3):
        materials.append(
            GroupOpsEffectMaterial(
                material_key=f"image-library:{item_id}",
                role="image",
                file_name="",
                content_type="",
                library_kind="image",
                library_material_id=item_id,
                upload_kind="image",
            )
        )
    for item_id in _ids(content_package.get("attachment_library_ids"), limit=9):
        materials.append(
            GroupOpsEffectMaterial(
                material_key=f"attachment-library:{item_id}",
                role="file",
                file_name="",
                content_type="",
                library_kind="attachment",
                library_material_id=item_id,
                upload_kind="attachment",
            )
        )
    for item_id in _ids(content_package.get("miniprogram_library_ids"), limit=1):
        item = repository.get_item("miniprogram", str(item_id), include_data=False)
        if not item or not item.get("enabled"):
            raise ContractError(f"miniprogram_resolve_failed:id={item_id}:not_found_or_disabled")
        attachment_payload = {
            "appid": clean_text(item.get("appid")),
            "page": clean_text(item.get("pagepath") or item.get("page_path")),
            "title": clean_text(item.get("title") or item.get("name")),
        }
        if not all(attachment_payload.values()):
            raise ContractError(f"miniprogram_resolve_failed:id={item_id}:missing_required_fields")
        materials.append(
            GroupOpsEffectMaterial(
                material_key=f"miniprogram-library:{item_id}",
                role="miniprogram",
                file_name="",
                content_type="",
                attachment_payload=attachment_payload,
                library_kind="miniprogram",
                library_material_id=item_id,
                upload_kind="image",
            )
        )
    for item_id in _ids(content_package.get("group_invite_library_ids"), limit=1):
        item = repository.get_item("group_invite", str(item_id), include_data=False)
        if not item or not item.get("enabled") or not clean_text(item.get("join_url")):
            raise ContractError(f"group_invite_resolve_failed:id={item_id}:not_ready")
        link = {
            "title": clean_text(item.get("title") or item.get("name")),
            "url": clean_text(item.get("join_url")),
        }
        if clean_text(item.get("description")):
            link["desc"] = clean_text(item.get("description"))
        if clean_text(item.get("pic_url")):
            link["picurl"] = clean_text(item.get("pic_url"))
        static_attachments.append({"msgtype": "link", "link": link})
    return static_attachments, tuple(materials)


@dataclass
class GroupOpsSchedulerSummary:
    scanned_at: str
    group_ops_scanned_plans: int = 0
    group_ops_due_nodes: int = 0
    group_ops_enqueued_jobs: int = 0
    group_ops_external_effect_jobs: int = 0
    group_ops_reused_external_effect_jobs: int = 0
    group_ops_skipped_future: int = 0
    group_ops_skipped_duplicate: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanned_at": self.scanned_at,
            "group_ops_scanned_plans": self.group_ops_scanned_plans,
            "group_ops_due_nodes": self.group_ops_due_nodes,
            "group_ops_enqueued_jobs": self.group_ops_enqueued_jobs,
            "group_ops_external_effect_jobs": self.group_ops_external_effect_jobs,
            "group_ops_reused_external_effect_jobs": self.group_ops_reused_external_effect_jobs,
            "group_ops_skipped_future": self.group_ops_skipped_future,
            "group_ops_skipped_duplicate": self.group_ops_skipped_duplicate,
            "errors": self.errors,
        }


class GroupOpsDueScheduler:
    def __init__(
        self,
        *,
        repo: GroupOpsRepository | None = None,
        effect_graph_repo: GroupOpsEffectGraphRepository | None = None,
    ) -> None:
        self._repo = repo
        self._effect_graph_repo = effect_graph_repo

    def run_due(self, *, now: datetime | None = None, operator: str = "automation_ops_scheduler") -> dict[str, Any]:
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        summary = GroupOpsSchedulerSummary(scanned_at=current_time.isoformat())
        business_tz = _business_timezone()
        repo = self._repo or build_group_ops_repository()
        effect_graph_repo = self._effect_graph_repo or build_group_ops_effect_graph_repository()
        if repo is None:
            summary.errors.append({"scope": "group_ops", "error": "group ops repository unavailable"})
            return summary.as_dict()
        plans, _total = repo.list_plans({"plan_type": "standard", "status": "active", "limit": 500, "offset": 0})
        for plan in plans:
            summary.group_ops_scanned_plans += 1
            plan_id = int(plan.get("id") or 0)
            try:
                groups = [
                    group
                    for group in repo.list_bound_groups(plan_id)
                    if clean_text(group.get("status") or "active") == "active" and clean_text(group.get("chat_id"))
                ]
                if not groups:
                    continue
                nodes = [node for node in repo.list_nodes(plan_id) if clean_text(node.get("status") or "active") == "active"]
                for node in nodes:
                    node_id = int(node.get("id") or 0)
                    try:
                        self._schedule_node(
                            summary=summary,
                            plan=plan,
                            node=node,
                            groups=groups,
                            now=current_time,
                            operator=operator,
                            business_tz=business_tz,
                            effect_graph_repo=effect_graph_repo,
                        )
                    except Exception as exc:
                        summary.errors.append(
                            {
                                "scope": "group_ops_node",
                                "plan_id": plan_id,
                                "node_id": node_id,
                                "error": str(exc),
                            }
                        )
            except Exception as exc:
                summary.errors.append({"scope": "group_ops_plan", "plan_id": plan_id, "error": str(exc)})
        return summary.as_dict()

    def _schedule_node(
        self,
        *,
        summary: GroupOpsSchedulerSummary,
        plan: dict[str, Any],
        node: dict[str, Any],
        groups: list[dict[str, Any]],
        now: datetime,
        operator: str,
        business_tz: ZoneInfo,
        effect_graph_repo: GroupOpsEffectGraphRepository,
    ) -> None:
        content_package = node.get("content_package_json") if isinstance(node.get("content_package_json"), dict) else {}
        materials: tuple[GroupOpsEffectMaterial, ...] = ()
        if production_data_ready():
            resolved_attachments, materials = _durable_content_package(content_package)
            resolved_image_media_ids: list[str] = []
        else:
            resolved_attachments, resolved_image_media_ids = resolve_group_ops_content_package_materials(content_package)
        content = build_node_group_message_content(
            node=node,
            sender=clean_text(plan.get("owner_userid")),
            resolved_attachments=resolved_attachments,
            resolved_image_media_ids=resolved_image_media_ids,
            allow_deferred_materials=bool(materials),
        )
        content = materialize_group_ops_content_dependencies(content, materials)
        base_payload = dict(content)
        base_payload.setdefault("attachments", [])
        base_payload["channel"] = "wecom_customer_group"
        base_payload["sender"] = clean_text(plan.get("owner_userid"))
        base_payload["plan_id"] = int(plan.get("id") or 0)
        base_payload["node_id"] = int(node.get("id") or 0)
        content_hash = _content_hash(base_payload)
        due_groups: dict[tuple[str, str, str], dict[str, Any]] = {}
        revision_groups: list[dict[str, str]] = []
        for group in groups:
            due_at = _due_at_for_group(plan=plan, node=node, group=group, business_tz=business_tz)
            revision_groups.append(
                {
                    "chat_id": clean_text(group.get("chat_id")),
                    "due_at": due_at.isoformat(timespec="minutes"),
                    "binding_created_at": clean_text(group.get("created_at")),
                    "binding_updated_at": clean_text(group.get("updated_at")),
                }
            )
            if due_at.astimezone(timezone.utc) > now.astimezone(timezone.utc):
                summary.group_ops_skipped_future += 1
            else:
                summary.group_ops_due_nodes += 1
            key = (
                due_at.isoformat(timespec="minutes"),
                clean_text(plan.get("owner_userid")),
                content_hash,
            )
            bucket = due_groups.setdefault(key, {"due_at": due_at, "chat_ids": []})
            bucket["chat_ids"].append(clean_text(group.get("chat_id")))

        revision_fingerprint = _stable_hash(
            {
                "plan_id": int(plan.get("id") or 0),
                "plan_updated_at": clean_text(plan.get("updated_at")),
                "node_id": int(node.get("id") or 0),
                "node_updated_at": clean_text(node.get("updated_at")),
                "content_hash": content_hash,
                "groups": sorted(revision_groups, key=lambda item: (item["due_at"], item["chat_id"])),
            },
            length=20,
        )

        for bucket in due_groups.values():
            chat_ids = sorted(dict.fromkeys(bucket["chat_ids"]))
            due_at = bucket["due_at"]
            chat_hash = _stable_hash(chat_ids)
            source_id = f"{int(plan['id'])}:node:{int(node['id'])}:due:{_minute_key(due_at)}:groups:{chat_hash}"
            scheduled_at = due_at.isoformat(timespec="seconds")
            idempotency_key = f"group_ops:{source_id}:{scheduled_at}:revision:{revision_fingerprint}"
            content_payload = dict(base_payload)
            content_payload["chat_ids"] = chat_ids
            planned = effect_graph_repo.plan(
                GroupOpsEffectGraphRequest(
                    idempotency_key=idempotency_key,
                    source_kind="plan_node",
                    plan_id=int(plan["id"]),
                    node_id=int(node.get("id") or 0),
                    chat_ids=chat_ids,
                    content_summary=(content.get("text") or {}).get("content", "") or clean_text(node.get("action_title")),
                    content_payload=content_payload,
                    actor_id=clean_text(operator) or "automation_ops_scheduler",
                    owner_userid=clean_text(plan.get("owner_userid")),
                    source_module="automation_engine.group_ops.scheduler",
                    source_route="group_ops_due_scheduler",
                    source_command_id=source_id,
                    scheduled_at=scheduled_at,
                    version_fingerprint=revision_fingerprint,
                    materials=materials,
                )
            )
            if planned and int(planned.get("final_effect_job_id") or 0):
                if planned.get("duplicate") is True:
                    summary.group_ops_reused_external_effect_jobs += 1
                    summary.group_ops_skipped_duplicate += 1
                else:
                    summary.group_ops_external_effect_jobs += 1


def run_group_ops_due_scheduler(
    *,
    repo: GroupOpsRepository | None = None,
    now: datetime | None = None,
    operator: str = "automation_ops_scheduler",
) -> dict[str, Any]:
    return GroupOpsDueScheduler(
        repo=repo,
    ).run_due(now=now, operator=operator)


def materialize_group_ops_plan_node(
    *,
    repo: GroupOpsRepository,
    plan: dict[str, Any],
    node: dict[str, Any],
    operator: str,
    now: datetime | None = None,
    effect_graph_repo: GroupOpsEffectGraphRepository | None = None,
) -> dict[str, Any]:
    """Pre-materialize every scheduled unit for one edited plan node."""

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    summary = GroupOpsSchedulerSummary(scanned_at=current_time.isoformat())
    groups = [
        group
        for group in repo.list_bound_groups(int(plan.get("id") or 0))
        if clean_text(group.get("status") or "active") == "active" and clean_text(group.get("chat_id"))
    ]
    if not groups or clean_text(node.get("status") or "active") != "active":
        return summary.as_dict()
    GroupOpsDueScheduler(repo=repo, effect_graph_repo=effect_graph_repo)._schedule_node(
        summary=summary,
        plan=plan,
        node=node,
        groups=groups,
        now=current_time,
        operator=operator,
        business_tz=_business_timezone(),
        effect_graph_repo=effect_graph_repo or build_group_ops_effect_graph_repository(),
    )
    return summary.as_dict()


__all__ = [
    "GroupOpsDueScheduler",
    "materialize_group_ops_plan_node",
    "run_group_ops_due_scheduler",
]
